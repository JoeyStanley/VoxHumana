"""VoxHumana web server — FastAPI app that wraps the processing pipeline."""

import io
import re
import shutil
import uuid
import zipfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.transcribe_with_whisper import transcribe
from pipeline.convert_whisper_to_textgrid import convert_whisper_to_textgrid
from pipeline.align_with_mfa import align_with_mfa
from pipeline.extract_with_newfave import extract_with_newfave

app = FastAPI(title="VoxHumana")

MAX_UPLOAD_BYTES = 1024 * 1024 * 1024  # 1 GB

BASE_DIR = Path(__file__).parent.parent
JOBS_DIR = BASE_DIR / "data" / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)

# In-memory job store. Fine for a single-server deployment.
jobs: dict[str, dict] = {}

# One job at a time — the pipeline is compute-heavy.
executor = ThreadPoolExecutor(max_workers=1)


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


def _run_pipeline(job_id: str, audio_path: Path, config: dict) -> None:
    """Run all pipeline steps in a background thread, updating job status as we go."""
    job_dir = JOBS_DIR / job_id
    try:
        jobs[job_id].update(step=1, step_name="Transcribing with Whisper")
        whisper_result = transcribe(
            str(audio_path), str(job_dir), config.get("whisper")
        )

        jobs[job_id].update(step=2, step_name="Converting transcript to TextGrid")
        convert_whisper_to_textgrid(whisper_result, str(audio_path), str(job_dir))

        jobs[job_id].update(step=3, step_name="Aligning with MFA")
        mfa_output_dir = align_with_mfa(
            str(audio_path), whisper_result, str(job_dir), config.get("mfa")
        )

        jobs[job_id].update(step=4, step_name="Extracting vowel formants with new-fave")
        extract_with_newfave(
            str(audio_path), mfa_output_dir, str(job_dir), config.get("newfave")
        )

        jobs[job_id].update(status="done", step=5, step_name="Complete")

    except Exception as exc:
        # Write the full traceback to disk for debugging; never send it to the client.
        (JOBS_DIR / job_id / "error.log").write_text(traceback.format_exc())
        jobs[job_id].update(
            status="error",
            error=str(exc),
        )
    finally:
        _cleanup_intermediates(job_dir, audio_path)


@app.post("/api/jobs")
async def create_job(
    audio: UploadFile = File(...),
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
):
    job_id = str(uuid.uuid4())
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
    }

    jobs[job_id] = {
        "status": "running",
        "step": 0,
        "step_name": "Queued",
        "error": None,
        "uploaded_files": [original_name],
        "audio_filename": audio_path.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    executor.submit(_run_pipeline, job_id, audio_path, config)

    return JSONResponse({"job_id": job_id})


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse(jobs[job_id])


@app.get("/api/jobs/{job_id}/download")
async def download_results(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
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
