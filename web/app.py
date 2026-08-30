"""VoxHumana web server — FastAPI app that wraps the processing pipeline."""

import importlib.metadata
import io
import json
import os
import re
import shutil
import subprocess
import random
import tempfile
import tomllib
import zipfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
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
from pipeline.align_with_mfa import align_with_mfa
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
from pipeline.languages import (
    NEWFAVE_LANGUAGE_PRESETS,
    SUPPORTED_MFA_ACOUSTIC_MODELS,
    SUPPORTED_MFA_DICTIONARIES,
)

# Strip any trailing slash so ROOT_PATH + "/api/..." is always well-formed.
# Set VXH_ROOT_PATH="" (or omit it) for a root-path deployment.
# Set VXH_ROOT_PATH="/VoxHumana" when deployed under a sub-path.
ROOT_PATH = os.environ.get("VXH_ROOT_PATH", "").rstrip("/")

_INDEX_HTML = Path(__file__).parent / "static" / "index.html"
_PYPROJECT_TOML = Path(__file__).parent.parent / "pyproject.toml"

app = FastAPI(title="VoxHumana")


def _get_app_version() -> str:
    """Return VoxHumana's own version, read straight from pyproject.toml.

    Installed package metadata (importlib.metadata) can go stale here since
    the editable install isn't reinstalled on every bump-my-version bump, so
    read the source of truth directly instead.
    """
    with _PYPROJECT_TOML.open("rb") as f:
        return tomllib.load(f)["project"]["version"]


APP_VERSION = _get_app_version()

MAX_UPLOAD_BYTES = 1024 * 1024 * 1024  # 1 GB
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

# One job at a time — the pipeline is compute-heavy.
executor = ThreadPoolExecutor(max_workers=1)

# Ordered list of job IDs that are queued or currently running (FIFO — the
# executor has exactly one worker thread, so execution order always matches
# this list's order). active_jobs[0] is always the job currently being
# processed. Used to report queue position to waiting jobs.
active_jobs: list[str] = []


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
        ln(f"Queue:     started at position {queue_position} in line — waited "
           f"{_fmt_duration(wait_seconds)} for earlier jobs before processing began")
    else:
        ln("Queue:     no wait — processing began immediately")

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
        ln("alignment quality around those words.")
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
    status_line = "SUCCESS" if failed_step is None else f"FAILED at \"{failed_step}\""
    queue_position = jobs.get(job_id, {}).get("queue_position_at_submission", 1)
    wait_seconds = jobs.get(job_id, {}).get("wait_seconds", 0.0)
    client_ip = job_client_info.get(job_id, {}).get("client_ip", "unknown")
    user_agent = job_client_info.get(job_id, {}).get("user_agent", "")

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
    ln(f"Client IP:    {client_ip}")
    ln(f"User-Agent:   {user_agent or 'unknown'}")
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
    ln(f"  model:                    {w_cfg.get('model', 'turbo')}")
    ln(f"  language:                 {w_cfg.get('language') or '(auto-detect)'}")
    ln(f"  initial_prompt:           {w_cfg.get('initial_prompt') or '(none)'}")
    ln(f"  condition_on_prev_text:   {w_cfg.get('condition_on_previous_text', True)}")
    ln(f"  [MFA]")
    ln(f"  acoustic_model:           {m_cfg.get('acoustic_model', 'english_us_arpa')}")
    ln(f"  dictionary:               {m_cfg.get('dictionary', 'english_us_arpa')}")
    ln(f"  fine_tune:                {m_cfg.get('fine_tune', False)}")
    ln(f"  num_jobs:                 {m_cfg.get('num_jobs', 1)}")
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
        "combine_preliquid":         nf_combine_preliquid,
        "include_intervocalic":      nf_cfg.get("include_intervocalic", True) if nf_combine_preliquid else None,
        "formants_engine":           formants_engine,
        "step_seconds":              step_seconds,
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


def _run_pipeline(job_id: str, audio_path: Path, config: dict) -> None:
    """Run pipeline steps in a background thread, honoring the steps config."""
    job_dir = JOBS_DIR / job_id
    submitted_at = datetime.fromisoformat(jobs[job_id]["created_at"])
    started_at = datetime.now(timezone.utc)
    wait_seconds = (started_at - submitted_at).total_seconds()
    jobs[job_id].update(
        status="running",
        started_at=started_at.isoformat(),
        wait_seconds=round(wait_seconds, 1),
    )
    step_times: list[tuple[str, float]] = []
    current_step: str | None = None
    error_tb: str | None = None

    steps = config.get("steps", {})
    run_transcription = steps.get("transcription", True)
    run_alignment     = steps.get("alignment", True)
    run_formants      = steps.get("formants", True)

    stem = audio_path.stem

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

            current_step = "Step 2b – Generating line-by-line transcript"
            jobs[job_id].update(step=2, step_name="Generating line-by-line transcript")
            _t = datetime.now(timezone.utc)
            generate_transcript(str(job_dir), stem, config.get("praat"))
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
            formants_engine = config.get("formants_engine", "newfave")
            if formants_engine == "fave_extract":
                current_step = "Step 4 – Extracting formants with FAVE-extract"
                jobs[job_id].update(step=4, step_name="Extracting formants with FAVE-extract")
                _t = datetime.now(timezone.utc)
                extract_with_fave(str(audio_path), mfa_output_dir, str(job_dir), config.get("fave_extract"))
                step_times.append((current_step, (datetime.now(timezone.utc) - _t).total_seconds()))
            else:
                current_step = "Step 4 – Extracting vowel formants with new-fave"
                jobs[job_id].update(step=4, step_name="Extracting vowel formants with new-fave")
                _t = datetime.now(timezone.utc)
                extract_with_newfave(str(audio_path), mfa_output_dir, str(job_dir), config.get("newfave"))
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
        if run_transcription and run_alignment:
            current_step = "Step 5 – Adding utterance tier to MFA TextGrid"
            report_step = 4 if run_formants else 3
            jobs[job_id].update(step=report_step, step_name="Adding utterance tier to MFA TextGrid")
            _t = datetime.now(timezone.utc)
            combine_textgrids(str(job_dir), stem, config.get("praat"))
            step_times.append((current_step, (datetime.now(timezone.utc) - _t).total_seconds()))

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
        # Each step below is independently guarded — this function runs on
        # the single background worker thread, so if a step here ever raised
        # (or, before this fix, if an earlier step's raise skipped the
        # active_jobs.remove() below), it wouldn't just corrupt this job's
        # bookkeeping — every job queued behind it would appear stuck.
        # active_jobs.remove() runs first, before best-effort logging/
        # cleanup, so queue position for the rest of the queue is correct
        # even if a cleanup step below fails.
        try:
            active_jobs.remove(job_id)
        except ValueError:
            pass

        completed_at = datetime.now(timezone.utc)
        try:
            _write_server_log(
                job_id=job_id,
                job_dir=job_dir,
                audio_path=audio_path,
                submitted_at=submitted_at,
                completed_at=completed_at,
                step_times=step_times,
                failed_step=current_step,
                error_tb=error_tb,
                config=config,
            )
        except Exception:
            pass
        try:
            _cleanup_intermediates(job_dir, audio_path)
        except Exception:
            pass
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
    combine_preliquid: bool = Form(False),
    include_intervocalic: bool = Form(True),
    formants_engine: str = Form("newfave"),
    fave_sex: Optional[str] = Form(None),
    fave_name: Optional[str] = Form(None),
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

    job_client_info[job_id] = {
        "client_ip": _get_client_ip(request),
        "user_agent": request.headers.get("user-agent", ""),
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
        "queue_position_at_submission": len(active_jobs) + 1,
    }

    active_jobs.append(job_id)
    executor.submit(_run_pipeline, job_id, audio_path, config)

    return JSONResponse({"job_id": job_id, "download_token": download_token})


def _job_status_payload(job_id: str) -> Optional[dict]:
    """Return a job's status dict (with queue position filled in), or None if unknown."""
    if job_id not in jobs:
        return None
    job = dict(jobs[job_id])
    if job["status"] == "queued" and job_id in active_jobs:
        position = active_jobs.index(job_id) + 1  # 1 = up next / currently starting
        total = len(active_jobs)
        job["queue_position"] = position
        job["queue_length"] = total
        job["step_name"] = (
            "Starting…" if position == 1
            else f"Waiting in queue (position {position} of {total})"
        )
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
