### About Whisper

Whisper is OpenAI's automatic speech recognition system, trained on 680,000 hours of
multilingual audio. It is state-of-the-art for general transcription, particularly on
accented and dialectal speech.

### Language

Leave on **Auto-detect** for most recordings — Whisper identifies the language from the first
30 seconds of audio. Set it explicitly to skip detection or for short recordings where
auto-detect may misfire.

### Model size

- **Turbo** (recommended) — Fast and nearly as accurate as Large. The best default for fieldwork recordings.
- **Large** — Most accurate, but significantly slower (~1× real-time without a GPU). Worth trying if Turbo produces problematic transcripts.
- **Medium** — A useful middle ground when Turbo misses content and Large is too slow.
- **Small** — Fastest option. Accuracy degrades noticeably on dialectal and accented speech; use only when speed is critical.

All models run on CPU by default. GPU access makes Whisper 5–10× faster.
