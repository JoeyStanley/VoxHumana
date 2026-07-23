from pathlib import Path

from pipeline.praat_utils import run_praat_script

SCRIPT_PATH = Path(__file__).parent / "praat" / "combine_preliquid_sequences.praat"


def combine_preliquid_sequences(
    textgrid_path, config=None, include_intervocalic=True, word_tier=1, phone_tier=2
):
    """
    Add a new tier, "phones - combined - liquids", that's a copy of the phone
    tier with each word-internal vowel+liquid (L/R) pair merged into a
    single combined interval -- e.g. "UH1" + "L" becomes "UHL1" (the stress
    marker moves to the end). The original phone tier is left untouched;
    the new tier is appended after the TextGrid's existing tiers.

    Vowels are identified by the presence of a stress digit (0/1/2) in the
    label, matching CMU ARPABET conventions -- only meaningful for English
    (english_us_arpa) TextGrids.

    Must run before new-fave reads the TextGrid. Since new-fave pairs a
    speaker's tiers positionally as exactly (Word, Phone), the caller still
    needs to build a plain 2-tier TextGrid pointing at the new
    "phones - combined - liquids" tier (e.g. via
    pipeline.tier_selection.extract_word_phone_tiers) before handing it to
    new-fave -- this function only adds the tier, it doesn't feed new-fave.

    Args:
        textgrid_path (str | Path): TextGrid to add the tier to, in place.
        config (dict, optional):    Passed through to praat_utils.run_praat_script.
        include_intervocalic (bool): If True (default), also merge a liquid
            that's followed by another vowel in the same word (e.g. the L in
            "yellow", or in "fuller"/"pooling" where a vowel-initial suffix
            follows what's really a coda liquid). If False, only word-final
            or preconsonantal liquids are merged. Phone-adjacency alone can't
            distinguish a true syllable-onset liquid ("yellow") from a coda
            liquid before a suffix boundary ("fuller"), so turning this off
            skips both alike.
        word_tier, phone_tier (int): 1-based Praat tier indices of the
            existing tiers to read from. Defaults match the tier order MFA
            itself writes (word, then phone), which pipeline.tier_selection
            also normalizes user-uploaded TextGrids to before this ever runs.

    Returns:
        Path to the modified TextGrid (same path as textgrid_path), which
        now has one more tier than before.
    """
    textgrid_path = Path(textgrid_path)
    run_praat_script(
        SCRIPT_PATH,
        [
            str(textgrid_path),
            word_tier,
            phone_tier,
            1 if include_intervocalic else 0,
        ],
        config=config,
    )
    return textgrid_path
