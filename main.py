import argparse
from pathlib import Path

from pipeline.transcribe_with_whisper import transcribe
from pipeline.convert_whisper_to_textgrid import convert_whisper_to_textgrid
from pipeline.generate_transcript import generate_transcript
from pipeline.align_with_mfa import align_with_mfa
from pipeline.extract_with_newfave import extract_with_newfave
from pipeline.combine_textgrids import combine_textgrids
from pipeline.languages import MFA_MODEL_BY_LANGUAGE, NEWFAVE_LANGUAGE_PRESETS


def run_pipeline(audio_path, job_dir, config=None):
    """
    Run the full VoxHumana pipeline on a single audio file.

    Steps:
        1. Transcribe with Whisper
        2. Convert transcript to TextGrid, then generate a line-by-line transcript
        3. Forced-align with MFA
        4. Extract vowel formants with new-fave
        5. Add the utterance tier to the MFA TextGrid

    Returns the path to the new-fave output directory.
    """
    if config is None:
        config = {}

    Path(job_dir).mkdir(parents=True, exist_ok=True)
    stem = Path(audio_path).stem

    print("Step 1: Transcribing with Whisper...")
    whisper_result = transcribe(audio_path, job_dir, config.get("whisper"))

    print("Step 2: Converting transcript to TextGrid...")
    convert_whisper_to_textgrid(whisper_result, audio_path, job_dir)
    generate_transcript(job_dir, stem, config.get("praat"))

    print("Step 3: Aligning with MFA...")
    mfa_output_dir = align_with_mfa(audio_path, job_dir, config.get("mfa"))

    print("Step 4: Extracting vowel formants with new-fave...")
    newfave_output_dir = extract_with_newfave(
        audio_path, mfa_output_dir, job_dir, config.get("newfave")
    )

    print("Step 5: Adding utterance tier to MFA TextGrid...")
    combine_textgrids(job_dir, stem, config.get("praat"))

    print(f"Done. Results written to: {newfave_output_dir}")
    return newfave_output_dir


def main():
    parser = argparse.ArgumentParser(description="Run the VoxHumana pipeline.")
    parser.add_argument("audio_path", help="Path to the input .wav file")
    parser.add_argument("job_dir", help="Directory for all intermediate and final output")
    parser.add_argument(
        "--language",
        choices=sorted(MFA_MODEL_BY_LANGUAGE),
        default="en",
        help="Language of the recording (default: en). Selects the matching "
             "MFA acoustic model/dictionary and new-fave vowel preset.",
    )
    args = parser.parse_args()

    mfa_model = MFA_MODEL_BY_LANGUAGE[args.language]
    config = {
        "whisper": {"language": args.language},
        "mfa": {"acoustic_model": mfa_model, "dictionary": mfa_model},
        "newfave": {"language": NEWFAVE_LANGUAGE_PRESETS.get(mfa_model, "en")},
    }
    run_pipeline(args.audio_path, args.job_dir, config)


if __name__ == "__main__":
    main()
