#!/bin/bash
# Polls origin/main and runs deploy.sh when new commits show up. Invoked on a
# timer (see voxhumana-deploy.timer) rather than a long-lived loop, so a slow
# or hung deploy can't wedge every future check.
#
# The whole body is wrapped in main() and called as the last line on purpose:
# this script lives in the repo it pulls into, and bash reads a script
# incrementally as it executes -- if `git pull` rewrites this file mid-run,
# an un-wrapped script can read corrupted/half-updated lines. A function
# body is parsed in full before it runs, so wrapping the logic sidesteps
# that entirely.
set -euo pipefail

main() {
  cd "$(dirname "$0")/.."

  # Only deploy from a main checkout. On any other branch HEAD never equals
  # origin/main, so every tick would look like "new commits" and redeploy.
  local branch
  branch=$(git symbolic-ref --quiet --short HEAD || echo "(detached)")
  if [ "$branch" != "main" ]; then
    echo "$(date -Iseconds) checkout is on '$branch', not main; skipping" >&2
    exit 0
  fi

  git fetch --quiet origin main

  local local_rev remote_rev
  local_rev=$(git rev-parse HEAD)
  remote_rev=$(git rev-parse origin/main)

  if [ "$local_rev" = "$remote_rev" ]; then
    exit 0
  fi

  # Deploy only when origin/main has moved *ahead* of us. If local main has
  # its own commits (ahead or diverged), there's nothing new upstream to
  # deploy, and `pull --ff-only` would either no-op or fail every tick.
  if ! git merge-base --is-ancestor "$local_rev" "$remote_rev"; then
    echo "$(date -Iseconds) local main ${local_rev:0:7} is not behind origin/main ${remote_rev:0:7}; skipping" >&2
    exit 0
  fi

  echo "$(date -Iseconds) new commits ${local_rev:0:7} -> ${remote_rev:0:7}, deploying"
  git pull --ff-only origin main
  scripts/deploy.sh
}

main "$@"
