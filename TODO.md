
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

## MFA: additional acoustic models and dictionaries (done)

Done: the Alignment section's acoustic model and dictionary dropdowns list all 5 supported
languages (`english_us_arpa`, `spanish_mfa`, `french_mfa`, `german_mfa`, `portuguese_mfa`),
auto-paired to the Whisper language selection via `syncLanguage()`/`LANG_TO_MFA` in
`web/static/index.html`, with no "coming soon" gating. See "Multi-language support: next
languages" below for the up-to-date checklist for adding further languages.

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

## MFA: custom dictionaries (done)

OOV word extraction to `mfa_output/oovs_found.txt` is implemented and confirmed working
end-to-end on 2026-09-04: a real recording containing the coined word "mormonese" correctly
surfaced it in `oovs_found.txt`, both on a fresh Whisper transcription and via Trolley mode's
"skip Transcription" path re-running just MFA on the same audio.

Done on 2026-09-16: users can now supply custom pronunciations for out-of-vocabulary words via
an `oov_mode` control in the Alignment step's Advanced options — "Let MFA guess" (G2P, default),
"Upload a custom dictionary file" (merge with or replace the built-in dictionary), or "Type
custom words" (always merges). Custom pronunciations are validated against the selected
dictionary's phone set synchronously at job submission (`merge_or_validate_pronunciations()` in
`pipeline/align_with_mfa.py`, called from `web/app.py`'s `create_job`) so a bad phone symbol
fails immediately with a specific error instead of surfacing deep into a background alignment
run. See `pipeline/languages.py` for the G2P model mapping and dictionary-file resolution.

Not done: this hasn't been tested end-to-end yet (marked "(experimental)" in the UI in the
meantime). Before removing that label, work through:
- One-time setup: confirm `mfa model download g2p english_us_arpa/french_mfa/german_mfa` are
  installed on the server — without them, "Let MFA guess" silently no-ops like the old default.
- Golden path: reuse the "mormonese" test recording above through all three modes (guess,
  upload+merge, upload+replace with a small dictionary, type) and confirm the expected
  pronunciation/alignment behavior in each case.
- Fail-fast: submit a deliberately wrong phone (e.g. `hello` with `H` instead of `HH`) via
  Upload or Type and confirm it's rejected in seconds, before transcription/alignment runs, with
  the bad phone named in the error — not after a job partially completes.
- Validation edge cases: upload mode with no file, wrong extension, type mode with an empty
  textarea — each should 400 immediately.
- Confirm `processing_log.txt` on a completed job records the mode used, the uploaded filename
  or typed pronunciations verbatim, and the MFA dictionary docs link.
- Confirm `data/jobs/<id>/mfa_oov/` is deleted after the job finishes, and that a batch of 2+
  files with Upload mode selected once validates/runs each row independently.

Not done: per-language phone-set instructions in the OOV upload/type UI are currently generic
("e.g. CMU ARPABET for english_us_arpa") rather than dynamically rewritten per selected
dictionary — writing accurate phone-set copy for each of the 5 supported dictionaries is real
content work, not yet done.

---

## Multi-language support: next languages (priority)

Bumped to priority on 2026-10-01: adding a language used to mean someone with server access
had to be walked through a manual sequence of `mfa model download` commands by hand. That's no
longer true — `scripts/setup_mfa_models.sh` derives the full model list (acoustic, dictionary,
G2P) straight from `pipeline/languages.py` and downloads whatever's missing, idempotently. Now
that the install workflow is understood end-to-end (including how to run it under the `shiny`
service account on the production server), the server-side part of adding a language is a
non-issue — what's left is genuinely just the per-language wiring/testing checklist below.

Whisper, MFA, and the UI language selector are wired up for multiple languages; English,
Spanish, French, German, and Portuguese (`english_us_arpa`/`spanish_mfa`/`french_mfa`/
`german_mfa`/`portuguese_mfa`) are all live end-to-end for Transcription + Alignment (Formant
extraction is disabled for non-English — see below).

### Adding a new language (checklist)
1. Download MFA models on the server:
   `mfa model download acoustic <name>` and `mfa model download dictionary <name>`.
1b. If the language should support "Let MFA guess" out-of-vocabulary handling, also download
    a G2P model (`mfa model download g2p <name>`) and add an entry to
    `MFA_G2P_MODEL_BY_DICTIONARY` in `pipeline/languages.py`. Check MFA's G2P catalog first
    (`mfa model download g2p` with no name lists it) — G2P model names don't always match
    dictionary names (e.g. no exact `spanish_mfa`/`portuguese_mfa` G2P model exists, only
    regional variants — see the item below).
2. Add to the two allowlists in `web/app.py`: `SUPPORTED_MFA_ACOUSTIC_MODELS` and
   `SUPPORTED_MFA_DICTIONARIES`.
3. Add one `<option>` to each of the three dropdowns in `web/static/index.html`
   (Language, Acoustic model, Dictionary) and one entry to the `LANG_TO_MFA` JS map.
4. Test end-to-end on a real recording in that language.

### Regional variants for Spanish and Portuguese (Whisper + MFA) (priority)

MFA has no G2P model named exactly `spanish_mfa`/`portuguese_mfa` — only regional variants
(`spanish_latin_america_mfa`, `spanish_spain_mfa`, `portuguese_brazil_mfa`,
`portuguese_portugal_mfa`). As of 2026-09-16, "Let MFA guess" OOV handling is unavailable for
Spanish and Portuguese for this reason (the UI shows a note explaining this). Properly fixing
this means adding regional variants to both Whisper's language handling and MFA's acoustic
model/dictionary/G2P selection for these two languages, not just picking one regional G2P
model as a default — a larger initiative than the OOV feature itself, but no longer blocked by
not knowing how to get new models onto the server (see note above) — just needs the design work
(how regional choice surfaces in the UI alongside the existing Language dropdown) and the
per-variant wiring/testing.

### Planned next languages
English, Spanish, French, German, and Portuguese are done (see above). Next, roughly in order:
regional Spanish/Portuguese variants (above); then Italian, Dutch, Mandarin, Japanese, Korean,
and others where both Whisper and MFA have solid models — check MFA's model catalog
(`mfa model download acoustic`/`dictionary`/`g2p` with no name) against Whisper's supported
languages before committing to one, same as was done for the regional-variant gap above.

### Also consider
- The `task` parameter in Whisper: setting `task="translate"` outputs an English transcript
  even for non-English audio. This could be a useful intermediate mode (transcribe → English
  → MFA with English models) before full multi-language MFA support is ready.

---

## Formant extraction for non-English languages (not yet implemented)

new-fave is NOT purely English-only — the underlying FastTrack analysis is language-independent.
The shipped `fasttrack_config.yml` already includes the full IPA vowel inventory in
`target_labels`, so it would detect and measure Spanish vowels (a, e, i, o, u) correctly.

### What needs to change to enable it

The three parameters VxH passes to `fave_audio_textgrid` are English-specific:

| Parameter | Current value | Fix for non-English |
|---|---|---|
| `recode_rules` | `"cmu2labov"` | `"norecode"` — identity pass-through; IPA labels preserved as-is |
| `labelset_parser` | `"cmu_parser"` | `None` — skip CMU stress-digit parsing |
| `point_heuristic` | `"fave"` | leave as `"fave"` — unknown labels fall back to `1/3` point, which is fine |

new-fave ships a built-in `norecode` scheme specifically for this purpose.

### What the output would look like
- Vowel labels in the CSV would be IPA symbols (e.g. `a`, `e`, `i`, `o`, `u` for Spanish)
  rather than Labov notation (`ae`, `iy`, etc.)
- F1–F4 measurements would be acoustically valid
- The FAVE measurement-point heuristic defaults to `1/3` through the vowel for any
  unrecognized label — reasonable but not language-tuned

### Open question: optimizer behavior
The `vowel_place.yml` patterns drive the front/back optimization step. Some Spanish vowels
happen to match (`e` → front, `i` → front, `o` → back) but `a` and `u` don't match
anything. Whether this degrades the optimizer meaningfully needs a real-data test before
shipping.

### Implementation plan
1. In `pipeline/extract_with_newfave.py` (or the config layer), branch on the MFA model
   name: if non-English, pass `recode_rules="norecode"` and `labelset_parser=None`.
2. Remove the formant lock-out from the UI (`syncLanguage()` in `index.html`) and the
   backend safety net (`FORMANT_SUPPORTED_LANGUAGES` in `app.py`).
3. Test on a real Spanish recording; check that the CSV is populated and formant values
   are plausible.
4. Decide whether to expose the `recode_rules` choice to power users (probably not needed
   for most researchers).

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

(The queue system and per-job wait-time reporting this depends on — queue position,
`wait_seconds`, etc. — are already implemented, so this item is just the email/UI layer.)

### Privacy wording (for the UI)
"Your email is used to deliver your results and is recorded in our job log alongside
your Job ID. It is not shared or used for any other purpose."

### Shut down when tab closes
Currently, the processing continues after closing the tab. If the email thing doesn't
happen soon, I should fix that so that canceled jobs don't clog the queue.

---

## Client-side job persistence: cross-machine recovery (optional)

`localStorage`-based job recovery (restoring the progress/done view after a tab reload,
and a "recent jobs" list for multiple submissions) is already implemented. The one piece
left, and it's optional:

**Cross-machine / cross-browser recovery** — `localStorage` only helps on the same browser
profile and machine. For "I want to check a job from my phone" or "I cleared my browser
data," recovery would require a job-ID-only lookup form. Open question: should it expose
only status (queued/running/done/error — safe, no data leak) or also the download link?
This ties to the "Job ID guessability" item under Security audit below (job IDs are only
~1,190 combinations/day and are not secret). Recommendation: status-only for a bare job ID;
still require the token for downloads, so the download token stays the actual bearer
credential.

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
  including on pipeline failure. Orphan cleanup (end-of-job sweep) exists but should
  be verified under crash conditions.
- **On-server processing**: all three tools (Whisper, MFA, new-fave) run entirely
  locally — audio never leaves the server. This should be stated explicitly in the
  User Guide and privacy notice.
- **Job ID guessability**: job IDs are YYMMDD_Stop1_Stop2 (~1,190 combinations
  per day). A determined person could enumerate today's IDs. Consider whether result
  downloads need any additional authentication (e.g. a one-time token) if the tool
  is used for sensitive studies.
- **Upload validation**: confirm that only audio files can be uploaded (check MIME
  type and extension), and that the 1 GB size limit is enforced server-side.

---

## Add Nigerian English (priority)

Check to see if there is a Nigerian English model for Whisper. There apparently is one
for MFA. See about incorporating that. (Might as well do British, Australian, etc as well if they're there.)

## Previous versions of software (High priority)

Add (old) FAVE and add MFA 1.0. These together would (I believe) reproduce DARLA output.

While we're at it, add other versions of MFA.

## Multiple jobs at once

This might be a server permissions thing, but I'd like to make it faster on the server. I'd also like to intelligently manage a queue of jobs: prioritize shorter ones and only tap into some of the threads/cores for the long queues when they're not otherwise being used by shorter jobs. 

## Support for .txt transcriptions

DARLA could process tranascriptions as plain .txt files. Monica has requested I add that feature.

**Status (2026-09-15): built, tested, then reverted — not currently wired in.** A beta
tester asked for this. A full implementation was built and tested end-to-end over
2026-09-08–09-15, then pulled back out of the app because after seeing it work end to
end, the plain-text option didn't feel like the right fit to ship. The working code is
parked (not deleted) in case this gets revisited. Notes below are detailed enough to
re-wire it without re-deriving the design.

### Design

A plain-text transcript has no timestamps, so it can't be split into per-utterance
intervals the way Whisper's output is. The approach taken: wrap the *entire* transcript
as a single interval, on a single "utterances" tier, spanning the full duration of the
audio — a scratch TextGrid that stands in for what `convert_whisper_to_textgrid.py`
normally produces at `whisper_output/{stem}.TextGrid`, so `align_with_mfa.py` needs no
changes to consume it. This can only replace the Transcribe step (Whisper) — Align must
still run, since MFA is what actually produces word/phone timing from the flat text.
It can't be used for Extract-only jobs (new-fave needs real word/phone tiers, which a
single untimed interval can't provide).

### Parked files (present but not imported anywhere)

- **[pipeline/praat/txt_to_TextGrid.praat](pipeline/praat/txt_to_TextGrid.praat)** — Joey's
  original Praat script (credit: Monica), adapted into a proper `form` with three
  parameters instead of hardcoded paths: `Duration` (real), `Text file` (sentence),
  `Output TextGrid` (sentence). Reads the .txt file line by line, drops `#`-prefixed
  comment lines and blank lines, joins the rest into one string (adding a trailing space
  between lines where needed), and writes a single-tier, single-interval TextGrid named
  `"utterances"` spanning `[0, Duration]`.
  - **Praat gotcha worth remembering**: form variable names only lowercase the *first
    character* of the label, not the whole thing — `"Output TextGrid"` becomes
    `output_TextGrid$`, not `output_textgrid$`. Confirmed empirically against the real
    Praat binary; got this wrong on the first pass and it silently failed with "Unknown
    variable" until fixed.
  - Duration is passed in as a number rather than having the script open the audio
    itself (`Open long sound file` + `Get total duration`), since VoxHumana already
    computes duration elsewhere (librosa) and re-reading a long audio file just for its
    length is wasted work — this was the original ask that prompted the parameterization.
- **[pipeline/txt_to_textgrid.py](pipeline/txt_to_textgrid.py)** — Python wrapper
  following the same pattern as `combine_textgrids.py`/`generate_transcript.py`
  (a thin `pipeline/*.py` ↔ `pipeline/praat/*.praat` pair calling
  `praat_utils.run_praat_script`). `txt_to_textgrid(txt_path, audio_path, job_dir,
  config=None)` computes duration via `librosa.get_duration()`, then writes to
  `job_dir/whisper_output/{audio_stem}.TextGrid` — the exact path `align_with_mfa.py`
  already reads its transcript input from.

Both files have a header comment pointing back to this TODO entry.

### How the rest was wired in (now reverted — this is how to redo it)

**Frontend — `web/static/index.html`:**
- `#textgrid-input`'s `accept` widened from `.TextGrid` to `.TextGrid,.txt`.
- A warning banner (`#txt-transcript-warning`, styled like the existing
  `#utterance-tier-picker-status` warning) shown whenever the selected file ends in
  `.txt`: explains the single-utterance-spanning-the-recording tradeoff (slower/less
  accurate alignment vs. a real timestamped TextGrid).
- `isTxtTranscript(file)` helper; `setTextGrid()` toggles the banner via it.
- `maybeLoadTierPicker()` short-circuits (hides both tier-picker sections, skips the
  `/api/textgrid-tiers` fetch) when the selected file is `.txt`, since a plain-text file
  has no tiers to read.
- `updateTextGridHint()`'s secondary text mentions the `.txt` option, but only in the
  Align-is-running branch (matches the design constraint above).
- The post-job reset routine hides the warning banner along with the rest of the
  TextGrid upload zone's reset.

**Backend — `web/app.py`:**
- `from pipeline.txt_to_textgrid import txt_to_textgrid` alongside the other pipeline
  imports.
- In `create_job()`, the "a TextGrid was uploaded, Transcribe was skipped" branch grew a
  new fork: `is_txt_transcript = Path(tg_original).suffix.lower() == ".txt"`.
  - If `.txt` and `not run_alignment` → 400 (`"A plain-text transcript has no word/phone
    tiers, so it can only be used when Align is running..."`).
  - If `.txt` (and Align is running) → the raw upload is written to
    `whisper_output/{safe_stem}_transcript.txt`, `txt_to_textgrid()` is called to
    produce `whisper_output/{safe_stem}.TextGrid`, and a new `plain_text_transcript =
    True` local var is set (threaded into `config["plain_text_transcript"]`). No tier
    picking happens — there are no tiers to pick.
  - Otherwise, the existing `.TextGrid` tier-selection logic (word/phone or utterance
    tier extraction) runs unchanged, just re-indented under an `else:`.
- `config["mfa"]` gets `beam: 100, retry_beam: 400` added *only* when
  `plain_text_transcript` is true (see "What broke" below for why) — every other job
  path is untouched and still uses MFA's own defaults.
- `_write_processing_log()`: the "STEP 1 — TRANSCRIPTION: skipped" branch distinguishes
  `"plain-text transcript"` from `"user-supplied TextGrid"` in its header and body text;
  the STEP 2 (MFA) `tg_source` description does the same; the MFA "Parameters:" block
  prints `beam`/`retry_beam` (and a short explanation) whenever they're set.
- `_write_server_log()`'s `summary.jsonl` line gets a `"plain_text_transcript": bool`
  field alongside the other per-job settings, for later analytics.

**`pipeline/align_with_mfa.py`:**
- `config.get("beam")` / `config.get("retry_beam")` (both `None` by default, meaning "use
  MFA's own defaults") get appended as `--beam <n>` / `--retry_beam <n>` to the `mfa
  align` command, for both the conda and docker runners.

### What was found while testing this (the real risk, if this comes back)

Tested end-to-end against real audio (`data/sample_audio/1min/PhonicID002-Gavin_mormonese.wav`,
~71.5s) via a real beta-tester transcript:

- MFA's defaults (`beam=10`, `retry_beam=40` — tuned for short, Whisper-length
  utterances) **completely failed** to align that one ~71-second utterance:
  `NoAlignmentsError: There were no successful alignments for 1 utterances.` A short,
  throwaway test sentence against the same audio happened to align fine at the
  defaults, which is what made this easy to miss at first — it only surfaced with a
  realistic, full-length transcript.
- Widening to `--beam 100 --retry_beam 400` (MFA's own suggested fallback in that error
  message) fixed it: produced a clean 868-interval word/phone alignment with plausible
  timings, in about a minute instead of failing in ~2 seconds.
- **This is not a guaranteed fix for longer recordings.** A full sociolinguistic
  interview (tens of minutes) forced into a single utterance may still fail, or become
  very slow, even at the wider beam — this was only confirmed to work at ~70 seconds.
  The more robust (but more invasive) alternative, not built: split the transcript text
  into multiple utterance-sized intervals (by sentence-ending punctuation, say) spread
  across the duration — even without real timestamps, bounding each alignment search to
  a shorter window is much more survivable for MFA than one very long span. Worth
  prototyping if beam-widening alone proves insufficient.

## Flexibility in tier order

Instead of imposing a tier order, let the user pick. This would be an "advanced option" for MFA.

## Stereo?

Check to make sure that stereo audio files can indeed be processed. (Per Jen)

## Browser tab title reflects job status

Right now the tab title is static, so you have to actually check the tab to see progress.
Update `document.title` to reflect what's happening:

- **One job**: show queue position while waiting ("Queued (2 of 3) — VoxHumana"), then the
  current pipeline stage while running ("Transcribing… — VoxHumana", "Aligning (MFA)… —
  VoxHumana", "Extracting formants… — VoxHumana"), then done/error state.
- **Multiple jobs** (batch): not sure yet what the best summary looks like — maybe a count
  of in-progress/done ("3 running, 1 done — VoxHumana")? Worth revisiting once the one-job
  case is in and it's clearer what reads well in a cramped tab title.