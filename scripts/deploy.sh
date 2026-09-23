#!/bin/bash
# Brings the environment in line with the just-pulled commit and restarts
# the service. Assumes the git pull already happened (see
# scripts/poll_deploy.sh) -- this only does the steps after that.
#
# NOTE: per TODO_for_server.md #12, some updates need manual steps beyond
# this (new MFA models/dictionaries, new Whisper model sizes, new system
# packages) -- those are flagged in CHANGELOG.md but NOT handled here, since
# an unattended timer can't read prose and decide what to run. Watch the
# CHANGELOG after enabling auto-deploy so those don't silently go missing.
set -euo pipefail
cd "$(dirname "$0")/.."

uv sync
systemctl --user restart voxhumana
