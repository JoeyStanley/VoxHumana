"""VoxHumana web server — FastAPI app that wraps the processing pipeline."""

import asyncio
import hashlib
import importlib.metadata
import io
import json
import multiprocessing
import os
import re
import shutil
import subprocess
import random
import secrets
import tempfile
import tomllib
import zipfile
import traceback
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException, Header
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import uuid

import librosa
import tgt

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.transcribe_with_whisper import transcribe
from pipeline.convert_whisper_to_textgrid import convert_whisper_to_textgrid
from pipeline.generate_transcript import generate_transcript
from pipeline.align_with_mfa import align_with_mfa, merge_or_validate_pronunciations
from pipeline.extract_with_newfave import extract_with_newfave, LANGUAGE_DEFAULTS, PRELIQUID_RECODE_RULES
from pipeline.extract_with_fave import extract_with_fave, get_fave_version
from pipeline.combine_textgrids import combine_textgrids
from pipeline.tier_selection import (
    list_tier_names,
    guess_tier_roles,
    extract_word_phone_tiers,
    guess_utterance_tier,
    extract_utterance_tier,
)
from web.scheduler import FairScheduler, QueuedJob, estimate_stage_times
from web.killable import StepProcess, StepKilled
from web.class_codes import ClassCodeStore, code_status
from pipeline.languages import (
    MFA_DICTIONARY_DOCS_URL,
    MFA_G2P_MODEL_BY_DICTIONARY,
    NEWFAVE_LANGUAGE_PRESETS,
    SUPPORTED_MFA_ACOUSTIC_MODELS,
    SUPPORTED_MFA_DICTIONARIES,
)

# Strip any trailing slash so ROOT_PATH + "/api/..." is always well-formed.
# Set VXH_ROOT_PATH="" (or omit it) for a root-path deployment.
# Set VXH_ROOT_PATH="/VoxHumana" when deployed under a sub-path.
ROOT_PATH = os.environ.get("VXH_ROOT_PATH", "").rstrip("/")

_INDEX_HTML = Path(__file__).parent / "static" / "index.html"
_ADMIN_HTML = Path(__file__).parent / "static" / "admin.html"
_PYPROJECT_TOML = Path(__file__).parent.parent / "pyproject.toml"

@asynccontextmanager
async def _lifespan(app):
    yield
    # Shutting down (e.g. a deploy restart): kill the running job's step so
    # the process can exit promptly instead of waiting out a long Whisper
    # run, and don't start any more queued jobs on the way out.
    global _shutting_down
    _shutting_down = True
    for step in list(_running_steps.values()):
        step.kill()


app = FastAPI(title="VoxHumana", lifespan=_lifespan)


def _get_app_version() -> str:
    """Return VoxHumana's own version, read straight from pyproject.toml.

    Installed package metadata (importlib.metadata) can go stale here since
    the editable install isn't reinstalled on every bump-my-version bump, so
    read the source of truth directly instead.
    """
    with _PYPROJECT_TOML.open("rb") as f:
        return tomllib.load(f)["project"]["version"]


APP_VERSION = _get_app_version()

# The production server has 2 CPU cores and no GPU, where Small transcribes in
# about 0.8x the recording's length versus about 2.7x (plus ~4.5 min to load)
# for Turbo — so Small is the web default. The CLI keeps its own default.
DEFAULT_WHISPER_MODEL = "small"

MAX_UPLOAD_BYTES = 1024 * 1024 * 1024  # 1 GB
MAX_OOV_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB — a plain-text pronunciation dictionary, not audio
_OOV_DICT_EXTENSIONS = {".txt", ".dict"}
JOB_RETENTION_HOURS = 72

# NEWFAVE_LANGUAGE_PRESETS, SUPPORTED_MFA_ACOUSTIC_MODELS, and
# SUPPORTED_MFA_DICTIONARIES now live in pipeline/languages.py, shared with
# main.py, so the CLI and the web app can't drift out of sync on which
# languages are supported (see pipeline/languages.py for details).

BASE_DIR = Path(__file__).parent.parent
JOBS_DIR = BASE_DIR / "data" / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR = BASE_DIR / "data" / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# In-memory job store. Fine for a single-server deployment.
jobs: dict[str, dict] = {}

# Client IP/User-Agent per job, for abuse/bot detection in server logs only.
# Kept separate from `jobs` so it never round-trips through the public
# /api/jobs status endpoint, which returns `jobs[job_id]` verbatim.
job_client_info: dict[str, dict] = {}

# ─── Job queues ───────────────────────────────────────────────────────────────
# Each job's work is split in two: transcription (Whisper, plus turning its
# output into a TextGrid and a transcript), then alignment (MFA, formant
# extraction, and adding the utterance tier). Each half has its own
# fair-share queue (web/scheduler.py) and its own single worker thread, so
# while one job is in Whisper another can be in MFA/new-fave, and a job that
# skips Whisper goes straight to the alignment queue instead of waiting
# behind someone's long transcription.
#
# Each queued job adds one _run_next() "tick" to its queue's executor; the
# tick asks that queue for the best job *when it runs*, so execution order
# follows the scheduler rather than submission order.
#
# Cores: while Whisper is busy, the alignment worker gets 1 core and Whisper
# the rest. When the alignment queue is idle, Whisper borrows that core back
# (it re-checks before each 30-second window — see
# pipeline/transcribe_with_whisper.py). When no Whisper job is running or
# waiting, alignment steps may use every core.
#
# With fewer than 3 cores, splitting isn't worth it (Whisper needs them
# all), so each job runs start to finish in the transcription queue instead.
# VXH_PIPELINE=0/1 overrides that; VXH_CPU_CORES overrides the core count.
CPU_CORES = int(os.environ.get("VXH_CPU_CORES") or os.cpu_count() or 1)
PIPELINE = (
    os.environ["VXH_PIPELINE"] == "1" if os.environ.get("VXH_PIPELINE") in ("0", "1")
    else CPU_CORES >= 3
)
try:
    MEM_TOTAL_GB = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
except (ValueError, OSError, AttributeError):
    MEM_TOTAL_GB = None

transcribe_queue = FairScheduler()
align_queue = FairScheduler()
_QUEUES = {"transcribe": transcribe_queue, "align": align_queue}
_executors = {
    "transcribe": ThreadPoolExecutor(max_workers=1),
    "align": ThreadPoolExecutor(max_workers=1),
}

# How many threads the running Whisper step should use. It's shared memory,
# so the Whisper process sees changes mid-step (before its next window).
_whisper_threads = multiprocessing.get_context("spawn").Value("i", CPU_CORES)


@dataclass
class _JobRun:
    """What a job carries from submission, through its queue(s), to the end."""
    job_id: str
    audio_path: Path
    config: dict
    submitter: str
    priority_label: Optional[str]
    transcribe_cost: float              # estimated seconds in each queue
    align_cost: float
    step_times: list = field(default_factory=list)
    align_enqueued_at: Optional[datetime] = None


_job_runs: dict[str, _JobRun] = {}

# submitter_id from the browser: letters, digits, hyphens (a UUID in practice).
_SUBMITTER_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")

# Cancellation. A waiting job is simply dropped from the scheduler; a running
# one is stopped by killing its current step, which runs in a child process
# (web/killable.py). _cancel_requests maps job ID -> who asked ("user" or
# "admin") and is also checked between steps.
_cancel_requests: dict[str, str] = {}
_running_steps: dict[str, StepProcess] = {}
_shutting_down = False
_CANCEL_MESSAGES = {
    "user":  "You cancelled this job.",
    "admin": "This job was cancelled by the VoxHumana administrator. Email "
             "voxhumana.ling@gmail.com with any questions.",
}


# Organ stops drawn from the Salt Lake Tabernacle organ — used to generate
# memorable job IDs in the form YYMMDD_Stop1_Stop2.
_ORGAN_STOPS = [
    "Bombarde", "Bourdon", "BourdonDoux", "Celeste", "Chimes", "ChimneyFlute", "ChoralBass", 
    "Chromorne", "Clarinet", "Clarion", "CorAnglais", "ContraBourdon", "ContreTrompette", 
    "ContraGamba", "Cornopean", "Diaphone", "Diapason", "Doppelflote", "Dulciana", "Fifteenth", 
    "Flugelhorn", "Flute", "FluteCeleste", "Fourniture", "FrenchHorn", "Fugara", "Gamba", 
    "Gedeckt", "GeigenPrincipal", "Gemshorn", "Harp", "HarmonicFlute", "LieblichBourdon", 
    "Mixture", "MutedVioles", "Nachthorn", "Nazard","Oboe", "Octave", "Ophicleide", "OpenWood", 
    "Piccolo", "PleinJeu", "Prestant", "Principal", "Rauschquinte", "Rohrschalmi", "RoyalTrumpet", 
    "Salicional", "Spitzflote", "Subbass", "SuperOctave", "Tierce", "Trombone", "Trompette", 
    "Tremulant", "Trumpet", "Tuba", "Tutti", "UndaMaris", "Viole", "VioleCeleste", "Waldflote",
    "Zymbelstern",
]


def _generate_job_id() -> str:
    """Return a unique job ID in the form YYMMDD_Stop1_Stop2.

    Draws two distinct organ stops at random. The caller should check for
    collisions against the jobs dict and retry if needed (extremely rare).
    """
    from datetime import date
    datestamp = date.today().strftime("%y%m%d")
    stop1, stop2 = random.sample(_ORGAN_STOPS, 2)
    return f"{datestamp}_{stop1}_{stop2}"


# Class codes give a class's jobs queue priority during a time window. They
# are managed on the /admin page and kept in data/ (gitignored) — the repo is
# public, so they can't live in it. Generated codes look like "Gemshorn-Tuba-42".
class_codes = ClassCodeStore(BASE_DIR / "data" / "class_codes.json", _ORGAN_STOPS)


def _load_or_create_admin_token() -> str:
    """Return the /admin password, creating data/admin_token.txt on first run.

    Generated on the server rather than set in config so nothing secret ever
    goes into the (public) repo or the systemd unit. Read it once over SSH:
    `cat data/admin_token.txt`.
    """
    token_path = BASE_DIR / "data" / "admin_token.txt"
    try:
        token = token_path.read_text().strip()
        if token:
            return token
    except FileNotFoundError:
        pass
    token = secrets.token_urlsafe(32)
    fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
    print(f"VoxHumana: created admin token at {token_path}", flush=True)
    return token


ADMIN_TOKEN = _load_or_create_admin_token()


def _require_admin(authorization: Optional[str]) -> None:
    """Raise 401 unless the request carries `Authorization: Bearer <admin token>`."""
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not supplied or not secrets.compare_digest(supplied, ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid admin token.")


def _fmt_utc(iso: str) -> str:
    """'2026-10-05T19:00:00+00:00' -> 'Oct 05, 19:00 UTC' for error messages."""
    return datetime.fromisoformat(iso).astimezone(timezone.utc).strftime("%b %d, %H:%M UTC")


def _submitter_hash(submitter: str) -> str:
    """Short, stable stand-in for a submitter in logs and on the admin page."""
    return hashlib.sha256(submitter.encode()).hexdigest()[:10]


def _get_client_ip(request: Request) -> str:
    """Return the real client IP, accounting for the nginx reverse proxy.

    Behind nginx (see TODO_for_server.md §8), request.client.host is always
    127.0.0.1 — the actual client address is forwarded in X-Forwarded-For
    (may list multiple hops; the client is the first one) or X-Real-IP.
    """
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


def _sanitize_stem(filename: str) -> str:
    """Return a filesystem-safe stem from an uploaded filename, capped at 40 chars."""
    stem = Path(filename).stem
    stem = re.sub(r'[^\w\-]', '', stem)   # keep alphanumeric, underscore, hyphen
    stem = re.sub(r'_+', '_', stem).strip('_')
    return stem[:40] or "audio"


def _cleanup_intermediates(job_dir: Path, audio_path: Path) -> None:
    """Delete large files that are no longer needed once the pipeline finishes."""
    audio_path.unlink(missing_ok=True)
    for dirname in ("mfa_corpus", "mfa_temp", "mfa_oov"):
        d = job_dir / dirname
        if d.exists():
            shutil.rmtree(d)


_AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aiff", ".aif"}


def _looks_like_pronunciation_dict(text: str) -> bool:
    """Cheap sanity check for an uploaded/typed OOV dictionary: not empty, and
    at least one of the first ~20 non-blank lines looks tab-separated
    ("word<TAB>phones"). Catches "wrong file entirely" (audio, CSV, a Word
    doc) before bothering to invoke MFA — real phone-set validation is left
    to merge_or_validate_pronunciations()'s PhoneMismatchError handling.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    return any("\t" in line for line in lines[:20])


def _expire_old_jobs() -> None:
    """Delete job result directories older than JOB_RETENTION_HOURS.

    Uses the directory's last-modified time as the clock — this is set when
    the final output files are written, so it accurately reflects when the
    job finished. The in-memory jobs dict is also pruned so the server doesn't
    serve stale status for expired jobs.

    Logs in data/logs/ are stored separately and are never touched here.
    """
    import time
    cutoff = time.time() - JOB_RETENTION_HOURS * 3600
    for job_dir in JOBS_DIR.iterdir():
        if not job_dir.is_dir():
            continue
        if job_dir.stat().st_mtime < cutoff:
            shutil.rmtree(job_dir, ignore_errors=True)
            jobs.pop(job_dir.name, None)
            job_client_info.pop(job_dir.name, None)


def _cleanup_orphaned_audio() -> None:
    """Delete uploaded audio files left behind by jobs that never completed.

    If the server crashed while a job was running, the audio file may have
    survived cleanup. A job directory is considered orphaned if it is not
    present in the in-memory jobs dict (meaning the server restarted since
    it was submitted) AND still contains a top-level audio file.

    Checking the jobs dict rather than looking for output folders is correct
    for Trolley mode: a Whisper-only job legitimately has no mfa_output/ or
    newfave_output/, and a user-supplied-TextGrid job has no whisper_output/.
    """
    for job_dir in JOBS_DIR.iterdir():
        if not job_dir.is_dir():
            continue
        if job_dir.name in jobs:
            continue  # job is known to this server process — leave it alone
        for f in job_dir.iterdir():
            if f.is_file() and f.suffix.lower() in _AUDIO_EXTENSIONS:
                f.unlink(missing_ok=True)
                break


def _get_mfa_version(conda_env: str) -> str:
    """Return the MFA version string from the conda env, or 'unknown'."""
    try:
        proc = subprocess.run(
            ["conda", "run", "-n", conda_env, "mfa", "version"],
            capture_output=True, text=True, timeout=15,
        )
        out = (proc.stdout + proc.stderr).strip()
        match = re.search(r'\d+\.\d+[\.\d]*', out)
        if match:
            return match.group(0)
    except Exception:
        pass
    return "unknown"


def _write_processing_log(
    job_dir: Path,
    job_id: str,
    config: dict,
    audio_filename: str,
    submitted_at: datetime,
) -> None:
    """Write processing_log.txt documenting every parameter and how to replicate offline."""
    completed_at = datetime.now(timezone.utc)
    total_s = int((completed_at - submitted_at).total_seconds())
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    if h:
        duration_str = f"{h}h {m:02d}m {s:02d}s"
    elif m:
        duration_str = f"{m}m {s:02d}s"
    else:
        duration_str = f"{s}s"

    # Tool versions
    try:
        whisper_ver = importlib.metadata.version("openai-whisper")
    except Exception:
        whisper_ver = "unknown"
    try:
        newfave_ver = importlib.metadata.version("new-fave")
    except Exception:
        newfave_ver = "unknown"
    fave_ver = get_fave_version(config.get("fave_extract"))
    conda_env = config.get("mfa", {}).get("conda_env", "aligner")
    mfa_ver = _get_mfa_version(conda_env)

    # Output file list (excludes audio, working dirs, and the log itself)
    output_files = sorted(
        str(f.relative_to(job_dir))
        for f in job_dir.rglob("*")
        if f.is_file()
        and "mfa_corpus" not in f.parts
        and "mfa_temp" not in f.parts
        and "mfa_oov" not in f.parts
        and f.name != audio_filename
        and f.name != "processing_log.txt"
    )

    stem = Path(audio_filename).stem
    w_cfg  = config.get("whisper", {})
    m_cfg  = config.get("mfa", {})
    nf_cfg = config.get("newfave", {})
    formants_engine = config.get("formants_engine", "newfave")
    formants_engine_label = "FAVE-extract" if formants_engine == "fave_extract" else "new-fave"
    steps  = config.get("steps", {})
    ran_transcription = steps.get("transcription", True)
    ran_alignment     = steps.get("alignment", True)
    ran_formants      = steps.get("formants", True)

    BAR = "=" * 72
    bar = "-" * 40

    def dflt(val, default) -> str:
        return "  [VoxHumana default]" if val == default else ""

    out: list[str] = []
    ln = out.append

    # Header
    ln(BAR)
    ln("VoxHumana Processing Log")
    ln(BAR)
    ln("")
    ln("This file documents how your audio was processed by VoxHumana.")
    ln("It lists every parameter used at each step (including pipeline defaults")
    ln("you did not set), tool version numbers, and code you can run locally to")
    ln("reproduce or extend the analysis.")
    ln("")
    ln(f"Job ID:    {job_id}")
    ln(f"VoxHumana: v{APP_VERSION}")
    ln(f"Submitted: {submitted_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    ln(f"Completed: {completed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    ln(f"Duration:  {duration_str}")
    queue_position = jobs.get(job_id, {}).get("queue_position_at_submission", 1)
    wait_seconds = jobs.get(job_id, {}).get("wait_seconds", 0.0)
    if queue_position > 1:
        ahead = queue_position - 1
        ln(f"Queue:     {ahead} other job{'s' if ahead != 1 else ''} running or waiting at "
           f"submission — waited {_fmt_duration(wait_seconds)} before processing began")
    else:
        ln("Queue:     no wait — processing began immediately")
    class_code_label = jobs.get(job_id, {}).get("class_code_label")
    if class_code_label:
        ln(f"Priority:  class code ({class_code_label})")

    # Input / output
    ln("")
    ln(bar)
    ln("INPUT FILE")
    ln(bar)
    ln(f"  {audio_filename}")
    ln("")
    ln(bar)
    ln("OUTPUT FILES")
    ln(bar)
    for f in output_files:
        ln(f"  {f}")

    # ── WHISPER ──────────────────────────────────────────────────────────────
    ln("")
    ln("")
    ln(BAR)
    if ran_transcription:
        model = w_cfg.get("model", DEFAULT_WHISPER_MODEL)
        language = w_cfg.get("language") or None
        initial_prompt = w_cfg.get("initial_prompt") or None
        copt = w_cfg.get("condition_on_previous_text", True)

        ln(f"STEP 1 — TRANSCRIPTION: OpenAI Whisper  v{whisper_ver}")
        ln(BAR)
        ln("")
        ln("Whisper is an automatic speech recognition model that converts audio to")
        ln("text with segment-level timestamps. VoxHumana saves four outputs:")
        ln("  • a .json file with full segment detail (timings, detected language)")
        ln("  • a .txt file with the plain-text transcript")
        ln("  • a Praat TextGrid (.TextGrid), created with the TextGridTools package")
        ln("      in Python, with one interval per segment")
        ln("  • a _lines.txt file, one utterance per line prefixed with its start")
        ln("      time (mm:ss), generated by a Praat script (pipeline/praat/generate_transcript.praat)")
        ln("This TextGrid is the input to MFA alignment Step 2.")
        ln("")
        ln("Parameters:")
        ln(f"  - model:                       {model}{dflt(model, DEFAULT_WHISPER_MODEL)}")
        ln(f"  - language:                    {language or '(auto-detect)'}{dflt(language, None)}")
        ln(f"  - initial_prompt:              {repr(initial_prompt) if initial_prompt else '(none)'}{dflt(initial_prompt, None)}")
        ln(f"  - condition_on_previous_text:  {copt}{dflt(copt, True)}")
        ln("")
        ln("To replicate what VoxHumana did offline in a Python script:")
        ln("")
        ln("    import whisper, json")
        ln(f"    model = whisper.load_model({model!r})")
        ln(f"    result = model.transcribe(")
        ln(f"        {audio_filename!r},")
        if language:
            ln(f"        language={language!r},")
        if initial_prompt:
            ln(f"        initial_prompt={initial_prompt!r},")
        ln(f"        condition_on_previous_text={copt},")
        ln(f"    )")
        ln(f"    with open({(stem + '.json')!r}, 'w') as f:")
        ln(f"        json.dump(result, f, indent=2)")
        ln(f"    with open({(stem + '.txt')!r}, 'w') as f:")
        ln(f"        f.write(result['text'].strip())")
    else:
        ln("STEP 1 — TRANSCRIPTION: skipped (user-supplied TextGrid)")
        ln(BAR)
        ln("")
        tier_sel = config.get("tier_selection")
        tier_sel_names = (tier_sel or {}).get("tier_names") or []

        def _tier_label(idx) -> str:
            if idx is None or idx >= len(tier_sel_names):
                return "(unknown)"
            return f"\"{tier_sel_names[idx]}\" (tier {idx + 1} of {len(tier_sel_names)})"

        if ran_alignment:
            ln("Whisper transcription was not run. A Praat utterance TextGrid was")
            ln("uploaded by the user and used as the transcript input to MFA.")
            if tier_sel:
                ln("")
                ln(f"Utterance tier selected: {_tier_label(tier_sel.get('utterance_idx'))}")
        else:
            ln("Whisper transcription was not run. A Praat MFA-format TextGrid")
            ln("(Word and Phone tiers) was uploaded by the user and used directly")
            ln(f"as the input to {formants_engine_label} formant extraction.")
            if tier_sel:
                ln("")
                ln(f"Phone tier selected: {_tier_label(tier_sel.get('phone_idx'))}")
                ln(f"Word tier selected:  {_tier_label(tier_sel.get('word_idx'))}")

    # ── MFA ──────────────────────────────────────────────────────────────────
    ln("")
    ln("")
    ln(BAR)
    if ran_alignment:
        acoustic_model = m_cfg.get("acoustic_model", "english_us_arpa")
        dictionary = m_cfg.get("dictionary", "english_us_arpa")
        fine_tune = m_cfg.get("fine_tune", False)
        num_jobs = m_cfg.get("num_jobs", 1)
        output_format = m_cfg.get("output_format", "long_textgrid")
        oov_mode = m_cfg.get("oov_mode", "guess")
        oov_merge_with_builtin = m_cfg.get("oov_merge_with_builtin", True)
        oov_dictionary_original_filename = m_cfg.get("oov_dictionary_original_filename")
        oov_custom_words_text = m_cfg.get("oov_custom_words_text")
        g2p_model = MFA_G2P_MODEL_BY_DICTIONARY.get(dictionary)
        tg_source = "user-supplied TextGrid" if not ran_transcription else "Whisper/TextGridTools-generated TextGrid"

        ln(f"STEP 2 — FORCED ALIGNMENT: Montreal Forced Aligner (MFA)  v{mfa_ver}")
        ln(BAR)
        ln("")
        ln(f"MFA takes the audio and the {tg_source} and")
        ln("produces another Praat TextGrid with word- and phone-level time-aligned")
        ln("intervals. This TextGrid is the primary input to new-fave in Step 3.")
        ln("")
        ln("Any words in the transcript not found in the pronunciation dictionary are")
        ln("listed in oovs_found.txt (included in your download if any were found).")
        ln("")
        if oov_mode == "guess":
            if g2p_model:
                ln(f"Out-of-vocabulary words: MFA's G2P model ('{g2p_model}') automatically")
                ln("generated pronunciations for these. Poor guesses can degrade alignment")
                ln("quality around those words.")
            else:
                ln("Out-of-vocabulary words: automatic guessing isn't available for this")
                ln(f"dictionary ('{dictionary}') yet, so OOV words were left unaligned.")
        elif oov_mode == "upload":
            ln(f"Out-of-vocabulary words: a custom dictionary uploaded by the user "
               f"('{oov_dictionary_original_filename}') was "
               f"{'merged with' if oov_merge_with_builtin else 'used in place of'} "
               f"the built-in '{dictionary}' dictionary for this job.")
        elif oov_mode == "type":
            ln("Out-of-vocabulary words: custom pronunciations entered by the user were")
            ln(f"merged with the built-in '{dictionary}' dictionary for this job:")
            ln("")
            for word_line in (oov_custom_words_text or "").splitlines():
                if word_line.strip():
                    ln(f"    {word_line}")
        ln("")
        ln(f"MFA dictionary reference: {MFA_DICTIONARY_DOCS_URL}")
        ln(f"(this job used the '{dictionary}' dictionary)")
        if ran_transcription:
            ln("")
            if ran_formants:
                ln("After new-fave (Step 3) has read this TextGrid, a Praat script")
                ln("(pipeline/praat/combine_textgrids.praat) reorders its tiers to")
                ln("Phone, Word and adds the Whisper utterance tier at the bottom, so")
                ln("the downloaded mfa_output TextGrid has Phone, Word, and Utterance")
                ln("tiers all in one file.")
            else:
                ln("A Praat script (pipeline/praat/combine_textgrids.praat) reorders")
                ln("this TextGrid's tiers to Phone, Word and adds the Whisper utterance")
                ln("tier at the bottom, so the downloaded mfa_output TextGrid has Phone,")
                ln("Word, and Utterance tiers all in one file.")
        ln("")
        ln("Parameters:")
        ln(f"  - acoustic_model:   {acoustic_model}{dflt(acoustic_model, 'english_us_arpa')}")
        ln(f"  - dictionary:       {dictionary}{dflt(dictionary, 'english_us_arpa')}")
        ln(f"  - fine_tune:        {fine_tune}{dflt(fine_tune, False)}")
        ln(f"  - num_jobs:         {num_jobs}{dflt(num_jobs, 1)}")
        ln(f"  - output_format:    {output_format}{dflt(output_format, 'long_textgrid')}")
        ln("")
        ln("To replicate what VoxHumana did offline in the command line:")
        ln("")
        ln(f"    # 1. Create corpus_dir/ containing:")
        ln(f"    #      {audio_filename}")
        ln(f"    #      {stem}.TextGrid   (utterance TextGrid from whisper_output/)")
        ln(f"    # 2. Run:")
        if oov_mode == "guess":
            ln(f"    mfa align corpus_dir/ \\")
            ln(f"             {dictionary} \\")
            ln(f"             {acoustic_model} \\")
            ln(f"             mfa_output/ \\")
            if fine_tune:
                ln(f"             --fine_tune \\")
            if g2p_model:
                ln(f"             --g2p_model_path {g2p_model} \\")
            ln(f"             --output_format {output_format}")
        else:
            ln(f"    # A custom dictionary was used for this job (see above) — the merged/")
            ln(f"    # replaced dictionary file itself isn't kept after the job finishes,")
            ln(f"    # so this command isn't reproducible verbatim. Substitute your own")
            ln(f"    # merged dictionary path for DICTIONARY_PATH below.")
            ln(f"    mfa align corpus_dir/ \\")
            ln(f"             DICTIONARY_PATH \\")
            ln(f"             {acoustic_model} \\")
            ln(f"             mfa_output/ \\")
            if fine_tune:
                ln(f"             --fine_tune \\")
            ln(f"             --output_format {output_format}")
    else:
        ln("STEP 2 — FORCED ALIGNMENT: skipped")
        ln(BAR)
        ln("")
        if ran_formants:
            ln("MFA alignment was not run. A user-supplied MFA TextGrid was used")
            ln(f"directly as the input to {formants_engine_label} formant extraction.")
        else:
            ln("MFA alignment was not run for this job.")

    # ── FORMANT EXTRACTION (new-fave or FAVE-extract) ───────────────────────────
    ln("")
    ln("")
    ln(BAR)
    fv_cfg = config.get("fave_extract", {}) or {}
    if ran_formants and formants_engine == "fave_extract":
        sex = fv_cfg.get("sex")
        speaker_name = fv_cfg.get("name")
        fpm = fv_cfg.get("formant_prediction_method", "mahalanobis")
        vowel_system = fv_cfg.get("vowel_system", "NorthAmerican")
        remeasurement = bool(fv_cfg.get("remeasurement", False))
        min_vowel_duration = fv_cfg.get("min_vowel_duration")
        n_formants_fave = fv_cfg.get("n_formants")

        ln(f"STEP 3 — VOWEL FORMANT EXTRACTION: FAVE-extract (legacy)  v{fave_ver}")
        ln(BAR)
        ln("")
        ln("FAVE-extract is the original FAVE/DARLA vowel-formant algorithm (Rosenfelder,")
        ln("Fruehwald, Evanini, and Yuan), kept in VoxHumana as a legacy, English-only")
        ln("alternative to new-fave for comparison/reproducibility with older FAVE- or")
        ln("DARLA-based work. It locates vowel tokens in the MFA-aligned TextGrid and,")
        ln("by default, uses a Mahalanobis-distance method to pick the best-fitting LPC")
        ln("analysis order for each vowel. One output file is written:")
        ln("")
        ln("  *.txt              — one row per vowel token (tab-delimited)")
        ln("")
        ln("Parameters:")
        ln(f"  - speaker sex:              {sex}")
        ln(f"  - speaker name:             {speaker_name or '(not given)'}")
        ln(f"  - formantPredictionMethod:  {fpm}{dflt(fpm, 'mahalanobis')}")
        ln(f"  - vowelSystem:              {vowel_system}{dflt(vowel_system, 'NorthAmerican')}")
        ln(f"  - remeasurement:            {remeasurement}{dflt(remeasurement, False)}")
        ln(f"  - minVowelDuration:         {min_vowel_duration if min_vowel_duration is not None else '(FAVE-extract default)'}")
        ln(f"  - nFormants:                {n_formants_fave if n_formants_fave is not None else '(FAVE-extract default)'}")
        ln("")
        ln("To replicate what VoxHumana did offline on the command line (requires a")
        ln("separate Python 3.10 environment with FAVE-extract installed — see")
        ln("pipeline/extract_with_fave.py for setup):")
        ln("")
        ln("    # speaker.speaker should contain:")
        ln(f"    #   --sex\n    #   {sex}")
        ln("    python -m fave.extractFormants \\")
        ln("        --mfa \\")
        ln("        --speaker speaker.speaker \\")
        ln(f"        --formantPredictionMethod {fpm} \\")
        ln(f"        --vowelSystem {vowel_system} \\")
        if remeasurement:
            ln("        --remeasurement \\")
        ln(f"        {audio_filename} \\")
        ln(f"        mfa_output/{stem}.TextGrid \\")
        ln(f"        {stem}")
    elif ran_formants:
        nf_language = nf_cfg.get("language", "en")
        lang_defaults = LANGUAGE_DEFAULTS.get(nf_language, LANGUAGE_DEFAULTS["en"])

        # Mirrors the gating in pipeline.extract_with_newfave: only meaningful
        # for English (CMU ARPABET stress-digit labels), silently ignored
        # otherwise.
        combine_preliquid = bool(nf_cfg.get("combine_preliquid", False)) and nf_language == "en"
        include_intervocalic = nf_cfg.get("include_intervocalic", True)
        recode_rules_default = PRELIQUID_RECODE_RULES if combine_preliquid else lang_defaults["recode_rules"]

        speakers = nf_cfg.get("speakers", "all")
        recode_rules = nf_cfg.get("recode_rules", recode_rules_default)
        labelset_parser = nf_cfg.get("labelset_parser", lang_defaults["labelset_parser"])
        point_heuristic = nf_cfg.get("point_heuristic", lang_defaults["point_heuristic"])
        formant_ceiling = nf_cfg.get("formant_ceiling")
        num_formants = nf_cfg.get("num_formants")
        include_overlaps = nf_cfg.get("include_overlaps", True)

        has_ft_override = formant_ceiling is not None or num_formants is not None
        ft_display = "custom YAML (see formant_ceiling / num_formants below)" if has_ft_override else "default"
        fc_str = str(formant_ceiling) if formant_ceiling is not None else "(not set — new-fave default applies)"
        nf_str = str(num_formants) if num_formants is not None else "(not set — new-fave default applies)"
        ph_display = point_heuristic if point_heuristic is not None else "default (1/3 point)"

        ln(f"STEP 3 — VOWEL FORMANT EXTRACTION: new-fave  v{newfave_ver}")
        ln(BAR)
        ln("")
        if combine_preliquid:
            ln("Before new-fave ran, a Praat script (pipeline/praat/combine_preliquid_sequences.praat)")
            ln("added a new tier, \"phones - combined - liquids\", to a copy of the aligned TextGrid")
            ln("(saved as *_preliquid.TextGrid below): a copy of the phone tier with each word-internal")
            ln("vowel immediately followed by an \"L\" or \"R\" merged into a single combined interval,")
            ln("e.g. \"UH1\"+\"L\" -> \"UHL1\" (the stress marker moves to the end). The original phone")
            ln("tier is left untouched, so both are in *_preliquid.TextGrid side by side. new-fave")
            ln("extracted formants from the new combined tier, not the original phone tier. recode_rules")
            ln("was set to a variant of cmu2labov (see pipeline/resources/en_preliquid_recode.yml) that")
            ln("gives every combined vowel+liquid label its own new Labov-style code, e.g. \"UHL1\" -> \"uL\",")
            ln("\"AOR1\" -> \"owR\" (the capitalized liquid letter marks that the liquid is included in the")
            ln("measured span -- deliberately distinct from cmu2labov's own lowercase \"owr\", which means a")
            ln("vowel measured alone but recoded for a following, separate R; reusing that code for a")
            ln("differently-measured token would make the two impossible to tell apart later). This keeps")
            ln("the whole file in one consistent Labov-style notation system rather than mixing it with raw ARPABET.")
            ln("")
        ln("new-fave locates vowel tokens in the MFA-aligned TextGrid, estimates")
        ln("formant trajectories across each vowel using FastTrack, and applies a")
        ln("point-measurement heuristic to pick a single representative F1/F2")
        if nf_language == "en":
            ln("value per token. Phonetic labels are recoded from CMU ARPABET to Labov")
            if combine_preliquid:
                ln("vowel-class notation. Six output files are written:")
            else:
                ln("vowel-class notation. Five output files are written:")
        else:
            ln("value per token. Vowels are identified with a labelset parser matching")
            ln(f"the '{nf_language}' phone set. Five output files are written:")
        ln("")
        ln("  *_points.csv       — one row per vowel token (single-point measurement)")
        ln("  *_tracks.csv       — formant trajectories (multiple time points per token)")
        ln("  *_param.csv        — DCT coefficients of the formant tracks (Hz scale)")
        ln("  *_logparam.csv     — DCT coefficients of the formant tracks (log Hz scale)")
        ln("  *_recoded.TextGrid — Praat TextGrid with recoded labels applied (see recode_rules)")
        if combine_preliquid:
            ln("  *_preliquid.TextGrid — copy of the aligned TextGrid with the \"phones - combined -")
            ln("                       liquids\" tier added, as fed into new-fave (see above)")
        if has_ft_override:
            ln("  ft_config.yml      — FastTrack parameter overrides used for this run;")
            ln("                       only needed if you want to rerun the extraction offline")
        ln("")
        ln("Parameters:")
        ln(f"  - language:           {nf_language}{dflt(nf_language, 'en')}")
        ln(f"  - speakers:           {speakers}{dflt(speakers, 'all')}")
        ln(f"  - recode_rules:       {recode_rules}{dflt(recode_rules, lang_defaults['recode_rules'])}")
        ln(f"  - labelset_parser:    {labelset_parser}{dflt(labelset_parser, lang_defaults['labelset_parser'])}")
        ln(f"  - point_heuristic:    {ph_display}{dflt(point_heuristic, lang_defaults['point_heuristic'])}")
        ln(f"  - ft_config:          {ft_display}{dflt(ft_display, 'default')}")
        ln(f"  - formant_ceiling:    {fc_str}")
        ln(f"  - num_formants:       {nf_str}")
        ln(f"  - include_overlaps:   {include_overlaps}{dflt(include_overlaps, True)}")
        ln(f"  - combine_preliquid:  {combine_preliquid}{dflt(combine_preliquid, False)}")
        if combine_preliquid:
            ln(f"  - include_intervocalic: {include_intervocalic}{dflt(include_intervocalic, True)}")
        ln("")
        ln("To replicate what VoxHumana did offline in a Python script:")
        ln("")
        if combine_preliquid:
            ln(f"    # newfave_output/{stem}_preliquid.TextGrid in this download already has the")
            ln("    # \"phones - combined - liquids\" tier (see pipeline/praat/combine_preliquid_sequences.praat),")
            ln("    # alongside the original, untouched phone tier. new-fave needs exactly 2 tiers")
            ln("    # paired positionally as (Word, Phone), so rebuild a 2-tier copy pointing at")
            ln("    # the combined tier instead of the original phone tier:")
            ln("    import tgt")
            ln(f"    tg = tgt.io.read_textgrid('newfave_output/{stem}_preliquid.TextGrid')")
            ln("    out_tg = tgt.TextGrid()")
            ln("    out_tg.add_tier(tg.tiers[0])   # words")
            ln("    out_tg.add_tier(tg.tiers[-1])  # phones - combined - liquids")
            ln("    tgt.write_to_file(out_tg, 'for_extraction.TextGrid')")
            ln("")
        ln("    from new_fave import fave_audio_textgrid, write_data")
        ln("")
        if has_ft_override:
            ln("    # ft_config.yml is included in your download and contains the FastTrack")
            ln("    # overrides used for this run. You can use it directly instead of")
            ln("    # recreating it, or regenerate it with the code below:")
            ln("    import yaml")
            ln("    ft_override = {}")
            if formant_ceiling is not None:
                ln(f"    ft_override['max_max_formant'] = {formant_ceiling}")
            if num_formants is not None:
                ln(f"    ft_override['n_formants'] = {num_formants}")
            ln("    with open('ft_config.yml', 'w') as f:")
            ln("        yaml.dump(ft_override, f)")
            ln("")
            ft_repr = "'ft_config.yml'"
        else:
            ft_repr = "'default'"

        textgrid_repr = "'for_extraction.TextGrid'" if combine_preliquid else f"'mfa_output/{stem}.TextGrid'"
        ln(f"    speakers = fave_audio_textgrid(")
        ln(f"        {audio_filename!r},")
        ln(f"        {textgrid_repr},")
        ln(f"        speakers={speakers!r},")
        ln(f"        recode_rules={recode_rules!r},")
        ln(f"        labelset_parser={labelset_parser!r},")
        ln(f"        point_heuristic={point_heuristic!r},")
        ln(f"        ft_config={ft_repr},")
        ln(f"        include_overlaps={include_overlaps},")
        ln(f"    )")
        ln(f"    write_data(speakers, destination='newfave_output/')")
    else:
        ln("STEP 3 — VOWEL FORMANT EXTRACTION: skipped")
        ln(BAR)
        ln("")
        ln("new-fave formant extraction was not run for this job.")

    ln("")
    ln(BAR)
    ln("")

    (job_dir / "processing_log.txt").write_text("\n".join(out))


def _fmt_duration(seconds: float) -> str:
    """Format a duration in seconds as '42m 17s'."""
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:2d}s"


def _write_server_log(
    job_id: str,
    job_dir: Path,
    audio_path: Path,
    submitted_at: datetime,
    completed_at: datetime,
    step_times: list,
    failed_step: str | None,
    error_tb: str | None,
    config: dict,
    cancelled_by: str | None = None,
) -> None:
    """Write a server-side diagnostic log for every job, success or failure.

    Logs are stored in data/logs/YYYY-MM/<job_id>.txt, outside the job directory
    so they survive the 72-hour result cleanup.
    """
    w_cfg  = config.get("whisper", {}) or {}
    m_cfg  = config.get("mfa",     {}) or {}
    nf_cfg = config.get("newfave", {}) or {}

    # Version lookups — best effort, don't let failures break logging.
    try:
        whisper_ver = importlib.metadata.version("openai-whisper")
    except Exception:
        whisper_ver = "unknown"
    try:
        newfave_ver = importlib.metadata.version("new-fave")
    except Exception:
        newfave_ver = "unknown"
    fave_ver = get_fave_version(config.get("fave_extract"))
    mfa_ver = _get_mfa_version(m_cfg.get("conda_env", "aligner"))

    # Audio/TextGrid stats — best effort, don't let failures break logging.
    try:
        audio_size_bytes = audio_path.stat().st_size
    except OSError:
        audio_size_bytes = None
    try:
        audio_duration_seconds = round(librosa.get_duration(path=str(audio_path)), 3)
    except Exception:
        audio_duration_seconds = None
    textgrid_duration_seconds = None
    for _tg_path in (
        job_dir / "mfa_output" / f"{audio_path.stem}.TextGrid",
        job_dir / "whisper_output" / f"{audio_path.stem}.TextGrid",
    ):
        if _tg_path.exists():
            try:
                textgrid_duration_seconds = round(tgt.io.read_textgrid(str(_tg_path)).end_time, 3)
            except Exception:
                pass
            break

    total_seconds = (completed_at - submitted_at).total_seconds()
    if cancelled_by:
        status_line = (f"CANCELLED by {cancelled_by} "
                       + (f"during \"{failed_step}\"" if failed_step else "before it started"))
    else:
        status_line = "SUCCESS" if failed_step is None else f"FAILED at \"{failed_step}\""
    queue_position = jobs.get(job_id, {}).get("queue_position_at_submission", 1)
    wait_seconds = jobs.get(job_id, {}).get("wait_seconds", 0.0)
    align_wait_seconds = jobs.get(job_id, {}).get("align_wait_seconds")
    client_info = job_client_info.get(job_id, {})
    client_ip = client_info.get("client_ip", "unknown")
    user_agent = client_info.get("user_agent", "")
    submitter = _submitter_hash(client_info["submitter"]) if client_info.get("submitter") else None
    class_code_label = jobs.get(job_id, {}).get("class_code_label")
    estimated_cost_seconds = client_info.get("estimated_cost_seconds")
    audio_duration_at_submit = client_info.get("audio_duration_at_submit")

    BAR = "=" * 60
    lines = []
    ln = lines.append

    ln("VoxHumana Server Log")
    ln(BAR)
    ln(f"Job ID:       {job_id}")
    ln(f"File:         {audio_path.name}")
    ln(f"File size:    {audio_size_bytes:,} bytes" if audio_size_bytes is not None else "File size:    unknown")
    ln(f"Audio dur.:   {audio_duration_seconds}s" if audio_duration_seconds is not None else "Audio dur.:   unknown")
    ln(f"TextGrid dur: {textgrid_duration_seconds}s" if textgrid_duration_seconds is not None else "TextGrid dur: unknown")
    ln(f"Submitted:    {submitted_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    ln(f"Completed:    {completed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    ln(f"Total time:   {_fmt_duration(total_seconds)}")
    ln(f"Queue pos.:   {queue_position}  (wait: {_fmt_duration(wait_seconds)})")
    if align_wait_seconds is not None:
        ln(f"Align wait:   {_fmt_duration(align_wait_seconds)}  (after transcription, for the alignment queue)")
    ln(f"Server:       {CPU_CORES} cores, {MEM_TOTAL_GB} GB RAM, two-queue pipeline {'on' if PIPELINE else 'off'}")
    ln(f"Client IP:    {client_ip}")
    ln(f"User-Agent:   {user_agent or 'unknown'}")
    ln(f"Submitter:    {submitter or 'unknown'}")
    ln(f"Class code:   {class_code_label or 'none'}")
    if estimated_cost_seconds is not None:
        ln(f"Est. cost:    {_fmt_duration(estimated_cost_seconds)}")
    ln(f"Status:       {status_line}")
    ln("")
    ln("Step timings:")
    for name, secs in step_times:
        ln(f"  {name:<38} {_fmt_duration(secs)}")
    ln("")
    ln("Tool versions:")
    ln(f"  VoxHumana:       {APP_VERSION}")
    ln(f"  openai-whisper:  {whisper_ver}")
    ln(f"  MFA:             {mfa_ver}")
    ln(f"  new-fave:        {newfave_ver}")
    ln(f"  FAVE-extract:    {fave_ver}")
    ln("")
    s_cfg = config.get("steps", {})
    ln("Steps run:")
    ln(f"  transcription:  {s_cfg.get('transcription', True)}")
    ln(f"  alignment:      {s_cfg.get('alignment', True)}")
    ln(f"  formants:       {s_cfg.get('formants', True)}")
    ln("")
    ln("Settings:")
    ln(f"  [Whisper]")
    ln(f"  model:                    {w_cfg.get('model', DEFAULT_WHISPER_MODEL)}")
    ln(f"  language:                 {w_cfg.get('language') or '(auto-detect)'}")
    ln(f"  initial_prompt:           {w_cfg.get('initial_prompt') or '(none)'}")
    ln(f"  condition_on_prev_text:   {w_cfg.get('condition_on_previous_text', True)}")
    ln(f"  [MFA]")
    ln(f"  acoustic_model:           {m_cfg.get('acoustic_model', 'english_us_arpa')}")
    ln(f"  dictionary:               {m_cfg.get('dictionary', 'english_us_arpa')}")
    ln(f"  fine_tune:                {m_cfg.get('fine_tune', False)}")
    ln(f"  num_jobs:                 {m_cfg.get('num_jobs', 1)}")
    ln(f"  oov_mode:                 {m_cfg.get('oov_mode', 'guess')}")
    if m_cfg.get("oov_mode") == "upload":
        ln(f"  oov_dictionary_file:      {m_cfg.get('oov_dictionary_original_filename')}")
        ln(f"  oov_merge_with_builtin:   {m_cfg.get('oov_merge_with_builtin', True)}")
    nf_language = nf_cfg.get("language", "en")
    nf_lang_defaults = LANGUAGE_DEFAULTS.get(nf_language, LANGUAGE_DEFAULTS["en"])
    nf_point_heuristic = nf_cfg.get("point_heuristic", nf_lang_defaults["point_heuristic"])
    nf_combine_preliquid = bool(nf_cfg.get("combine_preliquid", False)) and nf_language == "en"
    nf_recode_rules_default = PRELIQUID_RECODE_RULES if nf_combine_preliquid else nf_lang_defaults["recode_rules"]
    ln(f"  [new-fave]")
    ln(f"  language:                 {nf_language}")
    ln(f"  speakers:                 {nf_cfg.get('speakers', 'all')}")
    ln(f"  recode_rules:             {nf_cfg.get('recode_rules', nf_recode_rules_default)}")
    ln(f"  labelset_parser:          {nf_cfg.get('labelset_parser', nf_lang_defaults['labelset_parser'])}")
    ln(f"  point_heuristic:          {nf_point_heuristic if nf_point_heuristic is not None else 'default (1/3 point)'}")
    ln(f"  formant_ceiling:          {nf_cfg.get('formant_ceiling') or '(default)'}")
    ln(f"  num_formants:             {nf_cfg.get('num_formants') or '(default)'}")
    ln(f"  include_overlaps:         {nf_cfg.get('include_overlaps', True)}")
    ln(f"  combine_preliquid:        {nf_combine_preliquid}")
    if nf_combine_preliquid:
        ln(f"  include_intervocalic:     {nf_cfg.get('include_intervocalic', True)}")
    formants_engine = config.get("formants_engine", "newfave")
    ln(f"  formants_engine:          {formants_engine}")
    if formants_engine == "fave_extract":
        fv_cfg = config.get("fave_extract", {}) or {}
        ln(f"  [FAVE-extract]")
        ln(f"  sex:                      {fv_cfg.get('sex')}")
        ln(f"  formantPredictionMethod:  {fv_cfg.get('formant_prediction_method', 'mahalanobis')}")
        ln(f"  vowelSystem:              {fv_cfg.get('vowel_system', 'NorthAmerican')}")
        ln(f"  remeasurement:            {bool(fv_cfg.get('remeasurement', False))}")

    if error_tb:
        ln("")
        ln("Error:")
        ln(error_tb)

    log_dir = LOGS_DIR / submitted_at.strftime("%Y-%m")
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{job_id}.txt").write_text("\n".join(lines) + "\n")

    # Append a one-line JSON summary to summary.jsonl for analytics.
    # Filenames are intentionally excluded (may contain speaker names).
    # initial_prompt is recorded as a boolean only (content may be sensitive).
    _step_name_to_key = {
        "Step 1 – Transcribing with Whisper":            "whisper",
        "Step 2 – Converting transcript to TextGrid":    "textgrid",
        "Step 3 – Aligning with MFA":                    "mfa",
        "Step 4 – Extracting vowel formants with new-fave": "newfave",
        "Step 4 – Extracting formants with FAVE-extract":  "fave_extract",
    }
    step_seconds = {
        _step_name_to_key[name]: round(secs, 1)
        for name, secs in step_times
        if name in _step_name_to_key
    }
    # Extract just the exception class name from the traceback, not the message.
    error_type = None
    if error_tb:
        for line in reversed(error_tb.strip().splitlines()):
            line = line.strip()
            if line and not line.startswith(" ") and ":" in line:
                error_type = line.split(":")[0].strip()
                break

    summary = {
        "job_id":                    job_id,
        "submitted_at":              submitted_at.isoformat(),
        "completed_at":              completed_at.isoformat(),
        "total_seconds":             round(total_seconds, 1),
        "audio_size_bytes":          audio_size_bytes,
        "audio_duration_seconds":    audio_duration_seconds,
        "textgrid_duration_seconds": textgrid_duration_seconds,
        "queue_position_at_submission": queue_position,
        "wait_seconds":              round(wait_seconds, 1),
        "client_ip":                 client_ip,
        "user_agent":                user_agent,
        "submitter":                 submitter,
        "class_code_label":          class_code_label,
        "estimated_cost_seconds":    estimated_cost_seconds,
        "audio_duration_at_submit":  audio_duration_at_submit,
        "status":                    ("cancelled" if cancelled_by
                                      else "success" if failed_step is None else "failed"),
        "failed_step":               failed_step,
        "error_type":                error_type,
        "whisper_model":             w_cfg.get("model", DEFAULT_WHISPER_MODEL),
        "language":                  w_cfg.get("language"),
        "initial_prompt_used":       bool(w_cfg.get("initial_prompt")),
        "condition_on_previous_text": w_cfg.get("condition_on_previous_text", True),
        "mfa_acoustic_model":        m_cfg.get("acoustic_model", "english_us_arpa"),
        "mfa_dictionary":            m_cfg.get("dictionary", "english_us_arpa"),
        "mfa_fine_tune":             m_cfg.get("fine_tune", False),
        "mfa_oov_mode":              m_cfg.get("oov_mode", "guess"),
        "mfa_oov_merge_with_builtin": m_cfg.get("oov_merge_with_builtin", True),
        "formant_ceiling":           nf_cfg.get("formant_ceiling"),
        "num_formants":              nf_cfg.get("num_formants"),
        "include_overlaps":          nf_cfg.get("include_overlaps", True),
        "combine_preliquid":         nf_combine_preliquid,
        "include_intervocalic":      nf_cfg.get("include_intervocalic", True) if nf_combine_preliquid else None,
        "formants_engine":           formants_engine,
        "step_seconds":              step_seconds,
        "align_wait_seconds":        align_wait_seconds,
        # Hardware and queue setup, so time estimates can be refit per setup.
        "cpu_cores":                 CPU_CORES,
        "mem_total_gb":              MEM_TOTAL_GB,
        "pipeline":                  PIPELINE,
        "versions": {
            "voxhumana":      APP_VERSION,
            "openai-whisper": whisper_ver,
            "mfa":            mfa_ver,
            "new-fave":       newfave_ver,
            "fave-extract":   fave_ver,
        },
    }
    with open(LOGS_DIR / "summary.jsonl", "a") as f:
        f.write(json.dumps(summary) + "\n")


def _check_cancel(job_id: str) -> None:
    """Raise StepKilled if this job was cancelled (or the server is stopping)."""
    if job_id in _cancel_requests or _shutting_down:
        raise StepKilled()


def _run_step(job_id: str, func, *args, env: dict | None = None):
    """Run one heavy pipeline step in a killable child process (web/killable.py)."""
    _check_cancel(job_id)
    step = StepProcess(func, *args, env=env)
    step.start()
    _running_steps[job_id] = step
    try:
        # A cancel that arrived after the check above but before the step was
        # registered wouldn't have found it to kill — catch that here.
        if job_id in _cancel_requests or _shutting_down:
            step.kill()
        return step.wait()
    finally:
        _running_steps.pop(job_id, None)


def _set_align_cores(cores: int) -> None:
    """Record how many cores the alignment worker is using; Whisper gets the rest."""
    _whisper_threads.value = max(1, CPU_CORES - cores)


def _align_step_env() -> dict:
    """Thread limits for the next alignment-queue step (MFA, new-fave, FAVE-extract).

    One core while a Whisper job is running or waiting; every core otherwise.
    Decided per step — the steps are short, so no need to adjust mid-step.
    """
    cores = 1 if not transcribe_queue.is_idle() else CPU_CORES
    _set_align_cores(cores)
    # numpy/BLAS and joblib (new-fave's parallel workers) read these.
    return {name: cores for name in (
        "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "LOKY_MAX_CPU_COUNT",
    )}


def _enqueue(run: _JobRun, stage: str) -> None:
    """Add a job to the "transcribe" or "align" queue and wake that queue's worker."""
    if stage == "align":
        cost = run.align_cost
    else:  # without pipelining, the whole job runs in this queue
        cost = run.transcribe_cost if PIPELINE else run.transcribe_cost + run.align_cost
    jobs[run.job_id]["queue"] = stage
    _QUEUES[stage].add(QueuedJob(run.job_id, run.submitter, cost, run.priority_label))
    _executors[stage].submit(_run_next, stage)


def _cancel_job(job_id: str, by: str) -> str:
    """Cancel a waiting or running job on behalf of "user" or "admin".

    Returns "cancelled" (it was waiting and is gone now), "cancelling" (it's
    running; its worker finishes the cleanup once the step dies), or
    "finished" (too late — it already completed or failed).
    """
    job = jobs[job_id]
    if job["status"] not in ("queued", "running"):
        return "finished"
    _cancel_requests[job_id] = by
    if transcribe_queue.remove(job_id) or align_queue.remove(job_id):
        run = _job_runs[job_id]
        job.update(status="error", cancelled=True, error=_CANCEL_MESSAGES[by],
                   step_name="Cancelled")
        _finish_job(run, "Waiting for alignment" if run.align_enqueued_at else None,
                    error_tb=None, cancelled_by=by)
        return "cancelled"
    # Already picked by a worker (or between queues): kill its current step,
    # if one is running. Otherwise _check_cancel() stops it before its next step.
    job["step_name"] = "Cancelling…"
    step = _running_steps.get(job_id)
    if step is not None:
        step.kill()
    return "cancelling"


def _run_next(stage: str) -> None:
    """Executor tick for one queue: run whichever waiting job it picks now."""
    if _shutting_down:
        return
    queue = _QUEUES[stage]
    queued = queue.pop_next()
    if queued is None:
        return
    run = _job_runs[queued.job_id]
    hand_off = False
    try:
        hand_off = _run_stage(run, stage)
    finally:
        # Guarded: this runs on a queue's only worker thread, so an exception
        # escaping here would leave every job behind it looking stuck. The
        # slot is freed before any hand-off, so a job is never counted in
        # both queues at once.
        try:
            queue.finish(queued.job_id)
            if stage == "align" and PIPELINE:
                _set_align_cores(0)  # alignment worker idle: Whisper may use every core
        except Exception:
            pass
    if hand_off:
        _enqueue(run, "align")


def _run_stage(run: _JobRun, stage: str) -> bool:
    """Run one queue's share of a job, honoring the steps config.

    In the transcription queue that's Whisper and its TextGrid/transcript;
    in the alignment queue, MFA, formants, and the utterance tier. Without
    pipelining, the transcription queue runs everything. Returns True when
    the job should move on to the alignment queue; otherwise the job is over
    (done, failed, or cancelled) and has been logged and cleaned up.
    """
    job_id = run.job_id
    job = jobs[job_id]
    job_dir = JOBS_DIR / job_id
    audio_path, config = run.audio_path, run.config
    stem = audio_path.stem
    step_times = run.step_times

    now = datetime.now(timezone.utc)
    if job.get("started_at") is None:  # this job's first turn on a worker
        wait_seconds = (now - datetime.fromisoformat(job["created_at"])).total_seconds()
        job.update(status="running", started_at=now.isoformat(), wait_seconds=round(wait_seconds, 1))
    elif run.align_enqueued_at is not None:
        job["align_wait_seconds"] = round((now - run.align_enqueued_at).total_seconds(), 1)

    current_step: str | None = None
    error_tb: str | None = None
    cancelled_by: str | None = None

    steps = config.get("steps", {})
    run_transcription = steps.get("transcription", True)
    run_alignment     = steps.get("alignment", True)
    run_formants      = steps.get("formants", True)
    do_alignment_half = stage == "align" or not PIPELINE
    # Thread limits for alignment steps only matter when they share the CPU
    # with a Whisper job, i.e. when running in the alignment queue.
    align_env = _align_step_env if stage == "align" else (lambda: None)

    # Always defined so new-fave can find the TextGrid even when alignment was skipped.
    mfa_output_dir = job_dir / "mfa_output"

    try:
        if stage == "transcribe" and run_transcription:
            current_step = "Step 1 – Transcribing with Whisper"
            job.update(step=1, step_name="Transcribing with Whisper")
            _t = datetime.now(timezone.utc)
            whisper_result = _run_step(
                job_id, transcribe, str(audio_path), str(job_dir), config.get("whisper"),
                _whisper_threads if PIPELINE else None,
            )
            step_times.append((current_step, (datetime.now(timezone.utc) - _t).total_seconds()))

            current_step = "Step 2 – Converting transcript to TextGrid"
            job.update(step=2, step_name="Converting transcript to TextGrid")
            _t = datetime.now(timezone.utc)
            _check_cancel(job_id)
            convert_whisper_to_textgrid(whisper_result, str(audio_path), str(job_dir))
            step_times.append((current_step, (datetime.now(timezone.utc) - _t).total_seconds()))

            current_step = "Step 2b – Generating line-by-line transcript"
            job.update(step=2, step_name="Generating line-by-line transcript")
            _t = datetime.now(timezone.utc)
            _check_cancel(job_id)
            generate_transcript(str(job_dir), stem, config.get("praat"))
            step_times.append((current_step, (datetime.now(timezone.utc) - _t).total_seconds()))

            if PIPELINE and (run_alignment or run_formants):
                run.align_enqueued_at = datetime.now(timezone.utc)
                job["step_name"] = "Transcribed — waiting for alignment"
                return True

        if do_alignment_half and run_alignment:
            current_step = "Step 3 – Aligning with MFA"
            job.update(step=3, step_name="Aligning with MFA")
            _t = datetime.now(timezone.utc)
            mfa_output_dir = _run_step(job_id, align_with_mfa, str(audio_path), str(job_dir),
                                       config.get("mfa"), env=align_env())
            step_times.append((current_step, (datetime.now(timezone.utc) - _t).total_seconds()))
            # If the user supplied their own TextGrid (Transcribe skipped), remove the
            # whisper_output/ staging folder — it only contained their uploaded file.
            if not run_transcription:
                shutil.rmtree(job_dir / "whisper_output", ignore_errors=True)

        if do_alignment_half and run_formants:
            formants_engine = config.get("formants_engine", "newfave")
            if formants_engine == "fave_extract":
                current_step = "Step 4 – Extracting formants with FAVE-extract"
                job.update(step=4, step_name="Extracting formants with FAVE-extract")
                _t = datetime.now(timezone.utc)
                _run_step(job_id, extract_with_fave, str(audio_path), mfa_output_dir, str(job_dir),
                          config.get("fave_extract"), env=align_env())
                step_times.append((current_step, (datetime.now(timezone.utc) - _t).total_seconds()))
            else:
                current_step = "Step 4 – Extracting vowel formants with new-fave"
                job.update(step=4, step_name="Extracting vowel formants with new-fave")
                _t = datetime.now(timezone.utc)
                _run_step(job_id, extract_with_newfave, str(audio_path), mfa_output_dir, str(job_dir),
                          config.get("newfave"), env=align_env())
                step_times.append((current_step, (datetime.now(timezone.utc) - _t).total_seconds()))
            # If the user supplied their own MFA TextGrid (Align skipped), remove the
            # mfa_output/ staging folder — it only contained their uploaded file.
            if not run_alignment:
                shutil.rmtree(job_dir / "mfa_output", ignore_errors=True)

        # Adds the Whisper utterance tier to the bottom of the MFA TextGrid.
        # Only possible when both Whisper and MFA ran (needs whisper_output/
        # for the utterance tier and mfa_output/ to merge it into). Must run
        # after new-fave (above) — new-fave expects exactly a Word/Phone tier
        # pair, and this 3-tier grid would break its tier pairing.
        if do_alignment_half and run_transcription and run_alignment:
            current_step = "Step 5 – Adding utterance tier to MFA TextGrid"
            report_step = 4 if run_formants else 3
            job.update(step=report_step, step_name="Adding utterance tier to MFA TextGrid")
            _t = datetime.now(timezone.utc)
            _check_cancel(job_id)
            combine_textgrids(str(job_dir), stem, config.get("praat"))
            step_times.append((current_step, (datetime.now(timezone.utc) - _t).total_seconds()))

        _write_processing_log(
            job_dir, job_id, config,
            audio_filename=audio_path.name,
            submitted_at=datetime.fromisoformat(job["created_at"]),
        )
        job.update(status="done", step=5, step_name="Complete")
        current_step = None  # marks success

    except StepKilled:
        cancelled_by = _cancel_requests.get(job_id)
        if cancelled_by:
            job.update(status="error", cancelled=True,
                       error=_CANCEL_MESSAGES[cancelled_by], step_name="Cancelled")
        else:  # killed because the server is shutting down
            job.update(
                status="error",
                error="The server restarted while this job was running. Please submit it again.",
            )

    except Exception as exc:
        error_tb = traceback.format_exc()
        # Write the full traceback to disk for debugging; never send it to the client.
        (JOBS_DIR / job_id / "error.log").write_text(error_tb)
        job.update(status="error", error=str(exc))

    _finish_job(run, current_step, error_tb, cancelled_by)
    return False


def _finish_job(run: _JobRun, failed_step: str | None, error_tb: str | None,
                cancelled_by: str | None) -> None:
    """Log and clean up a job that's over — done, failed, or cancelled.

    Every step is independently guarded: this runs on a queue's worker
    thread, and a raise here must not take the worker down with it.
    """
    job_id = run.job_id
    job_dir = JOBS_DIR / job_id
    try:
        _write_server_log(
            job_id=job_id,
            job_dir=job_dir,
            audio_path=run.audio_path,
            submitted_at=datetime.fromisoformat(jobs[job_id]["created_at"]),
            completed_at=datetime.now(timezone.utc),
            step_times=run.step_times,
            failed_step=failed_step,
            error_tb=error_tb,
            config=run.config,
            cancelled_by=cancelled_by,
        )
    except Exception:
        pass
    try:
        _cleanup_intermediates(job_dir, run.audio_path)
    except Exception:
        pass
    # A cancelled job has nothing worth downloading — remove it entirely.
    if cancelled_by:
        shutil.rmtree(job_dir, ignore_errors=True)
    _cancel_requests.pop(job_id, None)
    _job_runs.pop(job_id, None)
    try:
        _cleanup_orphaned_audio()
    except Exception:
        pass
    try:
        _expire_old_jobs()
    except Exception:
        pass


@app.post("/api/textgrid-tiers")
async def get_textgrid_tiers(textgrid: UploadFile = File(...)):
    """
    List the tiers in an uploaded TextGrid, with best-effort guesses at
    which tier plays which role.

    Used by two upload flows to let the user confirm/correct tier roles
    before a job is submitted (see pipeline/tier_selection.py):
      - "Extract only" (Transcribe and Align both off): new-fave pairs
        tiers positionally as (Word, Phone) and silently misreads
        TextGrids with the wrong tier count or order.
      - "Align only" (Transcribe off, Align on): MFA reads every tier in
        the TextGrid as a separate speaker's utterance list, so the user
        must confirm which single tier holds the utterance transcription.
    Both guesses are returned regardless of which flow is active, since
    computing either is cheap.
    """
    contents = await textgrid.read()
    with tempfile.NamedTemporaryFile(suffix=".TextGrid", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)

    try:
        tier_names = list_tier_names(tmp_path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse TextGrid: {exc}")
    finally:
        tmp_path.unlink(missing_ok=True)

    phone_idx, word_idx = guess_tier_roles(tier_names)
    utterance_idx = guess_utterance_tier(tier_names)
    return {
        "tiers": tier_names,
        "guess": {"phone": phone_idx, "word": word_idx, "utterance": utterance_idx},
    }


@app.post("/api/jobs")
async def create_job(
    request: Request,
    audio: UploadFile = File(...),
    textgrid: Optional[UploadFile] = File(None),
    phone_tier_index: Optional[int] = Form(None),
    word_tier_index: Optional[int] = Form(None),
    utterance_tier_index: Optional[int] = Form(None),
    whisper_model: str = Form(DEFAULT_WHISPER_MODEL),
    language: Optional[str] = Form(None),
    initial_prompt: Optional[str] = Form(None),
    condition_on_previous_text: bool = Form(True),
    acoustic_model: str = Form("english_us_arpa"),
    dictionary: str = Form("english_us_arpa"),
    fine_tune: bool = Form(False),
    oov_mode: str = Form("guess"),
    oov_merge_with_builtin: bool = Form(True),
    oov_custom_words: Optional[str] = Form(None),
    oov_dictionary: Optional[UploadFile] = File(None),
    formant_ceiling: Optional[str] = Form(None),
    num_formants: Optional[str] = Form(None),
    include_overlaps: bool = Form(True),
    combine_preliquid: bool = Form(False),
    include_intervocalic: bool = Form(True),
    formants_engine: str = Form("newfave"),
    fave_sex: Optional[str] = Form(None),
    fave_name: Optional[str] = Form(None),
    run_transcription: bool = Form(True),
    run_alignment: bool = Form(True),
    run_formants: bool = Form(True),
    submitter_id: Optional[str] = Form(None),
    class_code: Optional[str] = Form(None),
):
    # Class code: checked up front so a typo or an out-of-window code fails
    # before the upload is saved. Only the label is kept on the job (it's
    # shown back to the user); the code itself never leaves this function.
    class_code_label = None
    if class_code and class_code.strip():
        entry = class_codes.get(class_code)
        if entry is None:
            raise HTTPException(
                status_code=400,
                detail="Class code not recognized. Check the spelling, or clear the "
                       "Class code field to submit without priority.",
            )
        status = code_status(entry)
        if status != "active":
            raise HTTPException(
                status_code=400,
                detail=f"The class code for {entry['label']} is "
                       f"{'not active yet' if status == 'upcoming' else 'no longer active'} "
                       f"(active {_fmt_utc(entry['starts_at'])} to {_fmt_utc(entry['ends_at'])}). Clear the "
                       "Class code field to submit without priority.",
            )
        class_code_label = entry["label"]

    # Validate MFA model/dictionary names against the server-side allowlist.
    # These values go straight into the MFA CLI command, so we reject unknowns
    # rather than pass arbitrary strings through.
    if acoustic_model not in SUPPORTED_MFA_ACOUSTIC_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported acoustic model '{acoustic_model}'. "
                   f"Supported: {sorted(SUPPORTED_MFA_ACOUSTIC_MODELS)}",
        )
    if dictionary not in SUPPORTED_MFA_DICTIONARIES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported dictionary '{dictionary}'. "
                   f"Supported: {sorted(SUPPORTED_MFA_DICTIONARIES)}",
        )

    # Out-of-vocabulary word handling only means anything when MFA alignment
    # actually runs. The Alignment step's advanced options (including this
    # radio group) persist across "Start Over" regardless of which steps are
    # currently toggled on, so a stale oov_mode could otherwise be submitted
    # alongside Align turned off — normalize back to the no-op default rather
    # than demanding a dictionary/word list nobody will use.
    if not run_alignment:
        oov_mode = "guess"

    # Out-of-vocabulary word handling: "guess" (MFA's G2P, default), "upload"
    # (a user-supplied dictionary file), or "type" (pasted word/pronunciation
    # pairs). Presence checks here; the files themselves are read, sanity-
    # checked, and phone-validated further down, once job_dir exists.
    if oov_mode not in ("guess", "upload", "type"):
        raise HTTPException(status_code=400, detail=f"Unsupported oov_mode '{oov_mode}'.")
    if oov_mode == "upload" and oov_dictionary is None:
        raise HTTPException(
            status_code=400,
            detail="Upload mode selected for out-of-vocabulary word handling, but no "
                   "dictionary file was provided.",
        )
    if oov_mode == "type" and not (oov_custom_words and oov_custom_words.strip()):
        raise HTTPException(
            status_code=400,
            detail="Type-custom-words mode selected for out-of-vocabulary word handling, "
                   "but no words were entered.",
        )

    # Safety net: new-fave formant extraction needs a labelset parser and
    # recode scheme matching the MFA acoustic model's phone set. If an
    # unsupported acoustic model arrives with formants enabled — e.g. from a
    # direct API call bypassing the UI guard — quietly disable the formant
    # step rather than letting the pipeline fail mid-run.
    newfave_language = NEWFAVE_LANGUAGE_PRESETS.get(acoustic_model)
    if newfave_language is None and run_formants:
        run_formants = False

    # FAVE-extract is a legacy, English-only alternative to new-fave (it's
    # built entirely around CMU ARPABET / English dialectology -- there's no
    # multi-language support to speak of). Reject a request for it against a
    # non-English acoustic model rather than silently falling back to
    # new-fave, and require speaker sex up front: FAVE-extract's default
    # 'mahalanobis' formant-prediction method hard-fails without it.
    if formants_engine not in ("newfave", "fave_extract"):
        raise HTTPException(status_code=400, detail=f"Unsupported formants_engine '{formants_engine}'.")
    if formants_engine == "fave_extract" and run_formants:
        if newfave_language != "en":
            raise HTTPException(
                status_code=400,
                detail="FAVE-extract only supports English. Choose the English acoustic "
                       "model, or use new-fave for other languages.",
            )
        if not fave_sex or fave_sex.lower() not in ("m", "male", "f", "female"):
            raise HTTPException(
                status_code=400,
                detail="FAVE-extract requires speaker sex ('m' or 'f') to run its default "
                       "formant-prediction method.",
            )

    job_id = _generate_job_id()
    while job_id in jobs:
        job_id = _generate_job_id()
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    original_name = audio.filename or "audio.wav"
    suffix = Path(original_name).suffix or ".wav"
    safe_stem = _sanitize_stem(original_name)
    audio_path = job_dir / f"{safe_stem}{suffix}"

    total = 0
    with audio_path.open("wb") as fh:
        while chunk := await audio.read(1024 * 1024):  # stream 1 MB at a time
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                fh.close()
                audio_path.unlink(missing_ok=True)
                job_dir.rmdir()
                raise HTTPException(
                    status_code=413,
                    detail="File too large. Maximum upload size is 1 GB. "
                           "See the User Guide for tips on splitting long recordings.",
                )
            fh.write(chunk)

    # If a TextGrid was uploaded (Transcribe skipped), route it to the right directory:
    #   - Align is running  → whisper_output/ (utterance TextGrid for MFA)
    #   - Align is skipped  → mfa_output/     (MFA-format TextGrid for new-fave)
    uploaded_files = [original_name]
    if oov_mode == "upload" and oov_dictionary is not None:
        uploaded_files.append(oov_dictionary.filename or "custom_dictionary.dict")
    tier_selection: Optional[dict] = None
    if textgrid is not None and not run_transcription:
        tg_original = textgrid.filename or "transcript.TextGrid"
        if run_alignment:
            tg_dir = job_dir / "whisper_output"
        else:
            tg_dir = job_dir / "mfa_output"
        tg_dir.mkdir(parents=True, exist_ok=True)
        tg_path = tg_dir / f"{safe_stem}.TextGrid"
        tg_contents = await textgrid.read()
        tg_path.write_bytes(tg_contents)
        uploaded_files.append(tg_original)

        # Align skipped: this TextGrid feeds new-fave directly, which pairs
        # tiers positionally as (Word, Phone) and breaks silently on any
        # other tier count or order. Rebuild it as a canonical 2-tier grid
        # from the user's tier picks (or a best-effort name-based guess, if
        # the UI didn't send picks — e.g. a direct API call).
        if not run_alignment:
            try:
                tier_names = list_tier_names(tg_path)
            except Exception as exc:
                shutil.rmtree(job_dir, ignore_errors=True)
                raise HTTPException(
                    status_code=400,
                    detail=f"Could not parse {tg_original} as a TextGrid: {exc}",
                )
            guessed_phone, guessed_word = guess_tier_roles(tier_names)
            phone_idx = phone_tier_index if phone_tier_index is not None else guessed_phone
            word_idx = word_tier_index if word_tier_index is not None else guessed_word
            valid_range = range(len(tier_names))
            if (
                phone_idx is None or word_idx is None or phone_idx == word_idx
                or phone_idx not in valid_range or word_idx not in valid_range
            ):
                shutil.rmtree(job_dir, ignore_errors=True)
                raise HTTPException(
                    status_code=400,
                    detail=f"Could not determine word/phone tiers in {tg_original} "
                           f"(found {len(tier_names)} tier(s): {tier_names}). "
                           "Please select the word and phone tiers explicitly.",
                )
            extract_word_phone_tiers(tg_path, word_idx=word_idx, phone_idx=phone_idx)
            tier_selection = {
                "tier_names": tier_names,
                "phone_idx": phone_idx,
                "word_idx": word_idx,
            }
        else:
            # Align running: this TextGrid feeds MFA directly as its transcript
            # input. MFA treats every tier in the file as a separate speaker's
            # utterance list, so extra tiers (or the wrong tier holding the
            # transcription) would confuse alignment. Trim it down to just the
            # user-selected (or best-effort guessed) utterance tier.
            try:
                tier_names = list_tier_names(tg_path)
            except Exception as exc:
                shutil.rmtree(job_dir, ignore_errors=True)
                raise HTTPException(
                    status_code=400,
                    detail=f"Could not parse {tg_original} as a TextGrid: {exc}",
                )
            guessed_utterance = guess_utterance_tier(tier_names)
            utterance_idx = (
                utterance_tier_index if utterance_tier_index is not None else guessed_utterance
            )
            if utterance_idx is None or utterance_idx not in range(len(tier_names)):
                shutil.rmtree(job_dir, ignore_errors=True)
                raise HTTPException(
                    status_code=400,
                    detail=f"Could not determine the utterance-transcript tier in {tg_original} "
                           f"(found {len(tier_names)} tier(s): {tier_names}). "
                           "Please select the utterance tier explicitly.",
                )
            extract_utterance_tier(tg_path, utterance_idx=utterance_idx)
            tier_selection = {
                "tier_names": tier_names,
                "utterance_idx": utterance_idx,
            }

    # Out-of-vocabulary custom dictionary/words: write whatever was submitted
    # to disk, sanity-check it looks like a pronunciation dictionary, then
    # validate it against MFA itself — synchronously, right here in the
    # request — so a bad phone symbol (e.g. "H" instead of "HH") fails fast
    # with a specific error instead of surfacing deep into a background
    # alignment run. If this fails, the job is never queued.
    oov_dict_path: Optional[Path] = None
    oov_words_path: Optional[Path] = None
    oov_resolved_dictionary_path: Optional[Path] = None
    if oov_mode in ("upload", "type"):
        oov_dir = job_dir / "mfa_oov"
        oov_dir.mkdir(parents=True, exist_ok=True)

        if oov_mode == "upload":
            oov_suffix = Path(oov_dictionary.filename or "").suffix.lower()
            if oov_suffix not in _OOV_DICT_EXTENSIONS:
                shutil.rmtree(job_dir, ignore_errors=True)
                raise HTTPException(
                    status_code=400,
                    detail=f"Custom dictionary must be .txt or .dict (got "
                           f"'{oov_suffix or '(none)'}').",
                )
            oov_contents = await oov_dictionary.read()
            if len(oov_contents) > MAX_OOV_UPLOAD_BYTES:
                shutil.rmtree(job_dir, ignore_errors=True)
                raise HTTPException(
                    status_code=413,
                    detail="Custom dictionary file too large. Maximum is 5 MB.",
                )
            oov_text = oov_contents.decode(errors="replace")
            if not _looks_like_pronunciation_dict(oov_text):
                shutil.rmtree(job_dir, ignore_errors=True)
                raise HTTPException(
                    status_code=400,
                    detail="Custom dictionary doesn't look like a tab-separated "
                           "word/pronunciation file (word<TAB>phones per line).",
                )
            oov_dict_path = oov_dir / f"{_sanitize_stem(oov_dictionary.filename or 'custom_dict')}{oov_suffix}"
            oov_dict_path.write_bytes(oov_contents)
            new_pronunciations_path = oov_dict_path
        else:  # oov_mode == "type"
            if not _looks_like_pronunciation_dict(oov_custom_words):
                shutil.rmtree(job_dir, ignore_errors=True)
                raise HTTPException(
                    status_code=400,
                    detail="Custom words don't look like tab-separated word/pronunciation "
                           "lines (word<TAB>phones per line).",
                )
            oov_words_path = oov_dir / "typed_words.dict"
            oov_words_path.write_text(oov_custom_words)
            new_pronunciations_path = oov_words_path

        try:
            merged_path = await asyncio.to_thread(
                merge_or_validate_pronunciations, dictionary, new_pronunciations_path, oov_dir,
            )
        except RuntimeError as exc:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=str(exc))

        if oov_mode == "upload" and not oov_merge_with_builtin:
            # Validated above for phone compatibility, but the actual
            # alignment dictionary is the raw upload, not the merged copy.
            oov_resolved_dictionary_path = oov_dict_path
        else:
            oov_resolved_dictionary_path = merged_path

    config = {
        "whisper": {
            "model": whisper_model,
            "language": language or None,
            "initial_prompt": initial_prompt or None,
            "condition_on_previous_text": condition_on_previous_text,
        },
        "mfa": {
            "acoustic_model": acoustic_model,
            "dictionary": dictionary,
            "fine_tune": fine_tune,
            "oov_mode": oov_mode,
            "oov_resolved_dictionary_path": str(oov_resolved_dictionary_path) if oov_resolved_dictionary_path else None,
            "oov_merge_with_builtin": oov_merge_with_builtin,
            "oov_dictionary_original_filename": oov_dictionary.filename if oov_dictionary else None,
            "oov_custom_words_text": oov_custom_words if oov_mode == "type" else None,
        },
        "newfave": {
            "language": newfave_language or "en",
            "formant_ceiling": int(formant_ceiling) if formant_ceiling else None,
            "num_formants": int(num_formants) if num_formants else None,
            "include_overlaps": include_overlaps,
            "combine_preliquid": combine_preliquid,
            "include_intervocalic": include_intervocalic,
        },
        "formants_engine": formants_engine,
        "fave_extract": {
            "sex": fave_sex.lower() if fave_sex else None,
            "name": fave_name or None,
        },
        "steps": {
            "transcription": run_transcription,
            "alignment":     run_alignment,
            "formants":      run_formants,
        },
        "tier_selection": tier_selection,
    }

    download_token = uuid.uuid4().hex

    # Who submitted this, for fair sharing between users. The browser sends a
    # random ID kept in localStorage (so a whole batch, or a classroom behind
    # one shared IP, is told apart correctly); fall back to the IP for direct
    # API calls without one.
    client_ip = _get_client_ip(request)
    submitter = (
        submitter_id if submitter_id and _SUBMITTER_ID_RE.match(submitter_id)
        else f"ip:{client_ip}"
    )

    # Audio length drives the cost estimate the scheduler orders jobs by.
    try:
        audio_duration_at_submit = await asyncio.to_thread(
            librosa.get_duration, path=str(audio_path)
        )
    except Exception:
        audio_duration_at_submit = None
    transcribe_cost, align_cost = estimate_stage_times(
        audio_duration_at_submit, whisper_model,
        run_transcription, run_alignment, run_formants,
    )
    cost = transcribe_cost + align_cost

    job_client_info[job_id] = {
        "client_ip": client_ip,
        "user_agent": request.headers.get("user-agent", ""),
        "submitter": submitter,
        "estimated_cost_seconds": round(cost, 1),
        "audio_duration_at_submit": (
            round(audio_duration_at_submit, 3) if audio_duration_at_submit else None
        ),
        # Which steps were requested, for the admin queue view.
        "steps": {
            "whisper":  whisper_model if run_transcription else None,
            "mfa":      run_alignment,
            "formants": (
                ("FAVE-extract" if formants_engine == "fave_extract" else "new-fave")
                if run_formants else None
            ),
        },
    }

    jobs[job_id] = {
        "status": "queued",
        "step": 0,
        "step_name": "Queued",
        "error": None,
        "uploaded_files": uploaded_files,
        "audio_filename": audio_path.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "download_token": download_token,
        # 1 = no one ahead (next up); >1 = that many jobs (including this one)
        # were queued or running when this job was submitted.
        "queue_position_at_submission": sum(
            q.pending_count() + q.running_count() for q in _QUEUES.values()
        ) + 1,
        "class_code_label": class_code_label,
    }

    run = _JobRun(job_id, audio_path, config, submitter, class_code_label,
                  transcribe_cost, align_cost)
    _job_runs[job_id] = run
    # A job that skips Whisper goes straight to the alignment queue.
    _enqueue(run, "align" if PIPELINE and not run_transcription else "transcribe")

    return JSONResponse({"job_id": job_id, "download_token": download_token})


def _job_status_payload(job_id: str) -> Optional[dict]:
    """Return a job's status dict (with queue position filled in), or None if unknown.

    Position comes from the predicted order of whichever queue the job is
    waiting in, which can shift as other jobs arrive (a newcomer with a short
    file may go ahead of you). A job waiting for the alignment queue after
    Whisper has status "running" — it has started — but still gets a position.
    """
    if job_id not in jobs:
        return None
    job = dict(jobs[job_id])
    stage = job.get("queue")
    if job["status"] not in ("queued", "running") or stage is None:
        return job
    queue = _QUEUES[stage]
    order = queue.predicted_order()
    if job_id not in order:
        if job["status"] == "queued":
            # Picked by a worker, but not marked running yet.
            job["step_name"] = "Starting…"
        return job
    ahead = order.index(job_id)
    running = queue.running_count()
    job["queue_position"] = ahead + 1           # 1 = next to start
    job["queue_length"] = len(order) + running  # waiting + running, in this queue
    job["queue_ahead"] = ahead
    jobs_ahead = f"{ahead} job{'s' if ahead != 1 else ''} ahead of yours"
    if job["status"] == "running":  # transcribed, waiting for the alignment queue
        if ahead:
            text = f"Transcribed — waiting for alignment ({jobs_ahead})"
        else:
            text = ("Transcribed — next in line for alignment" if running
                    else "Transcribed — starting alignment…")
    elif ahead:
        text = f"Waiting in queue — {jobs_ahead}"
    else:
        text = ("Next in line — starts when the current job finishes" if running
                else "Starting…")
    if job.get("class_code_label"):
        text = f"Priority ({job['class_code_label']}) · {text}"
    job["step_name"] = text
    return job


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = _job_status_payload(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse(job)


@app.get("/api/jobs")
async def get_jobs_status(ids: str):
    """Batch status check for several jobs in one request.

    The batch-upload UI can have many jobs in flight at once (the primary
    submission plus every additional confirmed file); polling each one on
    its own timer meant a separate request per job every few seconds. The
    client instead collects every job it still needs to check and calls this
    once per tick, regardless of how many jobs that covers.
    """
    job_ids = [j for j in ids.split(",") if j]
    result = {}
    for job_id in job_ids:
        payload = _job_status_payload(job_id)
        result[job_id] = payload if payload is not None else {"status": "not_found"}
    return JSONResponse(result)


@app.get("/api/queue")
async def get_queue():
    """Current queue size, shown on the form before submitting.

    Only counts are exposed — never job IDs, which double as status lookup keys.
    """
    running = sum(q.running_count() for q in _QUEUES.values())
    waiting = sum(q.pending_count() for q in _QUEUES.values())
    # While a class window is open, the form warns people without the code
    # that class jobs will go first. Only the time left is exposed — never
    # the code or which class it is.
    priority_until = class_codes.active_until()
    priority_minutes_left = None
    if priority_until is not None:
        seconds_left = (priority_until - datetime.now(timezone.utc)).total_seconds()
        priority_minutes_left = max(1, -(-int(seconds_left) // 60))  # round up
    return JSONResponse({
        "queue_length": running + waiting,
        "running": running,
        "waiting": waiting,
        "submitters": len(transcribe_queue.submitters() | align_queue.submitters()),
        "class_priority_minutes_left": priority_minutes_left,
    })


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, token: str = ""):
    """Cancel your own job. Takes the job's download token, like the download link."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    if not secrets.compare_digest(token, jobs[job_id].get("download_token", "")):
        raise HTTPException(status_code=403, detail="Invalid token")
    outcome = await asyncio.to_thread(_cancel_job, job_id, "user")
    return JSONResponse({"outcome": outcome})


@app.get("/api/class-code")
async def check_class_code(code: str = ""):
    """Look up one class code for the form's inline check. Never lists codes."""
    entry = class_codes.get(code) if code.strip() else None
    if entry is None:
        return JSONResponse({"valid": False})
    return JSONResponse({
        "valid": True,
        "active": code_status(entry) == "active",
        "label": entry["label"],
        "starts_at": entry["starts_at"],
        "ends_at": entry["ends_at"],
    })


# ─── Admin (class codes + queue view) ─────────────────────────────────────────
# Not linked from the site. Every /api/admin route requires the token from
# data/admin_token.txt as `Authorization: Bearer <token>`.

@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
async def serve_admin():
    html = _ADMIN_HTML.read_text()
    html = html.replace("__ROOT_PATH__", ROOT_PATH)
    html = html.replace("__VERSION__", APP_VERSION)
    return HTMLResponse(html)


@app.get("/api/admin/class-codes")
async def admin_list_class_codes(authorization: Optional[str] = Header(None)):
    _require_admin(authorization)
    return JSONResponse({"codes": class_codes.list()})


@app.post("/api/admin/class-codes")
async def admin_create_class_code(
    authorization: Optional[str] = Header(None),
    label: str = Form(...),
    starts_at: str = Form(...),
    ends_at: str = Form(...),
    code: Optional[str] = Form(None),
):
    _require_admin(authorization)
    try:
        entry = class_codes.create(label, starts_at, ends_at, code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return JSONResponse(entry)


@app.delete("/api/admin/class-codes/{code}")
async def admin_delete_class_code(code: str, authorization: Optional[str] = Header(None)):
    _require_admin(authorization)
    if not class_codes.delete(code):
        raise HTTPException(status_code=404, detail="Code not found.")
    return JSONResponse({"deleted": code})


@app.get("/api/admin/queue")
async def admin_queue(authorization: Optional[str] = Header(None)):
    """Both queues: running jobs plus waiting jobs in predicted order.

    Times are per queue: "Est. time" is that queue's share of the job, and
    "waited" counts from when the job entered that queue.
    """
    _require_admin(authorization)
    now = datetime.now(timezone.utc).timestamp()

    def row(q: QueuedJob, stage: str, position: int) -> dict:
        info = job_client_info.get(q.job_id, {})
        return {
            "queue": stage,
            "position": position,  # 0 = running, then predicted start order
            "job_id": q.job_id,
            "submitter": _submitter_hash(q.submitter),
            "audio_filename": jobs.get(q.job_id, {}).get("audio_filename"),
            "audio_seconds": info.get("audio_duration_at_submit"),
            "steps": info.get("steps"),
            "estimated_cost_seconds": round(q.cost),
            "priority_label": q.priority_label,
            "waited_seconds": round((q.started_at or now) - q.enqueued_at),
            "running_seconds": round(now - q.started_at) if q.started_at else None,
            "step_name": jobs.get(q.job_id, {}).get("step_name"),
        }

    rows = []
    for stage, queue in _QUEUES.items():
        snap = queue.snapshot()
        rows += [row(q, stage, 0) for q in snap["running"]]
        rows += [row(q, stage, i + 1) for i, q in enumerate(snap["waiting"])]
    return JSONResponse({
        "jobs": rows,
        "pipeline": PIPELINE,
        "cpu_cores": CPU_CORES,
        "whisper_threads": _whisper_threads.value if PIPELINE else CPU_CORES,
    })


@app.post("/api/admin/jobs/{job_id}/cancel")
async def admin_cancel_job(job_id: str, authorization: Optional[str] = Header(None)):
    _require_admin(authorization)
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    outcome = await asyncio.to_thread(_cancel_job, job_id, "admin")
    return JSONResponse({"outcome": outcome})


@app.get("/api/jobs/{job_id}/download")
async def download_results(job_id: str, token: str = ""):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    if token != jobs[job_id].get("download_token", ""):
        raise HTTPException(status_code=403, detail="Invalid download token")
    if jobs[job_id]["status"] not in ("done", "error"):
        raise HTTPException(status_code=400, detail="Job not complete")

    job_dir = JOBS_DIR / job_id
    audio_filename = jobs[job_id].get("audio_filename", "")
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in job_dir.rglob("*"):
            if not f.is_file():
                continue
            # Skip MFA working directories (large, not useful to users)
            if "mfa_corpus" in f.parts or "mfa_temp" in f.parts:
                continue
            # Skip the uploaded audio file kept server-side
            if f.name == audio_filename:
                continue
            zf.write(f, f.relative_to(job_dir))

    buf.seek(0)
    stem = Path(jobs[job_id]["uploaded_files"][0]).stem
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{stem}_vxh_results.zip"'},
    )


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_index():
    html = _INDEX_HTML.read_text()
    html = html.replace("__ROOT_PATH__", ROOT_PATH)
    html = html.replace("__VERSION__", APP_VERSION)
    return HTMLResponse(html)


# Serve static files last so API routes take priority
app.mount(
    "/",
    StaticFiles(directory=str(Path(__file__).parent / "static"), html=True),
    name="static",
)
