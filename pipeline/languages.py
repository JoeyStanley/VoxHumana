"""Shared language/model mappings used by both the web app and the CLI.

Keeping this in one place ensures the CLI (main.py) and the web app
(web/app.py) can't drift out of sync on which MFA models exist and which
new-fave language preset each one maps to — see LANGUAGE_DEFAULTS in
extract_with_newfave.py for the actual recode_rules/labelset_parser
settings per preset.
"""

from pathlib import Path

# UI/CLI language code -> MFA acoustic model / dictionary name.
# VoxHumana always uses the same name for a model and its dictionary.
MFA_MODEL_BY_LANGUAGE = {
    "en": "english_us_arpa",
    "es": "spanish_mfa",
    "fr": "french_mfa",
    "de": "german_mfa",
    "pt": "portuguese_mfa",
}

SUPPORTED_MFA_ACOUSTIC_MODELS = set(MFA_MODEL_BY_LANGUAGE.values())
SUPPORTED_MFA_DICTIONARIES = set(MFA_MODEL_BY_LANGUAGE.values())

# MFA acoustic model name -> new-fave language preset (see
# pipeline.extract_with_newfave.LANGUAGE_DEFAULTS). Formant extraction is
# skipped for any model not listed here.
NEWFAVE_LANGUAGE_PRESETS = {
    model: language for language, model in MFA_MODEL_BY_LANGUAGE.items()
}

# MFA dictionary name -> MFA G2P model name, for "let MFA guess" out-of-
# vocabulary handling (`mfa align --g2p_model_path`). Not a reuse of
# MFA_MODEL_BY_LANGUAGE: MFA's G2P catalog doesn't have an exact-name G2P
# model for every dictionary here (no spanish_mfa/portuguese_mfa G2P model
# exists, only regional variants like spanish_latin_america_mfa). A
# dictionary omitted here simply gets no --g2p_model_path flag - OOV words
# are left unaligned, same as the pre-G2P baseline behavior.
MFA_G2P_MODEL_BY_DICTIONARY = {
    "english_us_arpa": "english_us_arpa",
    "french_mfa": "french_mfa",
    "german_mfa": "german_mfa",
    # spanish_mfa / portuguese_mfa intentionally omitted - see TODO.md.
}

# Root directory where `mfa model download` installs pretrained models.
MFA_PRETRAINED_MODELS_ROOT = Path.home() / "Documents" / "MFA" / "pretrained_models"

# General (version-agnostic) MFA Models docs page, linked from the job log so
# users can look up the exact dictionary/phone set that was used for a job.
MFA_DICTIONARY_DOCS_URL = "https://mfa-models.readthedocs.io/en/latest/dictionary/index.html"


def mfa_dictionary_file(dictionary_name: str) -> Path:
    """Path to an installed MFA dictionary's .dict file, used as the merge/
    validation target for `mfa model add_words`. Existence isn't checked
    here - a missing file surfaces naturally as a failure in the caller.
    """
    return MFA_PRETRAINED_MODELS_ROOT / "dictionary" / f"{dictionary_name}.dict"
