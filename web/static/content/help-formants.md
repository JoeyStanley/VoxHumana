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

Note: this is not a fixed ceiling the way classic FAVE's formant ceiling works. It is the
*maximum* ceiling FastTrack will try. Lowering it narrows the search range; it does not
force a specific ceiling.

### Number of formants

Controls how many formants FastTrack attempts to measure per candidate track. The default is 3.
Increase to 4 or 5 for higher-pitched voices where more formants fall within the analysis
window. Decreasing below 3 is probably not useful.

### Include vowels during overlapping speech (Advanced)

When unchecked, vowels that occur while another speaker is talking are excluded from
measurement. Overlapping speech can produce formant interference — the second speaker's
resonances contaminate the measurement of the first. For a typical fieldwork interview with
frequent back-channeling from the interviewer, unchecking this may improve data quality.
Leave it checked if the recording is a monologue or if you want maximum token count.
