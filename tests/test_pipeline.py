"""
Smoke test for the full VoxHumana pipeline.

Run from anywhere:
    python tests/test_pipeline.py

Requires the MFA conda env and models to be set up (see TODO_for_server.md).
"""

import shutil
from pathlib import Path

from pipeline.transcribe_with_whisper import transcribe
from pipeline.convert_whisper_to_textgrid import convert_whisper_to_textgrid
from pipeline.align_with_mfa import align_with_mfa
from pipeline.extract_with_newfave import extract_with_newfave

ROOT     = Path(__file__).parent.parent
AUDIO    = ROOT / "data" / "test1" / "UT007-Aiden.wav"
JOB_DIR  = ROOT / "data" / "test_job"


def run():
    assert AUDIO.exists(), f"Test audio not found: {AUDIO}"

    # Start clean so results can't be stale leftovers from a previous run.
    if JOB_DIR.exists():
        shutil.rmtree(JOB_DIR)
    JOB_DIR.mkdir(parents=True)

    # Step 1: Whisper
    print("Step 1: Transcribing with Whisper...")
    whisper_result = transcribe(str(AUDIO), str(JOB_DIR))
    stem = AUDIO.stem
    assert (JOB_DIR / "whisper" / f"{stem}.json").exists(), f"whisper/{stem}.json not created"
    assert (JOB_DIR / "whisper" / f"{stem}.txt").exists(), f"whisper/{stem}.txt not created"
    assert whisper_result.get("segments"), "Whisper returned no segments"

    # Step 2: TextGrid conversion
    print("Step 2: Converting transcript to TextGrid...")
    tg_path = convert_whisper_to_textgrid(whisper_result, str(AUDIO), str(JOB_DIR))
    assert Path(tg_path).exists(), f"whisper/{stem}.TextGrid not created"

    # Step 3: MFA alignment
    print("Step 3: Aligning with MFA...")
    mfa_output_dir = align_with_mfa(str(AUDIO), str(JOB_DIR))
    mfa_textgrids = list(Path(mfa_output_dir).glob("*.TextGrid"))
    assert mfa_textgrids, f"No TextGrid found in MFA output: {mfa_output_dir}"

    # Step 4: new-fave formant extraction
    print("Step 4: Extracting vowel formants with new-fave...")
    newfave_dir = extract_with_newfave(str(AUDIO), mfa_output_dir, str(JOB_DIR))
    newfave_files = list(Path(newfave_dir).iterdir())
    assert newfave_files, f"new-fave output directory is empty: {newfave_dir}"

    print(f"\nAll steps passed. Results in: {JOB_DIR}")


if __name__ == "__main__":
    run()
