import shutil
from pathlib import Path
import warnings
import yaml
import tgt
from new_fave import fave_audio_textgrid, write_data

from pipeline.combine_preliquid_sequences import combine_preliquid_sequences
from pipeline.tier_selection import extract_word_phone_tiers

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
        combine_preliquid (bool): Merge word-internal vowel+liquid (L/R) phone
                                 intervals into a single combined interval
                                 before extraction (e.g. "UH1"+"L" -> "UHL1"),
                                 so the vowel and liquid are measured as one
                                 unit. Only applies when language == "en" (it's
                                 silently ignored otherwise, since it depends
                                 on CMU ARPABET stress-digit labels); default
                                 False. Forces recode_rules to "norecode" (raw
                                 CMU ARPABET labels for everything, not just
                                 the combined ones) unless recode_rules is
                                 explicitly set, since new-fave's Labov-style
                                 English recoding has no equivalent category
                                 for combined vowel+liquid labels. See
                                 pipeline.combine_preliquid_sequences.
        include_intervocalic (bool): When combine_preliquid is on, whether to
                                 also merge a liquid followed by another vowel
                                 in the same word (e.g. "yellow", "fuller").
                                 Default True. See combine_preliquid_sequences
                                 for the caveat this involves.

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

    # new-fave (via aligned_textgrid/fasttrackpy) pairs a speaker's tiers
    # positionally as (Word, Phone) — it does not look at tier names. A
    # TextGrid with any other tier count silently misassigns roles instead of
    # failing: e.g. a 3-tier grid gets read as (tier1=Word, tier2=Phone),
    # dropping tier3, which is exactly the shape of a VoxHumana *output*
    # TextGrid (Step 5 appends an utterance tier) re-uploaded as if it were a
    # fresh MFA TextGrid. Catch that here with an actionable error rather than
    # let new-fave run against the wrong tiers and produce empty/garbage data.
    tg_for_validation = tgt.io.read_textgrid(str(textgrid_path), include_empty_intervals=True)
    n_tiers = len(tg_for_validation.tiers)
    if n_tiers != 2:
        tier_names = ", ".join(repr(t.name) for t in tg_for_validation.tiers)
        raise RuntimeError(
            f"Expected exactly 2 tiers (words, then phones) in {textgrid_path.name}, "
            f"found {n_tiers}: {tier_names}. new-fave pairs a speaker's tiers "
            "positionally, not by name, so an extra tier (e.g. an 'utterance' tier "
            "from a previously downloaded VoxHumana output) causes it to silently "
            "misread the wrong tiers as words/phones instead of failing loudly. "
            "Remove any extra tier and make sure the first tier is words and the "
            "second is phones before re-uploading."
        )

    output_dir = job_dir / "newfave_output"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Only meaningful for English (CMU ARPABET stress-digit labels) -- silently
    # ignored for any other language rather than erroring, mirroring how an
    # unsupported acoustic model silently disables the whole formants step
    # elsewhere in the pipeline (see web/app.py's create_job()).
    combine_preliquid = bool(config.get("combine_preliquid", False)) and language == "en"
    extraction_textgrid_path = textgrid_path
    preliquid_textgrid_path = None
    if combine_preliquid:
        # Work on a copy in newfave_output/, not the mfa_output TextGrid itself
        # -- this tier only exists because of a new-fave option, and mfa_output
        # may not even correspond to an actual MFA run (e.g. the "Extract only"
        # flow uploads a TextGrid directly). Adds a 3rd tier, "phones - combined
        # - liquids", to the copy; the original word/phone tiers are left
        # untouched.
        preliquid_textgrid_path = output_dir / f"{textgrid_path.stem}_preliquid.TextGrid"
        shutil.copy2(textgrid_path, preliquid_textgrid_path)
        combine_preliquid_sequences(
            preliquid_textgrid_path,
            config=config,
            include_intervocalic=config.get("include_intervocalic", True),
        )
        # new-fave requires exactly 2 tiers and pairs them positionally as
        # (Word, Phone), so build a throwaway 2-tier copy pointing at the new
        # tier instead of the original phone tier, and extract from that.
        # Tier order at this point is always (word, phone, phones - combined -
        # liquids) -- see combine_preliquid_sequences, which always appends
        # the new tier last -- so word is tier 0 and the new tier is the last one.
        extraction_textgrid_path = output_dir / f"{textgrid_path.stem}_for_extraction.TextGrid"
        n_tiers_with_combined = len(tgt.io.read_textgrid(str(preliquid_textgrid_path)).tiers)
        extract_word_phone_tiers(
            preliquid_textgrid_path,
            word_idx=0,
            phone_idx=n_tiers_with_combined - 1,
            output_path=extraction_textgrid_path,
        )

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

    # When combine_preliquid is active, the default recode scheme switches to
    # "norecode" (raw CMU ARPABET labels, left alone) for every token, not
    # just the combined ones. new-fave's Labov-style English recoding
    # (cmu2labov) has no equivalent category for a combined vowel+liquid
    # label like "UHL1" -- and there's no way to recode just the combined
    # ones without producing a file that mixes two different transcription
    # conventions (Labov shorthand for plain vowels, raw ARPABET for
    # preliquid ones). An explicit recode_rules override in config still
    # takes precedence.
    recode_rules_default = "norecode" if combine_preliquid else lang_defaults["recode_rules"]

    # fave_audio_textgrid() catches its own exceptions and returns None on
    # failure (e.g. a corrupt or mislabeled audio file), warning instead of
    # raising. Capture that warning so a None result produces a clear error
    # here instead of a confusing AttributeError inside write_data().
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        speakers = fave_audio_textgrid(
            audio_path,
            extraction_textgrid_path,
            speakers=config.get("speakers", "all"),
            recode_rules=config.get("recode_rules", recode_rules_default),
            labelset_parser=config.get("labelset_parser", lang_defaults["labelset_parser"]),
            point_heuristic=config.get("point_heuristic", lang_defaults["point_heuristic"]),
            ft_config=ft_config,
            include_overlaps=include_overlaps,
        )

    if combine_preliquid:
        # Throwaway file, not meant to be part of the user's download -- the
        # tier it was built from is already in {stem}_preliquid.TextGrid,
        # which is kept.
        extraction_textgrid_path.unlink(missing_ok=True)

    if speakers is None:
        detail = "; ".join(str(w.message) for w in caught) or "no further detail was reported by new-fave"
        raise RuntimeError(f"new-fave failed to process {audio_path.name}: {detail}")

    write_data(speakers, destination=str(output_dir))

    return output_dir
