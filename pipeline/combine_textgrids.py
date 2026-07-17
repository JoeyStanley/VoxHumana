from pathlib import Path

from pipeline.praat_utils import run_praat_script

SCRIPT_PATH = Path(__file__).parent / "praat" / "combine_textgrids.praat"


def combine_textgrids(job_dir, speaker_name, config=None):
    """
    Add the Whisper utterance tier to the bottom of the MFA TextGrid.

    Merges job_dir/mfa_output/{speaker_name}.TextGrid (words, phones) with
    job_dir/whisper_output/{speaker_name}.TextGrid (utterances), overwriting
    mfa_output/{speaker_name}.TextGrid in place with the resulting 3-tier grid,
    reordered to phones, words, utterances (MFA itself writes words before
    phones; new-fave doesn't care about final tier order since this runs
    after it — see below).

    Must be called after new-fave extraction (if it runs) has already read
    mfa_output/{speaker_name}.TextGrid — new-fave's tier pairing assumes
    exactly a Word/Phone tier pair, and a 3-tier grid would break it.

    Args:
        job_dir (str | Path):     Job directory containing whisper_output/
                                   and mfa_output/.
        speaker_name (str):       Stem shared by the whisper_output and
                                   mfa_output TextGrids (usually the audio
                                   file's stem).
        config (dict, optional):  Passed through to praat_utils.run_praat_script
                                   (e.g. {"praat_path": ...}).

    Returns:
        Path to the merged TextGrid (mfa_output/{speaker_name}.TextGrid).
    """
    run_praat_script(SCRIPT_PATH, [str(job_dir), speaker_name], config=config)
    return Path(job_dir) / "mfa_output" / f"{speaker_name}.TextGrid"
