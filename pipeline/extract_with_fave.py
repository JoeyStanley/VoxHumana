import os
import shutil
import subprocess
from pathlib import Path

import tgt

# FAVE-extract (https://github.com/JoFrhwld/FAVE) lives in its own venv, not
# the main project venv: it pins numpy <2.0, which cannot coexist with the
# numpy >=2.0 that new-fave/librosa/openai-whisper resolve to under this
# project's Python >=3.13 requirement. Unlike MFA it has no compiled/
# non-Python dependency, so a plain venv is enough -- no conda needed.
#
# Setup (see TODO_for_server.md):
#   uv venv --python 3.10 .venv-fave
#   uv pip install --python .venv-fave/bin/python \
#       "git+https://github.com/JoFrhwld/FAVE@v2.0.3" "setuptools<81"
#
# Two version-specific pins are required, both confirmed by direct testing,
# not just reading the docs:
#   - Python 3.10, not the project's normal 3.13 (or even 3.11): FAVE-extract
#     still opens files with the Python 2-era 'rU' mode in several places,
#     which was removed entirely in Python 3.11.
#   - setuptools<81: FAVE-extract uses the now-deprecated pkg_resources API
#     to locate its bundled Praat scripts; setuptools 81+ dropped it.
DEFAULT_VENV_PYTHON = Path(__file__).resolve().parent.parent / ".venv-fave" / "bin" / "python"

_SEX_ALIASES = {"m": "m", "male": "m", "f": "f", "female": "f"}


def _find_mfa_textgrid(mfa_output_dir: Path) -> Path:
    """Find the single MFA-produced TextGrid in mfa_output_dir.

    Mirrors the lookup in pipeline.extract_with_newfave.extract_with_newfave --
    both extractors read the same MFA output, just via different tools.
    """
    textgrid_files = sorted(mfa_output_dir.glob("*.TextGrid"))
    if not textgrid_files:
        raise FileNotFoundError(
            f"No MFA TextGrid found in {mfa_output_dir}. "
            "Make sure align_with_mfa() completed successfully."
        )
    if len(textgrid_files) > 1:
        names = ", ".join(f.name for f in textgrid_files)
        raise RuntimeError(
            f"Expected one TextGrid in {mfa_output_dir} but found {len(textgrid_files)}: {names}. "
            "VoxHumana processes one speaker per job."
        )
    return textgrid_files[0]


def _write_speaker_file(path: Path, config: dict) -> None:
    """Write a FAVE-extract *.speaker file.

    Speaker sex is not optional metadata here: FAVE-extract's default
    'mahalanobis' formant-prediction method (the classic FAVE/DARLA
    algorithm) uses it to pick the sex-appropriate maximum-formant ceiling
    and sex-specific covariance/means priors, and hard-fails without it.
    """
    sex = str(config.get("sex", "")).lower()
    if sex not in _SEX_ALIASES:
        raise ValueError(
            "FAVE-extract requires a speaker sex ('m' or 'f') -- "
            "pass config['sex']. It's used by the default 'mahalanobis' "
            "formant-prediction method to select sex-specific measurement priors."
        )
    lines = ["--sex", _SEX_ALIASES[sex]]
    for flag, key in (("--name", "name"),):
        value = config.get(key)
        if value:
            lines += [flag, str(value)]
    path.write_text("\n".join(lines) + "\n")


def _resolve_praat_binary(config: dict) -> str:
    praat_path = config.get("praat_path")
    if praat_path:
        return praat_path
    found = shutil.which("praat") or shutil.which("Praat") or shutil.which("praatcon")
    if not found:
        raise RuntimeError(
            "Could not find a 'praat' executable on PATH. Install Praat "
            "(https://www.praat.org) or set config['praat_path'] (e.g. "
            "'/Applications/Praat.app/Contents/MacOS/praat' on macOS)."
        )
    return found


def _make_praat_run_shim(real_praat: str, shim_dir: Path) -> None:
    """Work around a FAVE-extract/modern-Praat incompatibility.

    FAVE-extract shells out to Praat with plain os.system() calls that don't
    pass --run. Confirmed directly (`praat --help`): "--run is superfluous
    ... interactively, but necessary if you call Praat programmatically."
    Without it, this Praat version opens the script in its GUI editor
    instead of running it, and misreads the script's own positional
    arguments (e.g. treats the vowel count as a filename to read). Since
    PRAATNAME/PRAATPATH are resolved via shutil.which('praat'), putting a
    tiny wrapper named 'praat' ahead of the real binary on PATH lets FAVE's
    unmodified os.system() calls pick it up transparently.
    """
    shim_dir.mkdir(parents=True, exist_ok=True)
    shim_path = shim_dir / "praat"
    shim_path.write_text(f'#!/bin/sh\nexec "{real_praat}" --run "$@"\n')
    shim_path.chmod(0o755)


def _resolve_fave_scripts_home(venv_python: Path) -> Path:
    """Locate the installed fave package's praatScripts/ directory.

    FAVE-extract writes per-vowel scratch audio/formant files into this
    directory using bare filenames, and its Praat invocations resolve those
    bare filenames relative to the *process's* working directory (a leftover
    assumption from being run out of a source checkout, per its own comment
    "all temp files are in the '/bin' directory!"). So the subprocess must be
    run with this directory as its cwd, or Praat can't find the files sox
    just wrote. This also means FAVE-extract jobs can't run concurrently
    against the same venv install (shared scratch dir, not job-specific) --
    fine here, since VoxHumana's job queue only ever runs one job at a time.
    """
    result = subprocess.run(
        [str(venv_python), "-c",
         "import fave, os; print(os.path.join(os.path.dirname(fave.__file__), 'praatScripts'))"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(
            f"Could not locate fave's praatScripts/ directory via {venv_python}: {result.stderr}"
        )
    scripts_home = Path(result.stdout.strip().splitlines()[-1])
    if not scripts_home.is_dir():
        raise RuntimeError(f"fave's praatScripts/ directory not found at {scripts_home}")
    return scripts_home


def get_fave_version(config=None) -> str:
    """Return the installed FAVE-extract version, or 'unknown' if unavailable."""
    config = config or {}
    venv_python = Path(config.get("venv_python", DEFAULT_VENV_PYTHON))
    if not venv_python.exists():
        return "unknown"
    result = subprocess.run(
        [str(venv_python), "-c",
         "import importlib.metadata as m; print(m.version('fave'))"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return "unknown"
    return result.stdout.strip().splitlines()[-1]


def extract_with_fave(audio_path, mfa_output_dir, job_dir, config=None):
    """
    Run FAVE-extract (the classic FAVE/DARLA vowel-formant algorithm) on an
    MFA-aligned audio file, as a legacy alternative to new-fave.

    Takes the original audio and the TextGrid produced by MFA, and writes
    results to <job_dir>/fave_output/. Requires a separate FAVE-extract venv
    (see DEFAULT_VENV_PYTHON / module docstring for setup) and a Praat
    installation.

    Config options:
        sex (str):                     Required. "m"/"male" or "f"/"female" --
                                        needed by the default 'mahalanobis'
                                        formant-prediction method.
        name (str):                     Optional speaker name, recorded in the
                                        output but not required.
        formant_prediction_method (str): "mahalanobis" (default, the classic
                                        FAVE/DARLA algorithm) or "default".
        vowel_system (str):            "NorthAmerican" (default), "phila", or
                                        "simplifiedARPABET".
        remeasurement (bool):          Run FAVE-extract's second measurement
                                        pass using the speaker's own vowel
                                        system as the Mahalanobis baseline.
                                        Default False.
        min_vowel_duration (float):    Passed through as --minVowelDuration.
        n_formants (int):               Passed through as --nFormants.
        venv_python (str):              Path to the FAVE-extract venv's python,
                                        default DEFAULT_VENV_PYTHON.
        praat_path (str):               Explicit path to a praat/praatcon
                                        executable, if not on PATH (e.g. macOS's
                                        Praat.app is not on PATH by default).
        timeout (int):                  Seconds before giving up, default 7200.

    Returns:
        Path to the FAVE-extract output directory.
    """
    if config is None:
        config = {}

    audio_path = Path(audio_path)
    mfa_output_dir = Path(mfa_output_dir)
    job_dir = Path(job_dir)

    textgrid_path = _find_mfa_textgrid(mfa_output_dir)

    # FAVE-extract's --mfa mode has a shortcut for exactly 2 tiers (word,
    # phone) with no speaker-name prefix, which is exactly the shape
    # VoxHumana's own MFA output always is (see extract_with_newfave's
    # matching validation) -- anything else means it isn't a fresh MFA
    # TextGrid (e.g. a previously downloaded VxH result re-uploaded).
    tg = tgt.io.read_textgrid(str(textgrid_path), include_empty_intervals=True)
    n_tiers = len(tg.tiers)
    if n_tiers != 2:
        tier_names = ", ".join(repr(t.name) for t in tg.tiers)
        raise RuntimeError(
            f"Expected exactly 2 tiers (words, then phones) in {textgrid_path.name}, "
            f"found {n_tiers}: {tier_names}. FAVE-extract's --mfa mode pairs a "
            "single speaker's tiers positionally as (word, phone); remove any "
            "extra tier and make sure the first tier is words and the second "
            "is phones before re-uploading."
        )

    output_dir = job_dir / "fave_output"
    output_dir.mkdir(parents=True, exist_ok=True)

    venv_python = Path(config.get("venv_python", DEFAULT_VENV_PYTHON))
    if not venv_python.exists():
        raise RuntimeError(
            f"FAVE-extract's isolated venv python not found at {venv_python}. "
            "See the setup instructions in this module's docstring."
        )

    speaker_file = output_dir / "speaker.speaker"
    _write_speaker_file(speaker_file, config)

    real_praat = _resolve_praat_binary(config)
    shim_dir = output_dir / "_praat_shim"
    _make_praat_run_shim(real_praat, shim_dir)

    fave_scripts_home = _resolve_fave_scripts_home(venv_python)

    stem = audio_path.stem
    output_stem = output_dir / stem

    cmd = [
        str(venv_python), "-m", "fave.extractFormants",
        "--mfa",
        "--speaker", str(speaker_file),
        "--outputFormat", "txt",
        "--formantPredictionMethod", config.get("formant_prediction_method", "mahalanobis"),
        "--vowelSystem", config.get("vowel_system", "NorthAmerican"),
    ]
    if config.get("remeasurement"):
        cmd.append("--remeasurement")
    if config.get("min_vowel_duration") is not None:
        cmd += ["--minVowelDuration", str(config["min_vowel_duration"])]
    if config.get("n_formants") is not None:
        cmd += ["--nFormants", str(config["n_formants"])]
    cmd += [str(audio_path), str(textgrid_path), str(output_stem)]

    env = dict(os.environ)
    env["PATH"] = f"{shim_dir}{os.pathsep}{env.get('PATH', '')}"
    # FAVE-extract's own log ("*.formantlog") shells out to `git diff`/`git
    # describe` from its cwd for reproducibility bookkeeping, and git walks
    # up from cwd looking for a repo. Since fave_scripts_home is nested
    # inside this project's own venv (itself typically inside this git repo),
    # that walk would find *this* project's repo and dump its (possibly
    # uncommitted, possibly sensitive) diff into a file that ships in the
    # user's download. Setting the ceiling to the venv root -- an ancestor of
    # cwd, not cwd itself; GIT_CEILING_DIRECTORIES explicitly does not apply
    # to cwd, confirmed by testing -- stops that walk before it can reach any
    # enclosing repo, regardless of where the venv happens to live.
    env["GIT_CEILING_DIRECTORIES"] = str(venv_python.parent.parent)

    timeout = config.get("timeout", 7200)  # 2 hours default, matches align_with_mfa

    try:
        with subprocess.Popen(
            cmd, cwd=str(fave_scripts_home), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        ) as proc:
            try:
                stdout, _ = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()  # drain pipes so the process exits cleanly
                raise RuntimeError(
                    f"FAVE-extract timed out after {timeout // 60} minutes. "
                    "The recording may be too long. Try splitting it into shorter segments."
                )
    finally:
        shutil.rmtree(shim_dir, ignore_errors=True)
        # This is an input scaffolding file (FAVE-extract's --speaker flag
        # needs a file path, not inline args), not a result -- don't ship it
        # in the user's download; it has no information they didn't already
        # provide on the form.
        speaker_file.unlink(missing_ok=True)

    if proc.returncode != 0:
        raise RuntimeError(f"FAVE-extract failed (exit code {proc.returncode}).\nOutput:\n{stdout}")

    result_file = output_dir / f"{stem}.txt"
    if not result_file.exists():
        raise RuntimeError(
            f"FAVE-extract exited successfully but no output file was found at "
            f"{result_file}.\nOutput:\n{stdout}"
        )

    return output_dir
