# NOT CURRENTLY WIRED IN. This module is parked, working code for the
# "upload a plain-text transcript" feature described in TODO.md under
# "Support for .txt transcriptions" — nothing in web/app.py or main.py
# imports or calls it. See that TODO entry for why it was pulled back out
# and exactly how to re-wire it if picked back up later.

import librosa
from pathlib import Path

from pipeline.praat_utils import run_praat_script

SCRIPT_PATH = Path(__file__).parent / "praat" / "txt_to_TextGrid.praat"


def txt_to_textgrid(txt_path, audio_path, job_dir, config=None):
    """
    Build a scratch utterance TextGrid from a plain-text transcript.

    Stands in for transcribe_with_whisper + convert_whisper_to_textgrid when
    the user supplies their own transcript instead of running Whisper: wraps
    the whole file as a single interval spanning the audio's duration, on a
    single "utterances" tier, and writes it to whisper_output/{stem}.TextGrid
    — the same path align_with_mfa reads its transcript input from.

    Duration is computed here with librosa and passed to the Praat script as
    a form argument, rather than having the script open the audio itself.

    Args:
        txt_path (str | Path):    Plain-text transcript to wrap.
        audio_path (str | Path):  Audio file the transcript describes (used
                                   only for its duration and filename stem).
        job_dir (str | Path):     Job directory; output goes under
                                   job_dir/whisper_output/.
        config (dict, optional):  Passed through to praat_utils.run_praat_script
                                   (e.g. {"praat_path": ...}).

    Returns:
        Path to the generated whisper_output/{stem}.TextGrid.
    """
    # Round to match convert_whisper_to_textgrid's precision, for consistency
    # between the two paths that can produce whisper_output/{stem}.TextGrid.
    duration = round(librosa.get_duration(path=str(audio_path)), 3)

    whisper_dir = Path(job_dir) / "whisper_output"
    whisper_dir.mkdir(parents=True, exist_ok=True)
    output_path = whisper_dir / f"{Path(audio_path).stem}.TextGrid"

    run_praat_script(SCRIPT_PATH, [duration, str(txt_path), str(output_path)], config=config)
    return output_path
