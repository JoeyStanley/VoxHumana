
## Add a license (MIT is probably fine)

- Add a `LICENSE` file to the repo root (MIT license, copyright Joey Stanley)
- Add a one-liner to `README.md` at the bottom: `## License` + "MIT — see [LICENSE](LICENSE)"

---

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

## Investigate how new-fave detects overlapping speech

The `include_overlaps` parameter in `fave_audio_textgrid` excludes vowels that occur during
overlapping speech when set to False. It's not yet clear how new-fave determines what counts
as "overlapping" — specifically:

- Does it look at other tiers in the TextGrid? If so, which ones, and what label conventions
  does it expect?
- VoxHumana currently produces a single-speaker TextGrid from MFA (one Word tier, one Phone
  tier). If new-fave's overlap detection requires a second speaker tier to be present, the
  `include_overlaps=False` option may have no effect for our use case.
- If it does require a second tier: would it be worth adding an interviewer transcript tier
  to the TextGrid so that back-channels and interviewer overlaps are flagged? This would
  require either a separate transcription pass for the interviewer or manual annotation.

Check new-fave source (`mark_overlaps` in `new_fave/utils/textgrid.py`) to understand the
detection logic before advertising this option to users.

---

## "Trolley" mode: skip Whisper and go straight to MFA (not yet built)

A future workflow for power users: allow uploading a corrected transcript
(e.g. a manually edited version of Whisper's output) alongside the audio,
skipping the Whisper step entirely and feeding the corrected text straight
into MFA. This is useful when:
  - Whisper made errors that affected alignment quality (e.g. OOV words,
    proper nouns, dialect forms)
  - The user already has a transcript from another source
  - The user wants to iterate: run the full pipeline once, fix Whisper's
    output, then rerun from MFA onward without re-transcribing

The OOV words file (oovs_found.txt) is a natural trigger for this workflow —
if the user sees OOV words they recognize as errors, they can correct the
transcript and resubmit from MFA without paying the Whisper cost again.

**Note for implementation**: the orphaned audio cleanup logic (which deletes audio
files from jobs that have no newfave_output/) will need to be updated when Trolley
mode is built. A Trolley job legitimately has audio but no newfave_output at the
start of the run — so the cleanup must be aware of job status (e.g. check the
in-memory jobs dict, or a status file on disk) rather than just looking at
which output folders exist.

---

## MFA: OOV words file and custom dictionaries

Two related features worth adding when there is demand:

### OOV words file ✓ (implemented — needs real-world testing)
MFA's OOV words are now extracted from its internal log and written to
`mfa_output/oovs_found.txt`, included in the download zip when present.

**Testing note**: This is difficult to test end-to-end until the Trolley feature
exists. Whisper tends to recognize unfamiliar words as phonetically similar
dictionary words, so true OOVs rarely make it through to MFA in normal use.
Once the Trolley feature is built (allowing users to supply a corrected
transcript directly), test by feeding MFA a transcript that contains a made-up
or highly unusual word and confirming it appears in `oovs_found.txt`.

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

## Formant ceiling and number-of-formants overrides ✓ (already implemented)

Inputs are enabled in the UI, wired through the API, passed to new-fave via
`ft_config.yml`, and documented in `help-formants.md`. No further work needed.

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

### Privacy notice
Make clear that VoxHumana respects the sensitivity of sociolinguistic recordings:
  - All processing happens entirely on BYU's server — your audio is never sent to
    OpenAI or any other external service. Whisper, MFA, and new-fave all run locally.
  - Uploaded audio is deleted from the server as soon as processing finishes.
  - Result files are available for download for 72 hours, then deleted.
  - No audio or transcripts are retained, shared, or used for any other purpose.

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

## Security audit (not yet done)

VoxHumana handles sensitive sociolinguistic data — identifiable voices and personal
conversations from research participants. A dedicated security review should be done
before the tool is opened to broad public use. Key areas to audit:

- **Data in transit**: confirm all traffic runs over HTTPS (no HTTP fallback). Audio
  uploads and result downloads should never travel unencrypted.
- **Job directory access**: verify that `data/jobs/` cannot be accessed directly via
  URL — only through the API endpoints. Check this holds after any nginx/proxy config
  changes. (Currently confirmed safe — only `web/static/` is mounted as static.)
- **Audio deletion**: confirm the audio file is always deleted after processing,
  including on pipeline failure. Orphan cleanup (end-of-job sweep) is now implemented
  but should be verified under crash conditions.
- **On-server processing**: all three tools (Whisper, MFA, new-fave) run entirely
  locally — audio never leaves the server. This should be stated explicitly in the
  User Guide and privacy notice.
- **Job ID guessability**: job IDs are now YYMMDD_Stop1_Stop2 (~1,190 combinations
  per day). A determined person could enumerate today's IDs. Consider whether result
  downloads need any additional authentication (e.g. a one-time token) if the tool
  is used for sensitive studies.
- **Upload validation**: confirm that only audio files can be uploaded (check MIME
  type and extension), and that the 1 GB size limit is enforced server-side.

---

## File management and security (partially done — needs completion)

### What's already done
- Large intermediates deleted after every job: uploaded audio, `mfa_corpus/`,
  `mfa_temp/`. This recovers 1–3 GB per job immediately.
- Orphaned audio cleanup: at the end of each completed job, a sweep deletes audio
  files from any job directory that has no results (i.e. jobs that were running when
  the server last crashed). See Trolley mode caveat in that TODO item.
- No world-readable job directories: confirmed — only `web/static/` is served
  statically; `data/jobs/` is API-only.

### What still needs to be done
- **Result files expire**: job result directories accumulate forever. Once a user
  has downloaded their results (or after 72 hours), the entire job directory should
  be deleted. Coordinate with the logging system — logs must be written to
  `data/logs/` *before* the job directory is removed.

- **Logs live separately**: job logs (`data/logs/`) must never be deleted as part
  of job directory cleanup. They are the audit trail.

---

## Job logging system ✓ (implemented)
Every job (success or failure) now writes a server-side log to:

  data/logs/YYYY-MM/<job_id>.txt

Each file contains: job ID, filename, submitted/completed timestamps, total
duration, per-step timings, all settings used, tool versions, final status,
and full error traceback on failure. Logs are stored outside job directories
and are never deleted by the result cleanup sweep.

### Human-readable job codes ✓ (implemented)
Job IDs are now in the format YYMMDD_Stop1_Stop2, e.g. 260601_Bourdon_Flute.
Two distinct organ stops are drawn at random; a collision check retries if the
same pair was already used today (extremely rare at expected job volumes).

Organ stops pool (35), drawn from the Salt Lake Tabernacle organ:
  Bombarde, Bourdon, Celeste, Clarinet, Clarion, CorAnglais, Cornopean,
  Cymbelstern, Diaphone, Diapason, Doppelflote, Dulciana, Flugelhorn, Flute,
  FrenchHorn, Fugara, Gamba, Gemshorn, Harp, LieblichBourdon, Mixture,
  Nachthorn, Nazard, Oboe, Octave, Piccolo, Principal, Rauschquinte, Trombone,
  Trompette, Tremulant, Trumpet, Tuba, Tutti, Viole

35 × 34 = 1,190 combinations per day — sufficient for expected usage.

The job ID is used as the job directory name and shown to the user on both the
error screen and the success/download screen so they can reference it when
contacting the lab.

### Before building the full logging system, consider:
  - Whether logs should be plain .txt or structured (JSON, CSV) for easier parsing
  - When and how to clean up job directories after a job completes — how long
    to keep results available for download?
