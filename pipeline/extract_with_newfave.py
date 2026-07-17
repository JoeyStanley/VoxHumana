from pathlib import Path
import warnings
import yaml
from new_fave import fave_audio_textgrid, write_data

RESOURCES_DIR = Path(__file__).parent / "resources"

# Per-language defaults for new-fave's vowel-identification and recoding
# settings. English uses new-fave's built-in CMU/Labov resources, which
# only recognize CMU ARPABET phone labels (e.g. "AY1"). Other MFA acoustic
# models produce different phone sets, so they need their own labelset
# parser (a regex-based rule for identifying which phones are vowels) and
# a no-op recode scheme.
LANGUAGE_DEFAULTS = {
    "en": {
        "recode_rules": "cmu2labov",
        "labelset_parser": "cmu_parser",
        "point_heuristic": "fave",
    },
    "es": {
        "recode_rules": "norecode",
        "labelset_parser": str(RESOURCES_DIR / "spanish_mfa_parser.yml"),
        "point_heuristic": None,
    },
    "fr": {
        "recode_rules": "norecode",
        "labelset_parser": str(RESOURCES_DIR / "french_mfa_parser.yml"),
        "point_heuristic": None,
    },
    "de": {
        "recode_rules": "norecode",
        "labelset_parser": str(RESOURCES_DIR / "german_mfa_parser.yml"),
        "point_heuristic": None,
    },
    "pt": {
        "recode_rules": "norecode",
        "labelset_parser": str(RESOURCES_DIR / "portuguese_mfa_parser.yml"),
        "point_heuristic": None,
    },
}


def extract_with_newfave(audio_path, mfa_output_dir, job_dir, config=None):
    """
    Run new-fave vowel formant extraction on an MFA-aligned audio file.

    Takes the original audio and the TextGrid produced by MFA, extracts
    vowel formants using new-fave's FastTrack-based measurement pipeline,
    and writes results to <job_dir>/newfave_output/.

    Requires new-fave installed: pip install new-fave

    Config options:
        language (str):         Language preset for recode_rules /
                                 labelset_parser / point_heuristic defaults,
                                 default "en". See LANGUAGE_DEFAULTS.
        speakers (str):         Speaker selection, default "all"
        recode_rules (str):     Label recoding scheme, default from LANGUAGE_DEFAULTS
        labelset_parser (str):  Phonetic label parser, default from LANGUAGE_DEFAULTS
        point_heuristic (str):  Measurement point method, default from LANGUAGE_DEFAULTS
        ft_config (str):        FastTrack configuration, default "default"

    Returns:
        Path to the new-fave output directory.
    """
    if config is None:
        config = {}

    language = config.get("language", "en")
    lang_defaults = LANGUAGE_DEFAULTS.get(language, LANGUAGE_DEFAULTS["en"])

    audio_path = Path(audio_path)
    mfa_output_dir = Path(mfa_output_dir)
    job_dir = Path(job_dir)

    # MFA names the TextGrid after the source audio file, which may differ from
    # audio_path.stem (e.g. when the web server renames uploads, or when MFA
    # pulls utterance IDs from its global corpus cache). Glob for whatever is there.
    textgrid_files = sorted(mfa_output_dir.glob("*.TextGrid"))
    if not textgrid_files:
        raise FileNotFoundError(
            f"No MFA TextGrid found in {mfa_output_dir}. "
            "Make sure align_with_mfa() completed successfully."
        )
    if len(textgrid_files) > 1:
        names = ", ".join(f.name for f in textgrid_files)
        raise RuntimeError(
            f"Expected one TextGrid in {mfa_output_dir} but found {len(textgrid_files)}: {names}. "
            "VoxHumana processes one speaker per job."
        )
    textgrid_path = textgrid_files[0]

    output_dir = job_dir / "newfave_output"
    output_dir.mkdir(parents=True, exist_ok=True)

    formant_ceiling = config.get("formant_ceiling", None)
    num_formants = config.get("num_formants", None)
    include_overlaps = config.get("include_overlaps", True)

    ft_config = "default"
    if formant_ceiling is not None or num_formants is not None:
        override = {}
        if formant_ceiling is not None:
            override["max_max_formant"] = formant_ceiling
        if num_formants is not None:
            override["n_formants"] = num_formants
        ft_config_path = output_dir / "ft_config.yml"
        ft_config_path.write_text(yaml.dump(override))
        ft_config = str(ft_config_path)

    # fave_audio_textgrid() catches its own exceptions and returns None on
    # failure (e.g. a corrupt or mislabeled audio file), warning instead of
    # raising. Capture that warning so a None result produces a clear error
    # here instead of a confusing AttributeError inside write_data().
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        speakers = fave_audio_textgrid(
            audio_path,
            textgrid_path,
            speakers=config.get("speakers", "all"),
            recode_rules=config.get("recode_rules", lang_defaults["recode_rules"]),
            labelset_parser=config.get("labelset_parser", lang_defaults["labelset_parser"]),
            point_heuristic=config.get("point_heuristic", lang_defaults["point_heuristic"]),
            ft_config=ft_config,
            include_overlaps=include_overlaps,
        )

    if speakers is None:
        detail = "; ".join(str(w.message) for w in caught) or "no further detail was reported by new-fave"
        raise RuntimeError(f"new-fave failed to process {audio_path.name}: {detail}")

    write_data(speakers, destination=str(output_dir))

    return output_dir
