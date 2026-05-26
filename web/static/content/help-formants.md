### What are formants?

Formants are resonant frequencies of the vocal tract. The first two — F1 and F2 — are the
primary acoustic cues to vowel quality: F1 correlates with vowel height (low vs. high) and
F2 with backness (front vs. back). VoxHumana records F1–F4 for every vowel token.

### new-fave

new-fave is the modern successor to the FAVE toolkit. It uses Praat's formant tracker with
improved measurement-point selection and output organization.

### Speaker voice

Sets the formant ceiling — the maximum frequency Praat searches for formants.

- **Low** — ~5000 Hz ceiling, appropriate for adult men and lower voices.
- **High** — ~5500 Hz ceiling, appropriate for women and children.

Choosing the wrong type can cause F1–F2 tracking errors. When in doubt, inspect the output
formant tracks in Praat.

### Stop words

Common function words (*the, a, is, to…*) are usually unstressed and reduced, making their
vowels less representative of underlying categories. **Filter** removes them (recommended for
most research). **Keep** includes all words.

### Unstressed vowels

Vowels in unstressed syllables often reduce toward schwa, obscuring their underlying vowel
category. **Filter** excludes them from the output (recommended). **Keep** includes all vowel
tokens.

### Formant ceiling — advanced

Overrides the ceiling set by Speaker voice. Typical values: 4500–5000 Hz for men,
5000–5500 Hz for women. Adjust if formant tracks in your output look incorrect.

### Number of formants — advanced

How many formants Praat tracks simultaneously. 4 is standard. Setting this to 5 can help for
high-pitched voices or certain language varieties where F3 and F4 get conflated.
