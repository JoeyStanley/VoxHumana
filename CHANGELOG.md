# Changelog

All notable changes to VoxHumana will be documented here.

## [Unreleased]

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
