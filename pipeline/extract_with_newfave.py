from pathlib import Path
from new_fave import fave_audio_textgrid, write_data


def extract_with_newfave(audio_path, mfa_output_dir, job_dir, config=None):
    """
    Run new-fave vowel formant extraction on an MFA-aligned audio file.

    Takes the original audio and the TextGrid produced by MFA, extracts
    vowel formants using new-fave's FastTrack-based measurement pipeline,
    and writes results to <job_dir>/newfave_output/.

    Requires new-fave installed: pip install new-fave

    Config options:
        speakers (str):         Speaker selection, default "all"
        recode_rules (str):     Label recoding scheme, default "cmu2labov"
        labelset_parser (str):  Phonetic label parser, default "cmu_parser"
        point_heuristic (str):  Measurement point method, default "fave"
        ft_config (str):        FastTrack configuration, default "default"

    Returns:
        Path to the new-fave output directory.
    """
    if config is None:
        config = {}

    audio_path = Path(audio_path)
    mfa_output_dir = Path(mfa_output_dir)
    job_dir = Path(job_dir)

    textgrid_path = mfa_output_dir / (audio_path.stem + ".TextGrid")
    if not textgrid_path.exists():
        raise FileNotFoundError(
            f"No MFA TextGrid found at {textgrid_path}. "
            "Make sure align_with_mfa() completed successfully."
        )

    output_dir = job_dir / "newfave_output"
    output_dir.mkdir(parents=True, exist_ok=True)

    speakers = fave_audio_textgrid(
        audio_path,
        textgrid_path,
        speakers=config.get("speakers", "all"),
        recode_rules=config.get("recode_rules", "cmu2labov"),
        labelset_parser=config.get("labelset_parser", "cmu_parser"),
        point_heuristic=config.get("point_heuristic", "fave"),
        ft_config=config.get("ft_config", "default"),
    )

    write_data(speakers, destination=str(output_dir))

    return output_dir
