### What are formants?

Formants are resonant frequencies of the vocal tract. The first two, F1 and F2, are the
primary acoustic cues to vowel quality: F1 correlates with vowel height (low vs. high) and
F2 with backness (front vs. back). 

### Extraction method: new-fave vs. FAVE-extract

VoxHumana offers two formant-extraction engines:

- **new-fave (default)** — a modern, actively developed extractor. Works with English,
  Spanish, French, German, and Portuguese. Recommended for most use.
- **FAVE-extract (legacy, DARLA-style)** — the original FAVE/DARLA vowel-formant algorithm,
  kept for comparison and reproducibility with older FAVE- or DARLA-based work. English
  only. See "FAVE-extract" below for what it needs and how it differs.

### new-fave and FastTrack

new-fave measures formants using [FastTrack](https://github.com/santiagobarreda/FastTrack), 
which works differently from classic FAVE. Rather than applying a single fixed formant 
ceiling, FastTrack tries a range of candidate ceilings and selects the one that produces the 
most internally consistent formant tracks. This makes it more robust across speakers without 
manual by-speaker tuning.

### Formant ceiling (Hz)

Sets the upper bound of the formant ceiling range that FastTrack considers. The default (10000 Hz)
allows FastTrack to search broadly and select whatever ceiling fits the data best. You can
lower this if you want to constrain the search. For example, you can lower it to 5500 Hz for a 
corpus of lower-pitched voices where high ceilings produce spurious formants.

Note: this is the *maximum* ceiling FastTrack will try. Lowering it narrows the search range.

### Number of formants

Controls how many formants FastTrack attempts to measure per candidate track. The default is 3.
Increase to 4 or 5 for higher-pitched voices where more formants fall within the analysis
window. Decreasing below 3 is probably not useful.

### Include vowels during overlapping speech (Advanced)

When unchecked, vowels that occur while another speaker is talking are excluded from
measurement. Overlapping speech can produce unreliable results. For a typical fieldwork 
interview with frequent backchanneling from the interviewer, unchecking this may improve 
data quality. Leave it checked if the recording is a monologue or if you want the most data.

### Treat vowels and following liquids as single units (Advanced, English only)

For analyzing prelateral or prerhotic vowels (e.g. the vowel in *pull* or *pour*), it's often
more useful to measure formants across the whole vowel+liquid sequence rather than just the
vowel portion alone. When checked, a Praat script runs on a copy of the aligned TextGrid before
new-fave: it adds a new phone-level tier, **phones - combined - liquids**, alongside the existing
tiers. That new tier starts as a copy of the phone tier, but any word-internal vowel immediately
followed by an "L" or "R" has its interval merged with that liquid into a single combined
interval, with the stress marker moved to the end. For example, in the word *pull*,
"P + UH1 + L" becomes "P + UHL1". The original phone tier is left completely untouched. new-fave
measures formants from the new combined tier, not the original.

This only applies to English, since it relies on CMU ARPABET's stress-digit convention
(a vowel is any phone label containing a 0, 1, or 2) to tell vowels from consonants — a safe
assumption for the `english_us_arpa` MFA model, but not for other languages' phone sets.

Vowel and word boundaries come only from the MFA phone/word tiers — there's no syllable
information available, so this can't distinguish a coda liquid from a liquid that's really
the onset of the next syllable. This matters for the sub-option below.

This copy is saved as `newfave_output/*_preliquid.TextGrid`, with both the original `phones`
tier and the new `phones - combined - liquids` tier side by side, so you can always see exactly
what was merged and compare it against the original. It's kept separate from the `mfa_output`
TextGrid, which stays exactly as MFA (or your own upload) produced it.

**Combined labels get their own Labov-style codes**, so the output stays in the same notation
system either way — checking this box doesn't switch the whole file to raw ARPABET. new-fave's
normal English recoding (`cmu2labov`) has no category for a combined label like "IYL1", so this
option uses a variant (`pipeline/resources/en_preliquid_recode.yml`) that extends it. Every
combined vowel+liquid label gets a *new* code, built the same way: the vowel's normal Labov code
with a **capitalized** liquid letter appended — "UHL1" → `uL`, "AOR1" → `owR`, "AHL0" (schwa+L)
→ `@L`, "AHR1" (wedge+R) → `ʌR`. None of these reuse an existing `cmu2labov` code, even where
`cmu2labov` already has a similar-looking one:

- `cmu2labov` has no L-context category at all, so every vowel+L combination is necessarily new.
- `cmu2labov` *does* have lowercase codes like `owr`/`ahr`/`iyr` for a vowel that stayed a
  *separate* interval from a following R, recoded only to flag the context — formants measured
  over the vowel alone. A combined "AOR1" token has formants measured over the vowel **and** the
  liquid together, a different span with a different typical duration and trajectory. Reusing the
  exact same code for both would make two tokens measured by different procedures indistinguishable
  by label alone — a real problem if you ever pool results across a checkbox-off run and a
  checkbox-on run. So every vowel+R combination gets a new code too, never reusing `owr`/`ahr`/`iyr`
  etc. This also means IH/IY and UH/UW are no longer collapsed into one code the way `cmu2labov`
  collapses them before an unmerged R (`iyr` covers both) — that neutralization was specific to
  the unmerged context.

The capitalization (mirroring how `cmu2labov` itself uses a capital "F" for a different special
context — "eyF" for word-final EY) keeps every combined code visibly distinct from anything
`cmu2labov` could ever produce on its own, whether the liquid is L or R.

#### Include intervocalic liquids

Controls what happens when the liquid itself is followed by another vowel in the same word.

- **Checked (default):** merges the liquid regardless of what follows. This covers cases like
  *fuller* or *pooling*, where the "L" is the coda of the base word (*full*, *pool*) even
  though a vowel-initial suffix follows it — but it also merges cases like *yellow*, where the
  "L" is arguably the onset of the next syllable rather than a true coda liquid.
- **Unchecked:** only merges a liquid that's word-final or followed by a consonant (as in
  *pull* or *world*), skipping any liquid followed by a vowel in the same word.

There's no way to automatically tell *fuller* (coda liquid + suffix) apart from *yellow* (true
onset liquid) using phone and word boundaries alone — both look like "vowel, liquid, vowel"
within the same word. Turning this off excludes both alike; leaving it on includes both alike.
Choose based on which kind of error matters less for your analysis, or plan to filter specific
words afterward.

### FAVE-extract (legacy, DARLA-style)

FAVE-extract is the original vowel-formant algorithm behind both classic FAVE and DARLA
(Digital Archive of Southern Speech and its successors) — kept in VoxHumana as an alternative
to new-fave for comparison or reproducibility with older analyses, not as the recommended
default. It's English only, since it's built entirely around CMU ARPABET and English
dialectology (Philadelphia/North American vowel systems).

**Voice type is required** — "High" or "Low," matching DARLA's more recent wording for what
FAVE-extract itself still tracks as speaker sex under the hood. Its default formant-selection
method (Mahalanobis distance) uses sex-specific formant ceilings and measurement priors, and
can't run without this choice. Speaker name is optional — it's recorded in the output but
doesn't affect the measurements.

**A note on fidelity to DARLA:** DARLA was built on a much older release of FAVE (pre-2015,
before it was rewritten for Python 3 in 2022). VoxHumana runs the current, actively
maintained release, which its own developers describe as interface-compatible with the old
version but not verified to produce numerically identical output. If you need output that
exactly matches a specific historical DARLA run, treat this as a close approximation rather
than a guaranteed match, and compare against a known DARLA result on the same recording if
you have one available.

Output is a single tab-delimited `.txt` file, in FAVE-extract's own column format (distinct
from new-fave's `_points.csv`/`_tracks.csv` layout).
