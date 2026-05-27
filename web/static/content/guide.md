## User guide

### What you need

All you need to run VoxHumana is a browser and an audio recording. No software installation is required.
Accepted formats: WAV, MP3, FLAC, M4A, OGG, AAC, up to 1 GB.

### Step 1: Upload your audio

Drag and drop your file onto the upload zone, or click it to browse. Click the **?** button
next to "Audio file" for tips on large files and supported formats.

### Step 2: Configure transcription

Leave **Language** on Auto-detect for most recordings. Choose a **Whisper model**: Turbo is
the recommended default. It is fast and nearly as accurate as Large. See the **?** button for a full
speed–accuracy comparison.

### Step 3: Configure alignment

The defaults work for standard American English data. Leave them as-is unless you have a
specific reason to change the acoustic model or dictionary. See the **?** button for an explanation
of what forced alignment does and when you might need different settings.

### Step 4: Configure formant extraction

**These options are coming soon.**

### Submit and wait

Click **Submit** and keep the tab open. Processing time scales with recording length: a
60-minute interview takes roughly 60 minutes on a CPU-only server. The page updates
automatically when results are ready.

### Output files

Your download is a ZIP file containing:

- **transcript.json** — Whisper output: full text plus word-level timestamps and segment confidence scores.
- **\*.TextGrid** — MFA-aligned Praat TextGrid with a word tier and a phone tier. Open in [Praat](https://www.praat.org) alongside your audio to inspect the alignment.
- **newfave_output/** — new-fave results directory. The main file is a CSV with one row per vowel token. Key columns: `word`, `phone`, `F1`–`F4` (Hz), normalized values, `duration` (seconds), and `time` (midpoint timestamp).

### Analyzing your results

The formants CSV imports directly into R or Python:

```r
vowels <- read_csv("newfave_output/results.csv")   # R / tidyverse
```

```python
vowels = pd.read_csv("newfave_output/results.csv")  # Python / pandas
```

For vowel plotting in R, [ggplot2](https://cran.r-project.org/package=ggplot2) is recommended.
The `F1` axis is conventionally plotted inverted (high values at the bottom) to match IPA vowel
chart orientation.

### If your file is over 1 GB

Split the recording into segments and run each separately, then combine the output CSVs.
Using ffmpeg (splits into 30-minute chunks):

```
ffmpeg -i interview.wav -f segment -segment_time 1800 -c copy part%03d.wav
```

Or use Audacity: File → Export → Export Multiple, splitting by time interval.

### If processing is taking too long

Whisper is the bottleneck: without a GPU it runs at roughly 1× real-time. Options:

- Switch to the **Small** or **Medium** Whisper model (significantly faster, slightly less accurate on dialectal speech)
- Split into shorter segments and process them in parallel on separate machines
- Run VoxHumana locally on a machine with a GPU — see the [README](https://github.com/JoeyStanley/VoxHumana) for setup instructions

### If your transcription has errors

Minor errors — a few wrong words, fillers missed — generally have little effect on MFA
alignment. MFA is designed to be robust to small transcript mismatches. Significant errors
(whole sentences wrong, or long stretches with no corresponding transcript) can degrade
alignment quality. If alignment looks off, inspect the TextGrid in Praat and consider
correcting the transcript before rerunning.
