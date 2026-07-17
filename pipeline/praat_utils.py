import os
import platform
import shutil
import subprocess
from pathlib import Path

# Common install locations for the Praat.app bundle on macOS, where the
# `praat` binary usually isn't on PATH. Checked only if `praat`/`Praat`
# isn't found on PATH first.
_MAC_APP_PATHS = [
    Path("/Applications/Praat.app/Contents/MacOS/Praat"),
    Path.home() / "Applications/Praat.app/Contents/MacOS/Praat",
]


def find_praat_executable(config=None):
    """Locate the Praat executable.

    Checks, in order: an explicit config["praat_path"], the VXH_PRAAT_PATH
    env var, PATH, and (on macOS) the default Praat.app install locations.
    """
    config = config or {}
    override = config.get("praat_path") or os.environ.get("VXH_PRAAT_PATH")
    if override:
        return override

    on_path = shutil.which("praat") or shutil.which("Praat")
    if on_path:
        return on_path

    if platform.system() == "Darwin":
        for candidate in _MAC_APP_PATHS:
            if candidate.exists():
                return str(candidate)

    raise FileNotFoundError(
        "Could not find the Praat executable. Install Praat "
        "(https://www.fon.hum.uva.nl/praat/) so it's on PATH, or set the "
        "VXH_PRAAT_PATH environment variable to its location."
    )


def run_praat_script(script_path, args, config=None, timeout=120):
    """Run a Praat script headlessly with positional arguments filling its form.

    Praat resolves relative paths in a script (e.g. "Read from file") against
    the script file's own directory, not the process's working directory —
    so scripts under pipeline/praat/ take the job directory as their own
    form argument and build paths from it, rather than relying on cwd.
    """
    praat = find_praat_executable(config)
    cmd = [praat, "--run", str(script_path), *[str(a) for a in args]]

    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
    )

    if proc.returncode != 0:
        raise RuntimeError(
            f"Praat script {Path(script_path).name} failed (exit code {proc.returncode}).\n"
            f"STDOUT:\n{proc.stdout}\n"
            f"STDERR:\n{proc.stderr}"
        )

    return proc
