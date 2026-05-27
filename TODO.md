
## User Guide: single-speaker recordings

Add a note to the User Guide explaining that VoxHumana is designed for single-speaker
recordings (one participant, one interviewer) and that this is the optimal input for MFA.
Include a concrete example of what that looks like:
- One audio file per speaker or per interview session
- The target speaker should be the dominant voice
- Background noise, cross-talk, and multiple simultaneous speakers will degrade alignment quality

Also explain what "single speaker" means in MFA terms (`--single_speaker` mode) and why
VoxHumana doesn't expose it as a toggle: for typical fieldwork interviews, MFA's default
speaker-adaptive mode performs better and the distinction is unlikely to matter unless the
user is processing something unusual (e.g., a group conversation or a read-aloud wordlist
with no inter-speaker variation).

---

## MFA: additional acoustic models and dictionaries (coming soon — blocked on multi-language)

The Alignment section's acoustic model and dictionary dropdowns currently have only one
option each (`english_us_arpa`) and are disabled in the UI with a "coming soon" note.
Expanding them is blocked on the same multi-language MFA work described above.

When adding a new language:
1. Install the MFA acoustic model and dictionary (`mfa model download acoustic <name>`,
   `mfa model download dictionary <name>`).
2. Add the new `<option>` to both dropdowns in the Alignment section.
3. Decide whether to auto-pair model and dictionary based on the Whisper language selection,
   or let the user choose them independently (independent choice is more flexible but
   requires more UI guidance to avoid mismatched pairs).
4. Re-enable both dropdowns and remove the "coming soon" note once at least two options exist.

---

## MFA: OOV words file and custom dictionaries

Two related features worth adding when there is demand:

### OOV words file
MFA can output a list of out-of-vocabulary (OOV) words — words in the transcript that are
not in the pronunciation dictionary and received a guessed pronunciation. Surfacing this
file in the VoxHumana download would help users identify transcription or alignment problems
early (e.g. a misspelled name that MFA couldn't look up).

To implement: check MFA's output directory for an OOV file after alignment and include it
in the results zip if present.

### Custom dictionaries
Power users (e.g., researchers working with a specific dialect community) may want to
upload a custom pronunciation dictionary alongside their audio. MFA accepts a plain-text
dictionary file as the `DICTIONARY_PATH` argument instead of a model name.

To implement: add an optional file upload field in the Alignment section, validate that
it's a `.txt` or `.dict` file, and pass its path to MFA instead of the default model name.
Consider whether to allow this alongside or instead of the built-in dictionaries.

---

## Multi-language support (coming soon — blocked on MFA)

The Whisper transcription step already supports any language via the `language` parameter,
and the UI language field is wired up end-to-end. However, the forced alignment step (MFA)
currently only has English acoustic models and dictionaries configured, so the full pipeline
only works for English recordings. The language selector is disabled in the UI with a
"coming soon" note until this is resolved.

### What's needed to enable a new language
1. Install the MFA acoustic model and dictionary for the target language
   (e.g. `mfa model download acoustic spanish_mfa`, `mfa model download dictionary spanish_mfa`).
2. Add the language option to the Alignment section dropdowns in the UI.
3. Re-enable the Language dropdown in the Transcription section and wire language →
   acoustic model selection (either automatically or via user choice).
4. Test end-to-end on a real recording in that language.

### Also consider
- The `task` parameter in Whisper: setting `task="translate"` outputs an English transcript
  even for non-English audio. This could be a useful intermediate mode (transcribe → English
  → MFA with English models) before full multi-language MFA support is ready.
- Documenting which languages MFA supports out of the box.

---

## Transcription hint / initial_prompt expansion

The `initial_prompt` field is wired up and working. Possible future enhancements:

- **Per-speaker prompts**: if the recording has multiple speakers, allow separate hints
  per speaker (requires diarization, which is a larger feature).
- **Saved prompts**: let users save commonly used hints (e.g. a fieldwork community name
  and set of local vocabulary) and recall them from a dropdown.
- **Auto-prompt from metadata**: if the upload form eventually collects speaker/location
  metadata, pre-populate the hint field automatically.

---

## Formant ceiling and number-of-formants overrides (not yet wired up)

The Advanced options panel in the UI shows these controls with a "coming soon" note and the
inputs disabled. The backend work needed before enabling them:

- **new-fave side**: confirm that `fave_audio_textgrid()` exposes formant ceiling and number
  of formants via its `ft_config` parameter (check new-fave docs / source). If so, build a
  small config dict to pass those values through.
- **API side** (`web/app.py`): accept `formant_ceiling` and `num_formants` as `Form(...)` fields
  in `create_job()`, validate them (integers/floats in sane ranges), and populate
  `config["newfave"]` rather than leaving it `{}`.
- **JS side** (`web/static/index.html`): re-enable the inputs (remove `disabled` and
  `pointer-events:none`), and append the values to `FormData` alongside the other fields.
- Once wired up, remove the `.adv-coming-soon` note and update `help-formants.md` to describe
  the settings properly (typical values: 4500–5000 Hz men, 5000–5500 Hz women; 4 formants
  standard, 5 useful for high-pitched voices).

---

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

## CLI debugging tool (explore whether this is needed)

The web UI already shows step-by-step progress and surfaces error messages with
a job ID. Before building a separate debug tool, check whether the UI + error.log
is sufficient for diagnosing user-reported failures.

If a dedicated tool turns out to be useful, it could accept a job ID or a path to
an audio file and re-run individual steps with verbose output — useful for replaying
a failed job locally without going through the web interface. Options to consider:
  - `python debug.py --job <job_id>` — re-run pipeline on an existing job directory
  - `python debug.py --audio <file> --step whisper` — run just one step with full logging
  - Flags for overriding config (model size, language, MFA env, etc.)
  - Print full Whisper output, MFA stdout/stderr, new-fave warnings

Ask: does the existing `tests/test_pipeline.py` + the web error screen already cover
the debugging workflow well enough? If users can report a job ID and you can find
the error.log, a separate CLI debug tool may not be worth the maintenance cost.

Potentially make it so that the log files themselves can be read in by the debugging tool 
so that processing is completely replicable. 

---

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

## File management and security (partially done — needs completion)

### What's already cleaned up
After every job (success or failure), the pipeline now deletes the large intermediates
that are no longer needed: the uploaded audio file, `mfa_corpus/` (copy of audio),
and `mfa_temp/` (MFA working data). This recovers 1–3 GB per job immediately.

### What still needs to be done
- **Result files expire**: job result directories (`data/jobs/<uuid>/`) currently
  accumulate forever. Once a user has downloaded their results (or after a set
  retention window, e.g. 24–48 hours), the entire job directory should be deleted.
  Coordinate with the logging system below — logs must be written to `data/logs/`
  *before* the job directory is removed, so the record survives cleanup.

- **Logs live separately**: job logs (`data/logs/`) must never be deleted as part
  of job directory cleanup. They are the audit trail. Keep them indefinitely (or
  archive to cold storage after a year).

- **Uploaded audio is sensitive**: sociolinguistic recordings contain identifiable
  voices and personal conversations. The audio file is already deleted as soon as
  the pipeline finishes, which is correct. Verify this holds even if the server
  crashes mid-job (on restart, scan for job dirs that have an audio file but no
  `error.log` and no results — these are orphaned and should be cleaned up).

- **No world-readable job directories**: confirm that `data/jobs/` is not served
  as a static directory. Currently it is not (only `web/static/` is mounted), but
  double-check this after any nginx or static-file config changes.

---

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
