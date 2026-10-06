"""Run one pipeline step in a child process that can be killed mid-step.

Whisper and new-fave run as ordinary Python calls, and Python can't stop a
thread partway through -- so to let users and the admin cancel a running job
(including a multi-hour Whisper run), each heavy step runs in its own child
process instead. Side benefit: the web server's own process stays free to
handle uploads and status polls while a job is crunching.

The child calls os.setsid() first, so it leads its own process group, and
kill() signals the whole group -- that also catches MFA (launched through
`conda run`) and the worker processes new-fave starts via joblib.

Kept separate from web/app.py on purpose: the spawned child imports this
module to find _child_entry, and importing web.app there would re-run the
whole server setup in every child.
"""

import importlib
import multiprocessing
import os
import signal
import threading
import traceback

from pipeline.errors import UserFacingError

# "spawn" (not fork): forking a process that has torch loaded and other
# threads running isn't safe; spawn starts a clean interpreter.
_ctx = multiprocessing.get_context("spawn")

KILL_GRACE_SECONDS = 5


class StepKilled(Exception):
    """The step's process was killed (cancel or shutdown) before it finished."""


def _child_entry(conn, func_ref, args, kwargs, env):
    os.setsid()
    # Set env (e.g. thread limits) before importing the step's module: numpy,
    # torch, and joblib read their thread settings when first imported.
    os.environ.update(env)
    try:
        module_name, func_name = func_ref
        func = getattr(importlib.import_module(module_name), func_name)
        payload = ("ok", func(*args, **kwargs), None)
    except BaseException as exc:
        payload = ("error", exc, traceback.format_exc())
    try:
        conn.send(payload)
    except Exception:
        # Result or exception couldn't be pickled — send a plain stand-in.
        status, value, tb = payload
        conn.send(("error", RuntimeError(f"{type(value).__name__}: {value}"), tb))
    finally:
        conn.close()


class StepProcess:
    """One step function, run in a child process: start(), then wait() or kill().

    `func` must be a module-level function. It's passed to the child by name
    (not pickled) so its module is only imported after `env` is applied.
    """

    def __init__(self, func, *args, env: dict | None = None, **kwargs):
        self._func_ref = (func.__module__, func.__qualname__)
        self._args, self._kwargs = args, kwargs
        self._env = {k: str(v) for k, v in (env or {}).items()}
        self._proc = None
        self._conn = None
        self._killed = False

    def start(self) -> None:
        parent_conn, child_conn = _ctx.Pipe(duplex=False)
        # Not daemonic: new-fave's joblib workers are child processes of this
        # one, and daemonic processes aren't allowed children. The app kills
        # running steps itself on shutdown instead (see app.py).
        self._proc = _ctx.Process(
            target=_child_entry,
            args=(child_conn, self._func_ref, self._args, self._kwargs, self._env),
        )
        self._proc.start()
        child_conn.close()  # so recv() sees EOF if the child dies
        self._conn = parent_conn

    def wait(self):
        """Block until the step finishes; return its result or re-raise its error."""
        try:
            msg = self._conn.recv()
        except EOFError:
            msg = None  # child exited (or was killed) without reporting back
        finally:
            self._conn.close()
        self._proc.join()
        if self._killed:
            raise StepKilled()
        if msg is None:
            raise UserFacingError(
                f"This step stopped unexpectedly (exit code {self._proc.exitcode}). "
                "The server may have run out of memory."
            )
        status, value, tb = msg
        if status == "ok":
            return value
        if tb:
            value.add_note("Traceback from the step's process:\n" + tb)
        raise value

    def kill(self) -> None:
        """Stop the step and everything it launched: SIGTERM, then SIGKILL if needed."""
        self._killed = True
        if self._proc is None or self._proc.pid is None:
            return
        self._signal(signal.SIGTERM)
        timer = threading.Timer(KILL_GRACE_SECONDS, self._signal, args=(signal.SIGKILL,))
        timer.daemon = True
        timer.start()

    def _signal(self, sig) -> None:
        # Signal the group even if the child itself has exited: anything it
        # launched (MFA, joblib workers) may still be in that group.
        try:
            os.killpg(self._proc.pid, sig)
            return
        except (ProcessLookupError, PermissionError):
            pass
        # No such group: either everything is already gone, or the child was
        # killed before its setsid() ran (so it hasn't launched anything yet)
        # — signal the child alone, if it's still running.
        if self._proc.exitcode is None:
            try:
                os.kill(self._proc.pid, sig)
            except ProcessLookupError:
                pass
