"""Fair-share scheduler for VoxHumana's single processing worker.

The server still processes one job at a time; this module only decides
*which* waiting job goes next. Plain first-come-first-served let one person
who queued days of audio block everyone behind them (a classroom, say), so
instead each pick goes like this:

  1. Tier: if any waiting job was submitted with an active class code, only
     those jobs are considered. Otherwise, everything is.
  2. Aging: if some submitter hasn't had a turn in STARVATION_LIMIT, the
     one who has waited longest goes next. Measured per submitter, from
     their last turn -- not per job from submission -- so one aged job from a
     heavy user jumps ahead, not their whole backlog at once.
  3. Fair share: otherwise, each submitter is scored as
     (their recent usage) + (cost of their cheapest waiting job), and the
     lowest score wins. One rule covers both goals: light users beat heavy
     ones, and between two newcomers the shorter file goes first.
  4. Within the chosen submitter, their cheapest job goes first.

"Usage" is the estimated cost of jobs a submitter has started, decaying with
a USAGE_HALF_LIFE half-life, so yesterday's heavy use barely counts today.

No FastAPI imports here, so this can be exercised on its own.
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Optional

USAGE_HALF_LIFE = 12 * 3600      # seconds for a submitter's usage to halve
STARVATION_LIMIT = 24 * 3600     # max time a submitter goes without a turn
ORDER_CACHE_SECONDS = 30         # predicted order is time-dependent; refresh this often

# Estimated processing time, in seconds, on the production server (2 CPU
# cores, no GPU). Each step is a fixed startup cost plus a cost per second of
# audio, fitted to data/from_server/summary.jsonl (559 jobs through
# 2026-10-02). The scheduler orders jobs by these estimates and the admin
# page shows them, so refit them when the server or the tools change.
WHISPER_TIME = {        # model: (fixed seconds, seconds per audio second)
    "small":  (0,   0.81),   # n=15, but only 2–3 minute files so far
    "turbo":  (270, 2.66),   # n=64, files up to 45 minutes
    "medium": (150, 2.0),    # no data yet — a guess between small and turbo
    "large":  (400, 6.0),    # no usable data — a guess; needs more RAM than the server has free
}
ALIGNMENT_TIME = (80, 0.018)     # MFA: mostly startup cost (n=297)
FORMANTS_TIME = (1, 0.26)        # new-fave (n=258); FAVE-extract has no data, assumed similar
STEP_STARTUP_SECONDS = 5         # each heavy step starts a fresh process (web/killable.py)
DEFAULT_AUDIO_SECONDS = 1800     # used when the audio duration can't be read


def estimate_cost(
    audio_seconds: Optional[float],
    whisper_model: str,
    run_transcription: bool,
    run_alignment: bool,
    run_formants: bool,
) -> float:
    """Estimated processing time of a job on the production server, in seconds."""
    duration = audio_seconds if audio_seconds and audio_seconds > 0 else DEFAULT_AUDIO_SECONDS
    steps = []
    if run_transcription:
        steps.append(WHISPER_TIME.get(whisper_model, WHISPER_TIME["turbo"]))
    if run_alignment:
        steps.append(ALIGNMENT_TIME)
    if run_formants:
        steps.append(FORMANTS_TIME)
    return sum(STEP_STARTUP_SECONDS + fixed + per_second * duration
               for fixed, per_second in steps)


@dataclass
class QueuedJob:
    job_id: str
    submitter: str
    cost: float
    priority_label: Optional[str] = None   # class-code label; None = normal tier
    enqueued_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None


def _decayed_usage(entry: Optional[tuple[float, float]], now: float) -> float:
    if entry is None:
        return 0.0
    seconds, as_of = entry
    return seconds * 0.5 ** (max(0.0, now - as_of) / USAGE_HALF_LIFE)


def _choose(
    pending: list[QueuedJob],
    usage: dict[str, tuple[float, float]],
    last_turn: dict[str, float],
    now: float,
) -> QueuedJob:
    """Pick the next job from a non-empty pending list (see module docstring)."""
    candidates = [j for j in pending if j.priority_label] or pending

    by_submitter: dict[str, list[QueuedJob]] = {}
    for job in candidates:
        by_submitter.setdefault(job.submitter, []).append(job)

    def cheapest(jobs: list[QueuedJob]) -> QueuedJob:
        return min(jobs, key=lambda j: (j.cost, j.enqueued_at))

    def waiting_since(submitter: str) -> float:
        earliest = min(j.enqueued_at for j in by_submitter[submitter])
        return max(last_turn.get(submitter, earliest), earliest)

    starved = [s for s in by_submitter if now - waiting_since(s) >= STARVATION_LIMIT]
    if starved:
        chosen = min(starved, key=waiting_since)
    else:
        chosen = min(
            by_submitter,
            key=lambda s: (
                _decayed_usage(usage.get(s), now) + cheapest(by_submitter[s]).cost,
                waiting_since(s),
            ),
        )
    return cheapest(by_submitter[chosen])


class FairScheduler:
    """Thread-safe queue state: waiting jobs, running jobs, per-submitter usage."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, QueuedJob] = {}
        self._running: dict[str, QueuedJob] = {}
        self._usage: dict[str, tuple[float, float]] = {}   # submitter -> (seconds, as_of)
        self._last_turn: dict[str, float] = {}             # submitter -> last start time
        self._version = 0
        self._order_cache: Optional[tuple[int, float, list[str]]] = None

    # ── Mutations ──────────────────────────────────────────────────────────

    def add(self, job: QueuedJob) -> None:
        with self._lock:
            self._pending[job.job_id] = job
            self._version += 1

    def pop_next(self, now: Optional[float] = None) -> Optional[QueuedJob]:
        """Choose the next job, mark it running, and charge its submitter."""
        now = time.time() if now is None else now
        with self._lock:
            if not self._pending:
                return None
            job = _choose(list(self._pending.values()), self._usage, self._last_turn, now)
            del self._pending[job.job_id]
            job.started_at = now
            self._running[job.job_id] = job
            self._usage[job.submitter] = (
                _decayed_usage(self._usage.get(job.submitter), now) + job.cost, now
            )
            self._last_turn[job.submitter] = now
            self._prune(now)
            self._version += 1
            return job

    def finish(self, job_id: str) -> None:
        with self._lock:
            self._running.pop(job_id, None)
            self._version += 1

    def remove(self, job_id: str) -> bool:
        """Drop a waiting job (e.g. a future cancel). Returns True if it was waiting."""
        with self._lock:
            removed = self._pending.pop(job_id, None) is not None
            if removed:
                self._version += 1
            return removed

    def _prune(self, now: float) -> None:
        active = {j.submitter for j in self._pending.values()}
        active |= {j.submitter for j in self._running.values()}
        for s in list(self._usage):
            if s not in active and _decayed_usage(self._usage[s], now) < 1.0:
                del self._usage[s]
        for s in list(self._last_turn):
            if s not in active and now - self._last_turn[s] > STARVATION_LIMIT:
                del self._last_turn[s]

    # ── Queries ────────────────────────────────────────────────────────────

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def running_count(self) -> int:
        with self._lock:
            return len(self._running)

    def submitter_count(self) -> int:
        """Distinct submitters with a job waiting or running."""
        with self._lock:
            return len(
                {j.submitter for j in self._pending.values()}
                | {j.submitter for j in self._running.values()}
            )

    def predicted_order(self, now: Optional[float] = None) -> list[str]:
        """Waiting job IDs in the order they'd start if nothing else arrived.

        Simulates successive picks on copies of the state. Cached until the
        queue changes, or for ORDER_CACHE_SECONDS (decay and aging make the
        order drift with time), so frequent status polling stays cheap.
        """
        now = time.time() if now is None else now
        with self._lock:
            cache = self._order_cache
            if cache and cache[0] == self._version and now - cache[1] < ORDER_CACHE_SECONDS:
                return list(cache[2])
            pending = list(self._pending.values())
            usage = dict(self._usage)
            last_turn = dict(self._last_turn)
            order: list[str] = []
            while pending:
                job = _choose(pending, usage, last_turn, now)
                pending.remove(job)
                order.append(job.job_id)
                usage[job.submitter] = (_decayed_usage(usage.get(job.submitter), now) + job.cost, now)
                last_turn[job.submitter] = now
            self._order_cache = (self._version, now, order)
            return list(order)

    def snapshot(self, now: Optional[float] = None) -> dict:
        """Running jobs plus waiting jobs in predicted order (for the admin page)."""
        order = self.predicted_order(now)
        with self._lock:
            running = list(self._running.values())
            waiting = [self._pending[j] for j in order if j in self._pending]
        return {"running": running, "waiting": waiting}
