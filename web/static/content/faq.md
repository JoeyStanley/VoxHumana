## Frequently asked questions

### My file is larger than 1 GB. What do I do?

Split the recording into segments and process each one separately, then combine the output CSVs.
You can split with Audacity (File → Export → Export Multiple, split by time) or with ffmpeg:

```
ffmpeg -i interview.wav -f segment -segment_time 1800 -c copy part%03d.wav
```

This creates 30-minute chunks. Adjust `-segment_time` as needed (value is in seconds).

### Processing is taking a very long time.

Whisper transcription without a GPU runs at roughly 1× real-time — a 60-minute interview can
take about 60 minutes. Switching to the **Small** model is the quickest way to speed things up.
You can also split the recording into shorter segments and run them in parallel on separate
machines, or run VoxHumana locally on a GPU-equipped machine.

### How does the queue work?

VoxHumana processes one file at a time, but the queue isn't strictly first-come, first-served.
When the server frees up, the next job goes to whoever has used the least processing time
recently, and each person's shorter files go before their longer ones. Files that skip
transcription count as short, since Whisper is the slowest step. This keeps one person who
submits many long recordings from blocking everyone else. Their files still get processed,
just interleaved with other people's.

Because of this, your place in line can change while you wait. Someone who submits a short
file after you may start before you do. No one waits indefinitely: anyone who hasn't had a
turn in 24 hours goes next.

**Using VoxHumana with a class?** Instructors can request a class code by emailing
[voxhumana.ling@gmail.com](mailto:voxhumana.ling@gmail.com). Jobs submitted with the code
during your class time go ahead of the regular queue. Enter it in the **Class code** field
above the Submit button. A job that's already running can't be interrupted, so students may
still wait for that job to finish first.

### My transcription has errors. Will that affect the results?

Minor errors — a few wrong words, fillers missed — generally have little effect on MFA
alignment. MFA is designed to be robust to small mismatches. Significant errors (whole
sentences wrong, or long stretches with no corresponding transcript) can degrade alignment
quality, which in turn affects formant accuracy. If alignment looks off in the TextGrid,
consider correcting the transcript and rerunning.

### What languages are supported?

Whisper supports 99 languages for transcription. Forced alignment currently uses MFA's English
US model, so phone-level alignment is only reliable for English at this time. Support for
additional MFA language models is planned for future releases.

### What do the output files contain?

Your ZIP download includes:

- **transcript.json** — Whisper's transcript: text, word-level timestamps, and segment confidence scores.
- **\*.TextGrid** — Praat TextGrid with word and phone tiers from MFA forced alignment.
- **newfave_output/** — new-fave's CSV of vowel formant measurements (F1–F4 in Hz, normalized values, duration, timestamp, word, and phone label).

See the User Guide for a fuller description of the CSV columns.

### Can I run VoxHumana locally?

Yes. Clone the repository and run:

```
python main.py <audio_path> <output_dir>
```

Running locally lets you use a GPU for faster Whisper transcription and avoids upload time for
large files. See the [README](https://github.com/JoeyStanley/VoxHumana) for full setup
instructions, including MFA environment setup.

### Is my audio stored on the server?

Your audio file is automatically deleted as soon as processing completes — success or failure.
The server retains only the output files (transcript, TextGrid, formants CSV), which remain
available for download for a limited time. Sociolinguistic recordings contain identifiable
personal conversations; we take this seriously and do not retain audio.

### What is a TextGrid?

A TextGrid is the standard annotation format for [Praat](https://www.praat.org), the acoustic
phonetics software. The MFA output contains two tiers: one for words and one for phones, each
with a start time, end time, and label for every token. Opening the TextGrid in Praat alongside
your audio lets you inspect the forced alignment before relying on the formant measurements.

### Can VoxHumana handle multi-speaker recordings?

VoxHumana is currently optimized for single-speaker recordings. Multi-speaker diarization is
not yet supported. For best results, use a recording of a single speaker, or a recording where
you want measurements across all audible speech.
