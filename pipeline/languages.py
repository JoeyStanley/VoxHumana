"""Shared language/model mappings used by both the web app and the CLI.

Keeping this in one place ensures the CLI (main.py) and the web app
(web/app.py) can't drift out of sync on which MFA models exist and which
new-fave language preset each one maps to — see LANGUAGE_DEFAULTS in
extract_with_newfave.py for the actual recode_rules/labelset_parser
settings per preset.
"""

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
