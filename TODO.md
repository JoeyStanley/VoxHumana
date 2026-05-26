
## Email notification + close-tab workflow (not yet built)

The email field and notification section have been removed from the UI for now.
The full intended workflow, when built:

### Two submission modes (user's choice, both require email)
1. **Close-tab mode** — user submits, sees a confirmation that processing will
   continue server-side, and can safely close the browser. When the job finishes,
   VxH emails the results as a zip attachment (or a download link if the file is
   too large for email).
2. **Keep-open mode** — current behaviour: user keeps the tab open, watches the
   progress pipeline track, and downloads results when done.

### UI changes needed
- Restore the Notification section (step 5) with email as a *required* field
- Add a radio/toggle to choose between the two modes
- In close-tab mode: after submit, show a "You can safely close this tab" screen
  instead of the progress view
- In keep-open mode: current progress view, unchanged
- On the progress note, mention both options

### Backend changes needed
- Wire up an email library (e.g. `smtplib` with BYU SMTP, or SendGrid)
- Store email in the job record for logging; do not persist it after the email is sent
- After job completion in close-tab mode: zip results and send/link via email
- Queue system: the single-threaded executor already serialises jobs, but users
  in close-tab mode need to know their position. Consider a simple queue-position
  field in the job status response.

### Privacy wording (for the UI)
"Your email is used to deliver your results and is recorded in our job log alongside
your Job ID. It is not shared or used for any other purpose."

---

## Processing time estimation (not yet built)

Currently shows: "Processing time scales with recording length — a 1-hour interview
may take up to an hour." Replace with a real estimate when enough data exists.

### How to build it
1. Record two values for every completed job: audio duration (seconds) and total
   wall-clock processing time (seconds). Store these alongside the job log.
2. Once ~20–30 jobs have completed, fit a simple linear regression:
   estimated_time = a × audio_duration + b
   (or separate models per Whisper model size, since turbo ≠ large-v3 speed)
3. Show the estimate on the progress screen: "Estimated time remaining: ~12 min"
   Update it as steps complete and actual step times are known.
4. Ask Claude to help build/tune the model once the data exists.

### Notes
- Whisper is the dominant cost (~1× real-time without GPU, ~0.1× with GPU)
- MFA is fast (<1 min for most interviews)
- new-fave is fast (<1 min)
- Estimate should probably be shown *before* submit (on the form) so users can
  decide whether to use close-tab mode or keep the tab open

---

## some fun names for loading messages
coupling the manuals, laying out the console, registering the swell box, drawing console, warming up pipes, tuning the reeds, lacing up organ shoes, Adjusting the wind pressure, Opening the expression box, voicing the flue pipes, Warming up the pipes...

## Output files

Make sure they're all there, that they are organized by job (Whisper, MFA, new-fave), and that they have the original filename in them. 

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
