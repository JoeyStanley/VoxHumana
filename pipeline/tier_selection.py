"""
Helpers for handling user-supplied MFA-format TextGrids that don't match
new-fave's expected shape.

new-fave (via aligned_textgrid/fasttrackpy) pairs a speaker's tiers
positionally as (Word, Phone) — it never looks at tier names, and it doesn't
tolerate extra tiers. TextGrids that weren't produced by VoxHumana's own
align_with_mfa() step (hand-corrected files, TextGrids from other tools,
previously-downloaded VoxHumana *output* which has a 3rd utterance tier)
commonly have the wrong tier count and/or order. Rather than let new-fave
silently misread the wrong tiers, the web UI asks the user which tier is
which, and these helpers back that up: listing tier names, guessing the
likely roles, and rebuilding a canonical 2-tier (Word, Phone) grid from the
user's selection.
"""

from pathlib import Path

import tgt

PHONE_KEYWORDS = ("phone", "phoneme", "segment")
WORD_KEYWORDS = ("word",)


def list_tier_names(textgrid_path):
    """Return the tier names in a TextGrid, in file order."""
    tg = tgt.io.read_textgrid(str(textgrid_path), include_empty_intervals=True)
    return [tier.name for tier in tg.tiers]


def guess_tier_roles(tier_names):
    """
    Guess which tier index is the phone tier and which is the word tier.

    Defaults to the first tier as phones and the second as words, then
    overrides either role if some tier name fuzzily matches that role's
    keywords (case-insensitive substring match) — e.g. a tier named
    "sentence - phoneme" or "Segment" is recognized as the phone tier
    regardless of position.

    Args:
        tier_names (list[str]): Tier names in file order.

    Returns:
        (phone_idx, word_idx): 0-based tier indices, or None if there
            aren't enough tiers to fill that role.
    """
    lowered = [name.lower() for name in tier_names]

    phone_idx = 0 if len(tier_names) > 0 else None
    word_idx = 1 if len(tier_names) > 1 else None

    phone_match = next(
        (i for i, n in enumerate(lowered) if any(k in n for k in PHONE_KEYWORDS)),
        None,
    )
    word_match = next(
        (
            i
            for i, n in enumerate(lowered)
            if any(k in n for k in WORD_KEYWORDS) and i != phone_match
        ),
        None,
    )

    if phone_match is not None:
        phone_idx = phone_match
    if word_match is not None:
        word_idx = word_match

    if phone_idx == word_idx:
        # Fuzzy matches collided (e.g. a single tier matched both keyword
        # sets) — fall back to the plain positional default rather than
        # guess wrong.
        phone_idx = 0 if len(tier_names) > 0 else None
        word_idx = 1 if len(tier_names) > 1 else None

    return phone_idx, word_idx


def extract_word_phone_tiers(textgrid_path, word_idx, phone_idx, output_path=None):
    """
    Rebuild a TextGrid as a canonical 2-tier grid — the selected word tier
    first, then the selected phone tier, discarding any other tiers.

    This is the order new-fave's positional (Word, Phone) tier pairing
    expects, which is the reverse of the (Phone, Word, Utterance) order
    VoxHumana's own combine_textgrids step produces for downloaded output.

    Args:
        textgrid_path (str | Path): Source TextGrid.
        word_idx (int):             0-based index of the tier to use as words.
        phone_idx (int):             0-based index of the tier to use as phones.
        output_path (str | Path, optional):
            Where to write the result. Defaults to overwriting textgrid_path.

    Returns:
        Path to the written TextGrid.
    """
    tg = tgt.io.read_textgrid(str(textgrid_path), include_empty_intervals=True)
    word_tier = tg.tiers[word_idx]
    phone_tier = tg.tiers[phone_idx]

    out_tg = tgt.TextGrid()
    out_tg.add_tier(word_tier)
    out_tg.add_tier(phone_tier)

    dest = Path(output_path) if output_path else Path(textgrid_path)
    tgt.write_to_file(out_tg, str(dest))
    return dest
