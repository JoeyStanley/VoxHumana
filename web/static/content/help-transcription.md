### About Whisper

Whisper is OpenAI's automatic speech recognition system, trained on 680,000 hours of
multilingual audio. It is state-of-the-art for general transcription, particularly on
nonstandard speech.

### Language

VoxHumana can transcribe and force-align recordings in English, French, German, Portuguse, and Spanish, 
with more languages coming soon. For now, formant extraction is only supported for English.

### Transcription hint

This is an optional textbox that primes Whisper before transcription begins. Use it to
improve accuracy on names, places, or unusual vocabulary that Whisper might otherwise mishear:

- Speaker or interviewer names (e.g. *"Interviewer: Sarah. Participant: MecKenzie."*)
- Location or community (e.g. *"Heber, Utah; Buena Vista, Virginia"*)
- Topic keywords or unusual words (e.g. *"oystering, longshoreman, pyroclastic"*)
- Variety-specific spellings you want Whisper to prefer

### Model size

- **Turbo** (recommended) — Fast and nearly as accurate as Large. This is the best default for fieldwork recordings.
- **Large** — Most accurate, but significantly slower (~1× real-time). This is worth trying if Turbo produces problematic transcripts.
- **Medium** — A useful middle ground when Turbo misses content and Large is too slow.
- **Small** — Fastest option. Accuracy degrades noticeably on nonstandard speech; use only when speed is critical.

All models run on CPU by default. GPU access makes Whisper 5–10× faster.

### Carry context across chunks (Advanced)

Whisper processes audio in 30-second chunks. When this option is checked (the default),
each chunk is fed the text from the previous chunk as context. This means that names stay consistently
spelled, sentences flow naturally across boundaries, and the transcription stays coherent
over a long recording.

Turn it off when context between chunks isn't useful or could cause problems:

- **Wordlists or elicitation tasks**: Since each item is independent, carrying context from the
  previous item adds noise rather than signal.
- **Short audio files**: There is little or no overlap between chunks anyway.
- **Repetition loops**: If a transcript comes back with a passage repeated many times,
  Whisper got stuck in a feedback loop. Unchecking this and resubmitting will reset the
  model every 30 seconds and usually clears it up.
