### What are formants?

Formants are resonant frequencies of the vocal tract. The first two, F1 and F2, are the
primary acoustic cues to vowel quality: F1 correlates with vowel height (low vs. high) and
F2 with backness (front vs. back). 

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

**Label recoding is also switched off for this run.** new-fave's normal English behavior recodes
CMU ARPABET labels into Labov-style shorthand (e.g. "IY1" → "iy"), but there's no Labov category
for a combined label like "IYL1" — recoding just the combined ones would leave you with a file
that mixes two different transcription systems (Labov shorthand for plain vowels, raw ARPABET
for preliquid ones). To keep the whole file in one consistent system, `recode_rules` is set to
`norecode` whenever this option is checked, so *every* label — combined or not — stays in its
original raw ARPABET form (e.g. "IY1" stays "IY1", not "iy").

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
