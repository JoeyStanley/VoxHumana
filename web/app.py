"""VoxHumana web server — FastAPI app that wraps the processing pipeline."""

import importlib.metadata
import io
import json
import re
import shutil
import subprocess
import random
import zipfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import uuid

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.transcribe_with_whisper import transcribe
from pipeline.convert_whisper_to_textgrid import convert_whisper_to_textgrid
from pipeline.align_with_mfa import align_with_mfa
from pipeline.extract_with_newfave import extract_with_newfave

app = FastAPI(title="VoxHumana")

MAX_UPLOAD_BYTES = 1024 * 1024 * 1024  # 1 GB
JOB_RETENTION_HOURS = 72

# Languages for which new-fave formant extraction is supported.
# new-fave requires CMU/ARPABET phonemes produced by English MFA models,
# so formant extraction is skipped automatically for any other language.
FORMANT_SUPPORTED_LANGUAGES = {"en"}

# Allowlists for MFA model/dictionary names — these values are passed
# directly to the MFA CLI, so we validate them server-side to prevent
# unexpected inputs. Expand as new language models are added.
SUPPORTED_MFA_ACOUSTIC_MODELS = {"english_us_arpa", "spanish_mfa"}
SUPPORTED_MFA_DICTIONARIES    = {"english_us_arpa", "spanish_mfa"}

BASE_DIR = Path(__file__).parent.parent
JOBS_DIR = BASE_DIR / "data" / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR = BASE_DIR / "data" / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# In-memory job store. Fine for a single-server deployment.
jobs: dict[str, dict] = {}

# One job at a time — the pipeline is compute-heavy.
executor = ThreadPoolExecutor(max_workers=1)


# Organ stops drawn from the Salt Lake Tabernacle organ — used to generate
# memorable job IDs in the form YYMMDD_Stop1_Stop2.
_ORGAN_STOPS = [
    "Bombarde", "Bourdon", "Celeste", "Clarinet", "Clarion", "CorAnglais",
    "Cornopean", "Cymbelstern", "Diaphone", "Diapason", "Doppelflote",
    "Dulciana", "Flugelhorn", "Flute", "FrenchHorn", "Fugara", "Gamba",
    "Gemshorn", "Harp", "LieblichBourdon", "Mixture", "Nachthorn", "Nazard",
    "Oboe", "Octave", "Piccolo", "Principal", "Rauschquinte", "Trombone",
    "Trompette", "Tremulant", "Trumpet", "Tuba", "Tutti", "Viole",
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


def _sanitize_stem(filename: str) -> str:
    """Return a filesystem-safe stem from an uploaded filename, capped at 40 chars."""
    stem = Path(filename).stem
    stem = re.sub(r'[^\w\-]', '', stem)   # keep alphanumeric, underscore, hyphen
    stem = re.sub(r'_+', '_', stem).strip('_')
    return stem[:40] or "audio"


def _cleanup_intermediates(job_dir: Path, audio_path: Path) -> None:
    """Delete large files that are no longer needed once the pipeline finishes."""
    audio_path.unlink(missing_ok=True)
    for dirname in ("mfa_corpus", "mfa_temp"):
        d = job_dir / dirname
        if d.exists():
            shutil.rmtree(d)


_AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aiff", ".aif"}


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
    conda_env = config.get("mfa", {}).get("conda_env", "aligner")
    mfa_ver = _get_mfa_version(conda_env)

    # Output file list (excludes audio, working dirs, and the log itself)
    output_files = sorted(
        str(f.relative_to(job_dir))
        for f in job_dir.rglob("*")
        if f.is_file()
        and "mfa_corpus" not in f.parts
        and "mfa_temp" not in f.parts
        and f.name != audio_filename
        and f.name != "processing_log.txt"
    )

    stem = Path(audio_filename).stem
    w_cfg  = config.get("whisper", {})
    m_cfg  = config.get("mfa", {})
    nf_cfg = config.get("newfave", {})
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
    ln(f"Submitted: {submitted_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    ln(f"Completed: {completed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    ln(f"Duration:  {duration_str}")

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
        model = w_cfg.get("model", "turbo")
        language = w_cfg.get("language") or None
        initial_prompt = w_cfg.get("initial_prompt") or None
        copt = w_cfg.get("condition_on_previous_text", True)

        ln(f"STEP 1 — TRANSCRIPTION: OpenAI Whisper  v{whisper_ver}")
        ln(BAR)
        ln("")
        ln("Whisper is an automatic speech recognition model that converts audio to")
        ln("text with segment-level timestamps. VoxHumana saves three outputs:")
        ln("  • a .json file with full segment detail (timings, detected language)")
        ln("  • a .txt file with the plain-text transcript")
        ln("  • a Praat TextGrid (.TextGrid), created with the TextGridTools package")
        ln("      in Python, with one interval per segment")
        ln("This TextGrid is the input to MFA alignment Step 2.")
        ln("")
        ln("Parameters:")
        ln(f"  - model:                       {model}{dflt(model, 'turbo')}")
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
        if ran_alignment:
            ln("Whisper transcription was not run. A Praat utterance TextGrid was")
            ln("uploaded by the user and used as the transcript input to MFA.")
        else:
            ln("Whisper transcription was not run. A Praat MFA-format TextGrid")
            ln("(Word and Phone tiers) was uploaded by the user and used directly")
            ln("as the input to new-fave formant extraction.")

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
        tg_source = "user-supplied TextGrid" if not ran_transcription else "Whisper/TextGridTools-generated TextGrid"

        ln(f"STEP 2 — FORCED ALIGNMENT: Montreal Forced Aligner (MFA)  v{mfa_ver}")
        ln(BAR)
        ln("")
        ln(f"MFA takes the audio and the {tg_source} and")
        ln("produces another Praat TextGrid with word- and phone-level time-aligned")
        ln("intervals. This TextGrid is the primary input to new-fave in Step 3.")
        ln("")
        ln("If any words in the transcript were not found in the pronunciation dictionary,")
        ln("MFA guessed their pronunciations. Those words are listed in oovs_found.txt")
        ln("(included in your download if any were found). Poor guesses can degrade")
        ln("alignment quality around those words. To improve results, paste the OOV words")
        ln("into the Transcription Hint box and rerun — Whisper will have more context and")
        ln("is less likely to misspell or misrecognize them.")
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
        ln(f"    mfa align corpus_dir/ \\")
        ln(f"             {dictionary} \\")
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
            ln("directly as the input to new-fave formant extraction.")
        else:
            ln("MFA alignment was not run for this job.")

    # ── new-fave ──────────────────────────────────────────────────────────────
    ln("")
    ln("")
    ln(BAR)
    if ran_formants:
        speakers = nf_cfg.get("speakers", "all")
        recode_rules = nf_cfg.get("recode_rules", "cmu2labov")
        labelset_parser = nf_cfg.get("labelset_parser", "cmu_parser")
        point_heuristic = nf_cfg.get("point_heuristic", "fave")
        formant_ceiling = nf_cfg.get("formant_ceiling")
        num_formants = nf_cfg.get("num_formants")
        include_overlaps = nf_cfg.get("include_overlaps", True)

        has_ft_override = formant_ceiling is not None or num_formants is not None
        ft_display = "custom YAML (see formant_ceiling / num_formants below)" if has_ft_override else "default"
        fc_str = str(formant_ceiling) if formant_ceiling is not None else "(not set — new-fave default applies)"
        nf_str = str(num_formants) if num_formants is not None else "(not set — new-fave default applies)"

        ln(f"STEP 3 — VOWEL FORMANT EXTRACTION: new-fave  v{newfave_ver}")
        ln(BAR)
        ln("")
        ln("new-fave locates vowel tokens in the MFA-aligned TextGrid, estimates")
        ln("formant trajectories across each vowel using FastTrack, and applies the")
        ln("FAVE point-measurement heuristic to pick a single representative F1/F2")
        ln("value per token. Phonetic labels are recoded from CMU ARPABET to Labov")
        ln("vowel-class notation. Five output files are written:")
        ln("")
        ln("  *_points.csv       — one row per vowel token (single-point measurement)")
        ln("  *_tracks.csv       — formant trajectories (multiple time points per token)")
        ln("  *_param.csv        — DCT coefficients of the formant tracks (Hz scale)")
        ln("  *_logparam.csv     — DCT coefficients of the formant tracks (log Hz scale)")
        ln("  *_recoded.TextGrid — Praat TextGrid with Labov vowel-class labels applied")
        if has_ft_override:
            ln("  ft_config.yml      — FastTrack parameter overrides used for this run;")
            ln("                       only needed if you want to rerun the extraction offline")
        ln("")
        ln("Parameters:")
        ln(f"  - speakers:           {speakers}{dflt(speakers, 'all')}")
        ln(f"  - recode_rules:       {recode_rules}{dflt(recode_rules, 'cmu2labov')}")
        ln(f"  - labelset_parser:    {labelset_parser}{dflt(labelset_parser, 'cmu_parser')}")
        ln(f"  - point_heuristic:    {point_heuristic}{dflt(point_heuristic, 'fave')}")
        ln(f"  - ft_config:          {ft_display}{dflt(ft_display, 'default')}")
        ln(f"  - formant_ceiling:    {fc_str}")
        ln(f"  - num_formants:       {nf_str}")
        ln(f"  - include_overlaps:   {include_overlaps}{dflt(include_overlaps, True)}")
        ln("")
        ln("To replicate what VoxHumana did offline in a Python script:")
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

        ln(f"    speakers = fave_audio_textgrid(")
        ln(f"        {audio_filename!r},")
        ln(f"        'mfa_output/{stem}.TextGrid',")
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
    audio_filename: str,
    submitted_at: datetime,
    completed_at: datetime,
    step_times: list,
    failed_step: str | None,
    error_tb: str | None,
    config: dict,
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
    mfa_ver = _get_mfa_version(m_cfg.get("conda_env", "aligner"))

    total_seconds = (completed_at - submitted_at).total_seconds()
    status_line = "SUCCESS" if failed_step is None else f"FAILED at \"{failed_step}\""

    BAR = "=" * 60
    lines = []
    ln = lines.append

    ln("VoxHumana Server Log")
    ln(BAR)
    ln(f"Job ID:       {job_id}")
    ln(f"File:         {audio_filename}")
    ln(f"Submitted:    {submitted_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    ln(f"Completed:    {completed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    ln(f"Total time:   {_fmt_duration(total_seconds)}")
    ln(f"Status:       {status_line}")
    ln("")
    ln("Step timings:")
    for name, secs in step_times:
        ln(f"  {name:<38} {_fmt_duration(secs)}")
    ln("")
    ln("Tool versions:")
    ln(f"  openai-whisper:  {whisper_ver}")
    ln(f"  MFA:             {mfa_ver}")
    ln(f"  new-fave:        {newfave_ver}")
    ln("")
    s_cfg = config.get("steps", {})
    ln("Steps run:")
    ln(f"  transcription:  {s_cfg.get('transcription', True)}")
    ln(f"  alignment:      {s_cfg.get('alignment', True)}")
    ln(f"  formants:       {s_cfg.get('formants', True)}")
    ln("")
    ln("Settings:")
    ln(f"  [Whisper]")
    ln(f"  model:                    {w_cfg.get('model', 'turbo')}")
    ln(f"  language:                 {w_cfg.get('language') or '(auto-detect)'}")
    ln(f"  initial_prompt:           {w_cfg.get('initial_prompt') or '(none)'}")
    ln(f"  condition_on_prev_text:   {w_cfg.get('condition_on_previous_text', True)}")
    ln(f"  [MFA]")
    ln(f"  acoustic_model:           {m_cfg.get('acoustic_model', 'english_us_arpa')}")
    ln(f"  dictionary:               {m_cfg.get('dictionary', 'english_us_arpa')}")
    ln(f"  fine_tune:                {m_cfg.get('fine_tune', False)}")
    ln(f"  num_jobs:                 {m_cfg.get('num_jobs', 1)}")
    ln(f"  [new-fave]")
    ln(f"  speakers:                 {nf_cfg.get('speakers', 'all')}")
    ln(f"  recode_rules:             {nf_cfg.get('recode_rules', 'cmu2labov')}")
    ln(f"  labelset_parser:          {nf_cfg.get('labelset_parser', 'cmu_parser')}")
    ln(f"  point_heuristic:          {nf_cfg.get('point_heuristic', 'fave')}")
    ln(f"  formant_ceiling:          {nf_cfg.get('formant_ceiling') or '(default)'}")
    ln(f"  num_formants:             {nf_cfg.get('num_formants') or '(default)'}")
    ln(f"  include_overlaps:         {nf_cfg.get('include_overlaps', True)}")

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
        "status":                    "success" if failed_step is None else "failed",
        "failed_step":               failed_step,
        "error_type":                error_type,
        "whisper_model":             w_cfg.get("model", "turbo"),
        "language":                  w_cfg.get("language"),
        "initial_prompt_used":       bool(w_cfg.get("initial_prompt")),
        "condition_on_previous_text": w_cfg.get("condition_on_previous_text", True),
        "mfa_acoustic_model":        m_cfg.get("acoustic_model", "english_us_arpa"),
        "mfa_dictionary":            m_cfg.get("dictionary", "english_us_arpa"),
        "mfa_fine_tune":             m_cfg.get("fine_tune", False),
        "formant_ceiling":           nf_cfg.get("formant_ceiling"),
        "num_formants":              nf_cfg.get("num_formants"),
        "include_overlaps":          nf_cfg.get("include_overlaps", True),
        "step_seconds":              step_seconds,
        "versions": {
            "openai-whisper": whisper_ver,
            "mfa":            mfa_ver,
            "new-fave":       newfave_ver,
        },
    }
    with open(LOGS_DIR / "summary.jsonl", "a") as f:
        f.write(json.dumps(summary) + "\n")


def _run_pipeline(job_id: str, audio_path: Path, config: dict) -> None:
    """Run pipeline steps in a background thread, honoring the steps config."""
    job_dir = JOBS_DIR / job_id
    submitted_at = datetime.fromisoformat(jobs[job_id]["created_at"])
    step_times: list[tuple[str, float]] = []
    current_step: str | None = None
    error_tb: str | None = None

    steps = config.get("steps", {})
    run_transcription = steps.get("transcription", True)
    run_alignment     = steps.get("alignment", True)
    run_formants      = steps.get("formants", True)

    # Always defined so new-fave can find the TextGrid even when alignment was skipped.
    mfa_output_dir = job_dir / "mfa_output"

    try:
        if run_transcription:
            current_step = "Step 1 – Transcribing with Whisper"
            jobs[job_id].update(step=1, step_name="Transcribing with Whisper")
            _t = datetime.now(timezone.utc)
            whisper_result = transcribe(str(audio_path), str(job_dir), config.get("whisper"))
            step_times.append((current_step, (datetime.now(timezone.utc) - _t).total_seconds()))

            current_step = "Step 2 – Converting transcript to TextGrid"
            jobs[job_id].update(step=2, step_name="Converting transcript to TextGrid")
            _t = datetime.now(timezone.utc)
            convert_whisper_to_textgrid(whisper_result, str(audio_path), str(job_dir))
            step_times.append((current_step, (datetime.now(timezone.utc) - _t).total_seconds()))

        if run_alignment:
            current_step = "Step 3 – Aligning with MFA"
            jobs[job_id].update(step=3, step_name="Aligning with MFA")
            _t = datetime.now(timezone.utc)
            mfa_output_dir = align_with_mfa(str(audio_path), str(job_dir), config.get("mfa"))
            step_times.append((current_step, (datetime.now(timezone.utc) - _t).total_seconds()))
            # If the user supplied their own TextGrid (Transcribe skipped), remove the
            # whisper_output/ staging folder — it only contained their uploaded file.
            if not run_transcription:
                shutil.rmtree(job_dir / "whisper_output", ignore_errors=True)

        if run_formants:
            current_step = "Step 4 – Extracting vowel formants with new-fave"
            jobs[job_id].update(step=4, step_name="Extracting vowel formants with new-fave")
            _t = datetime.now(timezone.utc)
            extract_with_newfave(str(audio_path), mfa_output_dir, str(job_dir), config.get("newfave"))
            step_times.append((current_step, (datetime.now(timezone.utc) - _t).total_seconds()))
            # If the user supplied their own MFA TextGrid (Align skipped), remove the
            # mfa_output/ staging folder — it only contained their uploaded file.
            if not run_alignment:
                shutil.rmtree(job_dir / "mfa_output", ignore_errors=True)

        _write_processing_log(
            job_dir, job_id, config,
            audio_filename=audio_path.name,
            submitted_at=submitted_at,
        )
        jobs[job_id].update(status="done", step=5, step_name="Complete")
        current_step = None  # marks success

    except Exception as exc:
        error_tb = traceback.format_exc()
        # Write the full traceback to disk for debugging; never send it to the client.
        (JOBS_DIR / job_id / "error.log").write_text(error_tb)
        jobs[job_id].update(status="error", error=str(exc))

    finally:
        completed_at = datetime.now(timezone.utc)
        _write_server_log(
            job_id=job_id,
            audio_filename=audio_path.name,
            submitted_at=submitted_at,
            completed_at=completed_at,
            step_times=step_times,
            failed_step=current_step,
            error_tb=error_tb,
            config=config,
        )
        _cleanup_intermediates(job_dir, audio_path)
        _cleanup_orphaned_audio()
        _expire_old_jobs()


@app.post("/api/jobs")
async def create_job(
    audio: UploadFile = File(...),
    textgrid: Optional[UploadFile] = File(None),
    whisper_model: str = Form("turbo"),
    language: Optional[str] = Form(None),
    initial_prompt: Optional[str] = Form(None),
    condition_on_previous_text: bool = Form(True),
    acoustic_model: str = Form("english_us_arpa"),
    dictionary: str = Form("english_us_arpa"),
    fine_tune: bool = Form(False),
    formant_ceiling: Optional[str] = Form(None),
    num_formants: Optional[str] = Form(None),
    include_overlaps: bool = Form(True),
    run_transcription: bool = Form(True),
    run_alignment: bool = Form(True),
    run_formants: bool = Form(True),
):
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

    # Safety net: new-fave formant extraction requires English MFA output
    # (CMU/ARPABET phonemes). If a non-English language arrives with formants
    # enabled — e.g. from a direct API call bypassing the UI guard — quietly
    # disable the formant step rather than letting the pipeline fail mid-run.
    if language and language not in FORMANT_SUPPORTED_LANGUAGES and run_formants:
        run_formants = False

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
        },
        "newfave": {
            "formant_ceiling": int(formant_ceiling) if formant_ceiling else None,
            "num_formants": int(num_formants) if num_formants else None,
            "include_overlaps": include_overlaps,
        },
        "steps": {
            "transcription": run_transcription,
            "alignment":     run_alignment,
            "formants":      run_formants,
        },
    }

    download_token = uuid.uuid4().hex

    jobs[job_id] = {
        "status": "running",
        "step": 0,
        "step_name": "Queued",
        "error": None,
        "uploaded_files": uploaded_files,
        "audio_filename": audio_path.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "download_token": download_token,
    }

    executor.submit(_run_pipeline, job_id, audio_path, config)

    return JSONResponse({"job_id": job_id, "download_token": download_token})


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse(jobs[job_id])


@app.get("/api/jobs/{job_id}/download")
async def download_results(job_id: str, token: str = ""):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    if token != jobs[job_id].get("download_token", ""):
        raise HTTPException(status_code=403, detail="Invalid download token")
    if jobs[job_id]["status"] != "done":
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


# Serve static files last so API routes take priority
app.mount(
    "/",
    StaticFiles(directory=str(Path(__file__).parent / "static"), html=True),
    name="static",
)
