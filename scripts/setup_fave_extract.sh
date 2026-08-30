#!/usr/bin/env bash
# One-time server setup for the optional FAVE-extract formant-extraction
# backend. Safe to re-run -- every step is skipped if already done.
#
# What this touches:
#   - Creates .venv-fave/ inside the repo (a new, isolated Python 3.10 venv --
#     does not touch the main project venv or the MFA conda env).
#   - Downloads a Praat "barren" (headless, server) build to
#     $HOME/praat-bin/ and symlinks it to /usr/local/bin/praat (the one step
#     that needs sudo, so systemd-run services can find it on PATH too, not
#     just an interactive login shell).
#
# Nothing here modifies the running app, its config, or any existing
# environment. Run it, then follow the normal update flow in
# TODO_for_server.md #12 (git pull / uv sync / restart the service) if you
# haven't already.
#
# Usage: bash scripts/setup_fave_extract.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "== FAVE-extract setup =="
echo "Repo root: $REPO_ROOT"
echo

IS_LINUX=true
if [ "$(uname -s)" != "Linux" ]; then
  IS_LINUX=false
  echo "Non-Linux OS detected -- step 2 (downloading Praat's Linux headless build)"
  echo "will be skipped. On a Mac, install Praat normally (https://www.praat.org)"
  echo "and either put it on PATH or pass config[\"praat_path\"] explicitly (see"
  echo "pipeline/extract_with_fave.py). Step 1 (.venv-fave) still runs normally."
  echo
fi

# ── 1. Isolated venv for FAVE-extract ────────────────────────────────────────
if [ -x ".venv-fave/bin/python" ] && .venv-fave/bin/python -c "import fave" 2>/dev/null; then
  echo "[1/3] .venv-fave already has FAVE-extract installed -- skipping."
else
  echo "[1/3] Creating .venv-fave (Python 3.10) and installing FAVE-extract..."
  if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: 'uv' not found. This should already be installed for the main app"
    echo "(see TODO_for_server.md section 2). Install it first, then re-run this script."
    exit 1
  fi
  uv venv --python 3.10 .venv-fave
  uv pip install --python .venv-fave/bin/python \
    "git+https://github.com/JoFrhwld/FAVE@v2.0.3" "setuptools<81"
  echo "    -> installed: $(.venv-fave/bin/python -c 'import importlib.metadata as m; print(m.version("fave"))')"
fi
echo

# ── 2. Praat (headless "barren" build) ───────────────────────────────────────
if command -v praat >/dev/null 2>&1; then
  echo "[2/3] 'praat' already on PATH ($(command -v praat), $(praat --version 2>&1 | head -1)) -- skipping download."
elif [ "$IS_LINUX" = false ]; then
  echo "[2/3] Skipped (not Linux) -- install Praat yourself, see note above."
else
  echo "[2/3] Downloading Praat's headless Linux build..."
  case "$(uname -m)" in
    x86_64)  PRAAT_ARCH="linux-x64v3-barren" ;;
    aarch64) PRAAT_ARCH="linux-arm64-barren" ;;
    *)
      echo "ERROR: unrecognized architecture '$(uname -m)'."
      echo "Check https://www.fon.hum.uva.nl/praat/download_linux.html for the right build,"
      echo "download it to \$HOME/praat-bin/, and re-run this script."
      exit 1
      ;;
  esac

  PRAAT_URL=$(curl -fsSL https://api.github.com/repos/praat/praat/releases/latest \
    | python3 -c "
import json, sys
data = json.load(sys.stdin)
matches = [a['browser_download_url'] for a in data['assets'] if '$PRAAT_ARCH' in a['name']]
if not matches:
    sys.exit('No asset found matching $PRAAT_ARCH in the latest Praat release')
print(matches[0])
")
  echo "    -> $PRAAT_URL"

  mkdir -p "$HOME/praat-bin"
  TMP_TARBALL="$(mktemp)"
  curl -fsSL "$PRAAT_URL" -o "$TMP_TARBALL"
  tar -xzf "$TMP_TARBALL" -C "$HOME/praat-bin"
  rm -f "$TMP_TARBALL"
  chmod +x "$HOME/praat-bin/praat"

  echo "    Downloaded to $HOME/praat-bin/praat -- linking into /usr/local/bin"
  echo "    (this is the only step in this script that needs sudo; it makes"
  echo "    'praat' visible on PATH for the systemd-run app, not just your login shell):"
  sudo ln -sf "$HOME/praat-bin/praat" /usr/local/bin/praat
  echo "    -> $(praat --version)"
fi
echo

# ── 3. Verify ────────────────────────────────────────────────────────────────
echo "[3/3] Verifying..."
.venv-fave/bin/python -c "import fave; import importlib.metadata as m; print('FAVE-extract:', m.version('fave'))"
if command -v praat >/dev/null 2>&1; then
  praat --version
else
  echo "praat: not on PATH yet (expected if you skipped step 2 above)"
fi
echo
echo "Done. FAVE-extract is ready to use. If you haven't already, follow the normal"
echo "update flow (TODO_for_server.md #12): git pull / uv sync / restart the service."
