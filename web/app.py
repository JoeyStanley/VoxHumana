"""VoxHumana web server — FastAPI app that wraps the processing pipeline."""

import io
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


def _cleanup_intermediates(job_dir: Path) -> None:
    """Delete large files that are no longer needed once the pipeline finishes."""
    for f in job_dir.glob("audio.*"):
        f.unlink(missing_ok=True)
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
        _cleanup_intermediates(job_dir)


@app.post("/api/jobs")
async def create_job(
    audio: UploadFile = File(...),
    whisper_model: str = Form("turbo"),
    language: Optional[str] = Form(None),
    initial_prompt: Optional[str] = Form(None),
    condition_on_previous_text: bool = Form(True),
    acoustic_model: str = Form("english_us_arpa"),
    dictionary: str = Form("english_us_arpa"),
):
    job_id = str(uuid.uuid4())
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    suffix = Path(audio.filename or "audio.wav").suffix or ".wav"
    audio_path = job_dir / f"audio{suffix}"

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
        },
        "newfave": {},
    }

    jobs[job_id] = {
        "status": "running",
        "step": 0,
        "step_name": "Queued",
        "error": None,
        "uploaded_files": [audio.filename or "audio.wav"],
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
    buf = io.BytesIO()

    # Zip all output files except the mfa_corpus dir (just a copy of the input audio)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in job_dir.rglob("*"):
            if f.is_file() and "mfa_corpus" not in f.parts and "mfa_temp" not in f.parts:
                # Skip the raw audio copy we saved server-side
                if f.name.startswith("audio."):
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
