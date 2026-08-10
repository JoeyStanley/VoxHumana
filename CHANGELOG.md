# Changelog

All notable changes to VoxHumana will be documented here.

## [Unreleased]

## [0.14.5] - 2026-08-10
### Added
- Added file size and file duration information to internal logs so I can get a better idea of how long jobs take as a function of their duration. (This will eventually feed into a guess for how long the queue will be.)
- IP addresses and how users access VoxHumana now stored in internal logs so that I can spot potential bots or malicious users.

## [0.14.4] - 2026-08-06
### Fixed
- Jobs that ended in errors and jobs that were otherwise Xed out do not continue to reload when a new tab/page is opened. Unfinished jobs and finished jobs that haven't been Xed out remain in case of accidental or premature closure.

## [0.14.3] - 2026-07-30
- This is the version of the first live test and what the first beta testers initially saw on August 5, 2026.
### Fixed
- Trager & Bloch codes for preliquids now use capitals (e.g. "iyL", "iyR") instead of lowercase to avoid clash with existing codes like "iyr".

## [0.14.2] - 2026-07-29
### Fixed
- Preliquids now return results in Trager & Bloch rather than ARPABET to make it more consistent with the nonpreliquid option. 

## [0.14.1] - 2026-07-27
### Fixed
- Some issues with batch file uploads not displaying information.
- Cut down on processing time on batch file uploads.

## [0.13.0] - 2026-07-24
### Added
- When uploading a TextGrid to MFA, users can now specify which tier to use.
- Upload files for multiple jobs at once.
### Fixed
- Correct version number displayed at the bottom of the app. 

## [0.12.0] - 2026-07-23
### Added
- In English analyses, prelaterals and prerhotics can be combined with their following liquids in new-fave analysis.

## [0.11.0] - 2026-07-22
### Added
- new-fave formant extraction for French, German, and Portuguese (previously English and Spanish only)
- Queue information to the UI and logs
- Version of the Whisper output that has time stamps and line breaks
- Whisper utterance tier to MFA's TextGrid, yielding a three-tier TextGrid with phonemes, words, utterances. 
- Ability to specify tiers when doing new-fave only.

## [0.10.1] - 2026-06-17
### Fixed
- Removed hard-coded paths to make it run on the server. 

## [0.10.0] - 2026-06-16
### Added
- If the job crashes, you can still download whatever files were produced.
- Show name of the file on the download screen. (Useful if you've got multiple going.)
- Play a sound when the job finishes.

## [0.9.0] - 2026-06-12
### Added 
- new-fave in Spanish
- information about position in line when multiple jobs are in the queue

## [0.8.0] - 2026-06-09
### Added 
- Support for Spanish, French, German, and Portuguese for the Whisper and MFA steps

## [0.7.0] - 2026-06-02
### Added
- Trolley feature: users can now enter or exit the pipeline at any stage (e.g., start from an existing TextGrid, or stop after transcription)
### Fixed
- Bug fixes in output from extract-only runs
- Reset user options on job reset to prevent unexpected reprocessing
- Warning shown for invalid stage combinations
- Clearer error message when required files are not submitted

## [0.6.0] - 2025-06-02
### Added
- Server automatically deletes job files after 72 hours
- Detailed log file written out per job for debugging
- Out-of-vocabulary (OOV) file returned to users after alignment
- Job IDs replaced with organ stop combinations (from the SLC Tabernacle organ)
### Fixed
- Orphaned audio files are scanned and removed at job completion

## [0.5.0] - 2025-05-26
### Added
- Configurable parameters for Whisper, MFA, and new-fave via `ft_config.yml`
- FastTrack citation added to output

## [0.4.0] - 2025-05-26
### Added
- Contextual help drawers throughout the UI
- Documentation tab panels
- Clarified UI prompts and instructions

## [0.3.0] - 2025-05-26
### Added
- Per-job MFA temp directory isolation
- File size limit raised to 1 GB with user-facing guidance
### Fixed
- Whisper segment boundaries clamped to audio duration
- MFA alignment step now includes timeout and proper process cleanup
- TextGrid glob results sorted; error raised on unexpected multiples
- Error responses sanitized; error screen UX improved
- Large intermediate files cleaned up after each pipeline run

## [0.2.0] - 2026-05-26
### Added
- First working UI (FastAPI + web frontend)
- Full pipeline: Whisper transcription → MFA alignment → new-fave formant extraction
- Single-command pipeline runner

## [0.1.0] - 2026-05-16
### Added
- Initial project setup
- README with project background
- Whisper portion implemented. 
- Convert Whisper output to TextGrid.
