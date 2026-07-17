from pathlib import Path

from pipeline.praat_utils import run_praat_script

SCRIPT_PATH = Path(__file__).parent / "praat" / "generate_transcript.praat"


def generate_transcript(job_dir, speaker_name, config=None):
    """
    Create a plain-text, line-per-utterance transcript from the Whisper TextGrid.

    Reads job_dir/whisper_output/{speaker_name}.TextGrid and writes
    job_dir/whisper_output/{speaker_name}_lines.txt, one utterance per line
    prefixed with its start time (mm:ss).

    Args:
        job_dir (str | Path):     Job directory containing whisper_output/.
        speaker_name (str):       Stem shared by the whisper_output TextGrid
                                   (usually the audio file's stem).
        config (dict, optional):  Passed through to praat_utils.run_praat_script
                                   (e.g. {"praat_path": ...}).

    Returns:
        Path to the generated *_lines.txt file.
    """
    run_praat_script(SCRIPT_PATH, [str(job_dir), speaker_name], config=config)
    return Path(job_dir) / "whisper_output" / f"{speaker_name}_lines.txt"
