### Why upload a TextGrid?

Unchecking **Transcribe** lets you supply your own transcript instead of running Whisper —
for example, after correcting a transcript VoxHumana produced earlier, or when you already
have a hand-made one. What VoxHumana expects from that file depends on whether Alignment is
also running.

### Scenario 1: Transcribe off, Align on

VoxHumana hands your TextGrid straight to MFA as its transcript input. MFA treats **every
tier in the file as a separate speaker's list of utterances**, so if your file has more than
one tier, you'll be asked to pick which one holds the transcription — the others are dropped
before alignment.

Requirements:
- A Praat long-format TextGrid, one interval tier per speaker.
- Non-empty intervals are what gets aligned; empty intervals are treated as gaps between
  utterances and are fine to leave in — that's exactly what Whisper-generated TextGrids do.
- The tier's name doesn't matter — MFA doesn't look at it. Naming it `utterances` (VoxHumana's
  own convention) just makes the auto-guess more reliable.

### Scenario 2: Transcribe and Align both off ("Extract only")

Here your TextGrid is already aligned — it feeds new-fave directly, skipping MFA entirely.
new-fave pairs tiers **by position**, not by name: whichever tier comes first is read as the
Word tier, and the next as the Phone tier. It doesn't tolerate any other tier count or order.

Because of this, you'll always be asked to confirm which of your tiers is Word and which is
Phone (we guess based on tier names as a starting point, but the guess is never applied
silently). Requirements:
- Exactly two tiers you can designate as Word and Phone — extra tiers are fine as long as you
  can identify the right two.
- Standard MFA output naming (`words` / `phones`) makes the guess land correctly, but any
  name works since you confirm the pick yourself.

### What isn't checked

VoxHumana validates that your file parses as a TextGrid and that enough distinct tiers exist
for the scenario you're in. It does **not** currently check:
- Whether the TextGrid's duration matches your audio file's duration.
- Whether a tier you designate as Phone/Word/utterances actually contains phones, words, or
  sentences (versus, say, an empty tier or the wrong content entirely).

A mismatch in either case won't be caught at upload — it will most likely surface as a
confusing error (or garbage output) further down the pipeline. If something looks wrong,
opening both files in Praat side by side is the fastest way to check.

### Exporting from Praat

Use **File → Save → Save as text file...** (a "long" TextGrid) rather than the short/binary
format — this is the format MFA and new-fave both expect, and the format all of VoxHumana's
own intermediate files use.

### Common mistakes

- **Wrong tier order** for Extract-only uploads — remember it's positional (first tier = Word,
  second = Phone), not name-based. Double-check the picker's guess rather than assuming it's
  right.
- **Multiple speaker tiers** left in for the Align scenario — if your TextGrid has an
  interviewer tier alongside the participant's, make sure you pick the participant's tier as
  the utterance tier.
- **Short/binary TextGrid format** instead of long text format — re-export from Praat as
  described above.
- **Duration mismatch** between the TextGrid and the audio (e.g. uploading a TextGrid made
  for a trimmed or re-encoded version of the file) — nothing catches this automatically, so
  verify durations match before submitting.
