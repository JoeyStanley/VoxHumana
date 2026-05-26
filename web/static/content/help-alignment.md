### What is forced alignment?

Forced alignment maps a transcript to an audio file, finding the precise start and end time
of every word — and every phone within each word. VoxHumana needs phone-level timing to
measure formants at the right point in each vowel.

### Montreal Forced Aligner

MFA is the standard forced alignment tool in linguistics research. It uses an acoustic model
(a statistical description of how sounds are produced) paired with a pronunciation dictionary.

### Acoustic model

The default **English US — arpa** model is trained on American English and works well for most
North American data. Additional models for other varieties and languages are planned for future
releases.

### Dictionary

Maps each word to a sequence of phonemes. The dictionary must match the acoustic model — leave
this on the default unless you have a custom dictionary for your specific data.

### Note on accuracy

Minor transcription errors rarely affect alignment quality. Words not found in the dictionary
get a guessed pronunciation, which is slightly less reliable. If alignment looks off, inspect
the TextGrid in Praat alongside your audio.
