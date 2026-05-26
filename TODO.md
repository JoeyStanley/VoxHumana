
## some fun names for loading messages
coupling the manuals, laying out the console, registering the swell box, drawing console, warming up pipes, tuning the reeds, lacing up organ shoes, Adjusting the wind pressure, Opening the expression box, voicing the flue pipes, Warming up the pipes...

## User Guide tab (does not exist yet — needs to be built)
Add a "User Guide" tab to the UI (alongside the main upload form). Content to include:

### If your file is over 1 GB
Preferred: split the recording into segments.
  - Recommended tool: Audacity (free) — File > Export > Export Multiple, split by time
  - Command-line option: `ffmpeg -i interview.wav -f segment -segment_time 1800 -c copy part%03d.wav`
    (splits into 30-minute chunks; adjust segment_time as needed)
  - Run each segment through VxH separately, then combine the output CSV files.

Also works: compress to a smaller format before uploading.
  - MP3 (320 kbps): good quality, ~1/5 the size of WAV
  - FLAC: lossless compression, ~1/2 the size of WAV
  - `ffmpeg -i interview.wav -b:a 320k interview.mp3`
  - VxH accepts .wav, .mp3, .flac, and other common formats.

### If VxH is taking too long
Processing time scales with recording length. Without a GPU, Whisper transcription
alone takes roughly 1× real-time (a 60-minute interview takes ~60 minutes).

Options:
  - Split the recording (see above) and run segments in parallel on separate machines.
  - Use a smaller Whisper model (e.g., "small" or "base") — faster but less accurate.
  - Run VxH locally via the command line (`python main.py`), which avoids upload time
    and lets you run on your own hardware with a GPU.
  - Contact the lab for access to a GPU-equipped server if you have many recordings.

## Job logging system (does not exist yet — ask Claude before building)
Currently, errors are written to `data/jobs/<job_id>/error.log` and the job ID is
shown to the user on the error screen. That's a stopgap. The full system should:

### Log structure
Every job (success or failure) should produce a log file, not just errors.
Organize logs outside the job directories so they persist even after job cleanup:

  data/logs/
    2026-05/
      2026-05-26/
        20260526_143022_REED-VIPER-FORMANT.txt
        20260526_151847_FLUTE-NASAL-CRANE.txt

Each .txt file should contain: datetime, job ID, original filename, config used,
step-by-step timestamps, final status, and full traceback on error.

### Human-readable job codes
Replace raw UUID job IDs (shown to users) with memorable three-word codes, e.g.
BOURDON-NASAL-CRANE. Draw from:
  - Organ stops: Bourdon, Diapason, Flute, Oboe, Trumpet, Gedackt, Quintadena,
    Tierce, Larigot, Mixture, Cornet, Gamba, Celeste, Principal, Krummhorn,
    Dulcian, Zimbel, Nazard, Sesquialtera, Vox Humana
  - Linguistics terms: Nasal, Fricative, Vowel, Formant, Coda, Onset, Nucleus,
    Mora, Rhotic, Lateral, Velar, Alveolar, Bilabial, Glottal, Affricate,
    Tonal, Aspiration, Schwa, Diphthong, Allophone
  - Animals: Crane, Heron, Finch, Falcon, Tern, Wren, Swift, Egret, Ibis,
    Kite, Lark, Mink, Newt, Orca, Puffin, Rail, Stoat, Teal, Vole, Yak

20 × 20 × 20 = 8,000 unique codes. The code lives in the log filename AND is
shown to the user on both the error screen and the success/download screen so
they can always reference it when contacting the lab.

### Before building, ask Claude about:
  - Whether to keep UUIDs internally and only surface codes to users, or replace
    UUIDs entirely (UUIDs are safer for file paths; codes are better for humans)
  - Whether logs should be plain .txt or structured (JSON, CSV) for easier parsing
  - When and how to clean up job directories (uploaded audio + intermediates) after
    a job completes — how long to keep results available for download?
