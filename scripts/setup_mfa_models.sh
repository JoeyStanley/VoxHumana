#!/usr/bin/env bash
# One-time server setup for MFA's pretrained models (acoustic, dictionary,
# and G2P models for every language VoxHumana supports). Safe to re-run --
# `mfa model download` is itself idempotent (it checks its local cache and
# skips the download if the model is already present), so running this
# again later, or by accident, just confirms everything's there and exits
# in a few seconds. No need to "disable" it after first use.
#
# Needed before alignment jobs will succeed for a given language (acoustic +
# dictionary), and before "Let MFA guess" out-of-vocabulary handling (see
# pipeline/align_with_mfa.py) will do anything beyond its no-op fallback for
# a given language (G2P). Reads the language list straight from
# pipeline/languages.py so this can't drift out of sync with the code as
# new languages are added there.
#
# Usage: bash scripts/setup_mfa_models.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

CONDA_ENV="${MFA_CONDA_ENV:-aligner}"

echo "== MFA pretrained model setup =="
echo "Repo root:   $REPO_ROOT"
echo "Conda env:   $CONDA_ENV"
echo

if ! command -v conda >/dev/null 2>&1; then
  echo "ERROR: 'conda' isn't on PATH in this shell."
  echo "If conda is already installed, your interactive shell probably just hasn't"
  echo "sourced its init script yet (this is separate from whether the app itself"
  echo "can find it -- the systemd service has its own PATH). Try:"
  echo "  source \$HOME/miniconda3/etc/profile.d/conda.sh"
  echo "(see TODO_for_server.md section 3), then re-run this script."
  exit 1
fi

if ! MFA_VERSION_OUTPUT=$(conda run -n "$CONDA_ENV" mfa version 2>&1); then
  echo "ERROR: 'conda run -n $CONDA_ENV mfa version' failed:"
  echo "$MFA_VERSION_OUTPUT" | sed 's/^/    /'
  echo
  echo "If the env doesn't exist yet, see TODO_for_server.md section 3 to create it:"
  echo "  conda create -n $CONDA_ENV -c conda-forge montreal-forced-aligner -y"
  exit 1
fi
echo "MFA version: $MFA_VERSION_OUTPUT"

DICTIONARIES=$(python3 -c "
import sys; sys.path.insert(0, '.')
from pipeline.languages import MFA_MODEL_BY_LANGUAGE
print(' '.join(sorted(set(MFA_MODEL_BY_LANGUAGE.values()))))
")
G2P_MODELS=$(python3 -c "
import sys; sys.path.insert(0, '.')
from pipeline.languages import MFA_G2P_MODEL_BY_DICTIONARY
print(' '.join(sorted(set(MFA_G2P_MODEL_BY_DICTIONARY.values()))))
")

echo "Dictionaries/acoustic models: $DICTIONARIES"
echo "G2P models:                   $G2P_MODELS"
echo "(G2P list is shorter than the dictionary list on purpose -- not every"
echo " dictionary has a same-named G2P model in MFA's catalog. See the"
echo " comment above MFA_G2P_MODEL_BY_DICTIONARY in pipeline/languages.py.)"
echo

echo "[1/3] Acoustic models..."
for m in $DICTIONARIES; do
  conda run -n "$CONDA_ENV" mfa model download acoustic "$m"
done
echo

echo "[2/3] Dictionaries..."
for m in $DICTIONARIES; do
  conda run -n "$CONDA_ENV" mfa model download dictionary "$m"
done
echo

echo "[3/3] G2P models (for \"Let MFA guess\" out-of-vocabulary handling)..."
for m in $G2P_MODELS; do
  conda run -n "$CONDA_ENV" mfa model download g2p "$m"
done
echo

echo "Done. Verify with:"
echo "  conda run -n $CONDA_ENV mfa model list acoustic"
echo "  conda run -n $CONDA_ENV mfa model list dictionary"
echo "  conda run -n $CONDA_ENV mfa model list g2p"
