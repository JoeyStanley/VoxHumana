# VoxHumana — Server Deployment Checklist

This document covers everything needed to get VoxHumana running on a fresh server.
Work through the sections in order. Steps marked [ONE-TIME] only need to be done
once when first setting up the server; everything else applies to each new deployment.

---

## 1. Server requirements [ONE-TIME]

**OS:** Ubuntu 22.04 LTS or later (other Linux distros work; macOS works for local testing)

**Minimum hardware:**
- 8 GB RAM (Whisper turbo model needs ~4 GB; leave headroom for MFA + OS)
- 4 CPU cores (MFA uses 1 job by default; more cores help if num_jobs is raised)
- 50 GB disk minimum; 200 GB recommended (each job can use 2–4 GB temporarily)

**GPU (optional but recommended):**
- Whisper will automatically use CUDA if available, reducing transcription time from
  ~1× real-time to ~0.1× real-time
- Requires NVIDIA GPU, CUDA toolkit, and a torch installation with GPU support
- Without a GPU, a 1-hour interview takes roughly 60 minutes to transcribe

**Required system packages:**
```bash
sudo apt update && sudo apt install -y git ffmpeg build-essential
```
ffmpeg is required by both Whisper (audio decoding) and librosa (duration detection).

---

## 2. Python environment [ONE-TIME]

VoxHumana uses `uv` for dependency management. Install it and sync the project:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.cargo/env   # or open a new shell

git clone https://github.com/JoeyStanley/VoxHumana.git
cd VoxHumana
uv sync
```

To verify the install:
```bash
uv run python -c "import whisper, new_fave, tgt, librosa, fastapi; print('OK')"
```

---

## 3. MFA conda environment [ONE-TIME]

MFA must run in its own conda environment (it has dependency conflicts with the
main project). Install Miniconda if not already present:

```bash
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh -b -p $HOME/miniconda3
source $HOME/miniconda3/etc/profile.d/conda.sh
echo 'source $HOME/miniconda3/etc/profile.d/conda.sh' >> ~/.bashrc
```

Create the aligner environment and download the models:

```bash
conda create -n aligner -c conda-forge montreal-forced-aligner -y
conda run -n aligner mfa model download acoustic english_us_arpa
conda run -n aligner mfa model download dictionary english_us_arpa
```

Models are stored in `~/Documents/MFA/pretrained_models/` (MFA's global model store).
This is expected — models are shared across all jobs on the server and are never
duplicated. Per-job working data is isolated via `--temporary_directory` in the code.

To verify:
```bash
conda run -n aligner mfa version
conda run -n aligner mfa model list acoustic
conda run -n aligner mfa model list dictionary
```

---

## 4. Whisper model pre-download [ONE-TIME]

Whisper downloads its model on first use to `~/.cache/whisper/`. Pre-download it
so the first real user doesn't wait an extra few minutes:

```bash
uv run python -c "import whisper; whisper.load_model('turbo'); print('Whisper model ready')"
```

The turbo model is ~1.6 GB. Other models users may select (small, medium, large)
should also be pre-downloaded if you expect them to be used:
```bash
uv run python -c "import whisper; [whisper.load_model(m) for m in ['small','medium']]"
```

---

## 5. Directory setup

`data/jobs/` and `data/logs/` are both created automatically by the app on
startup, but verify the server user has write permissions:

```bash
mkdir -p data/jobs data/logs
chmod 755 data/jobs data/logs
```

If running as a dedicated service user (recommended), ensure that user owns the
entire VoxHumana directory:
```bash
sudo chown -R vxhuser:vxhuser /path/to/VoxHumana
```

---

## 6. Running the server

**Development / testing:**
```bash
uv run uvicorn web.app:app --host 0.0.0.0 --port 8000 --reload
```

**Production (no auto-reload, single worker required):**
```bash
uv run uvicorn web.app:app --host 127.0.0.1 --port 8000 --workers 1
```

IMPORTANT: Always use `--workers 1`. The pipeline uses an in-memory job store and
a single-threaded executor — multiple workers would have separate job stores and
break job status polling.

---

## 7. Systemd service (keep server running) [ONE-TIME]

Create `/etc/systemd/system/voxhumana.service`:

```ini
[Unit]
Description=VoxHumana web server
After=network.target

[Service]
Type=simple
User=vxhuser
WorkingDirectory=/path/to/VoxHumana
ExecStart=/path/to/VoxHumana/.venv/bin/uvicorn web.app:app --host 127.0.0.1 --port 8000 --workers 1
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then enable and start it:
```bash
sudo systemctl daemon-reload
sudo systemctl enable voxhumana
sudo systemctl start voxhumana
sudo systemctl status voxhumana
```

To view logs:
```bash
sudo journalctl -u voxhumana -f
```

---

## 8. Nginx reverse proxy [ONE-TIME]

Nginx sits in front of uvicorn, handles HTTPS, and must be configured to accept
large uploads (default nginx limit is 1 MB — VoxHumana allows up to 1 GB).

Install nginx:
```bash
sudo apt install -y nginx
```

Create `/etc/nginx/sites-available/voxhumana`:
```nginx
server {
    listen 80;
    server_name your-domain.byu.edu;

    # Must match or exceed MAX_UPLOAD_BYTES in web/app.py
    client_max_body_size 1G;

    # Increase timeouts for long-running uploads
    client_body_timeout 300s;
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

Enable it:
```bash
sudo ln -s /etc/nginx/sites-available/voxhumana /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

## 9. HTTPS with Let's Encrypt [ONE-TIME]

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.byu.edu
```

Certbot will automatically update the nginx config and set up auto-renewal.

Note: BYU IT may require using their own certificate infrastructure instead of
Let's Encrypt. Check with BYU IT before proceeding.

---

## 10. Disk space management

Cleanup runs automatically — no manual intervention needed under normal operation.

**Immediately after every job completes** (success or failure), the app deletes:
- The uploaded audio file
- `mfa_corpus/`  — copy of the audio used as MFA input (~same size as upload)
- `mfa_temp/`    — MFA working files (can be large; ~2–5× audio size)

This recovers 1–3 GB per job right away.

**What remains in the job directory after cleanup:**
- `whisper_output/`  — Whisper JSON, plain-text transcript, utterance TextGrid (small)
- `mfa_output/`      — word/phone-aligned TextGrid, `oovs_found.txt` if any (small)
- `newfave_output/`  — formant CSVs (small)
- `processing_log.txt` — per-job log included in the user's download zip (small)
- `error.log`        — full traceback, only present if the job failed (small)

**Job directories expire automatically** after 72 hours. At the end of every job,
`_expire_old_jobs()` sweeps `data/jobs/` and deletes any directory whose
last-modified time is older than the `JOB_RETENTION_HOURS` constant (currently 72).

**Server logs are permanent.** Every job writes two logs to `data/logs/`, which is
outside `data/jobs/` and is never touched by the cleanup sweep:
- `data/logs/YYYY-MM/<job_id>.txt` — full diagnostic log (settings, timings, errors)
- `data/logs/summary.jsonl`        — one JSON line per job for analytics

Monitor `data/logs/` growth over time. At ~1 KB per job, it takes many thousands
of jobs to become significant, but it is never auto-deleted.

If the server crashes mid-job, orphaned audio files (uploaded audio with no
completed output) are detected and deleted the next time any job finishes.

---

## 11. Verifying the deployment

Run a test job end-to-end using the CLI (no web server needed):
```bash
uv run python main.py data/test1/UT007-Aiden.wav data/test_deploy_check
```

Expected output (the CLI does not clean up intermediates, so all folders will be present):
- `data/test_deploy_check/whisper_output/UT007-Aiden.json` — Whisper transcript
- `data/test_deploy_check/whisper_output/UT007-Aiden.txt` — plain-text transcript
- `data/test_deploy_check/whisper_output/UT007-Aiden.TextGrid` — utterance boundaries
- `data/test_deploy_check/mfa_corpus/` — MFA input copy (large; safe to delete after testing)
- `data/test_deploy_check/mfa_temp/` — MFA working files (large; safe to delete after testing)
- `data/test_deploy_check/mfa_output/UT007-Aiden.TextGrid` — word/phone alignment
- `data/test_deploy_check/mfa_output/oovs_found.txt` — OOV words (if any)
- `data/test_deploy_check/newfave_output/` — formant CSV files

Note: `processing_log.txt` and server logs are only written by the web app, not the CLI.

Then verify the web server is reachable and can accept a job via the browser.

---

## 12. Updating VoxHumana

After every update, the minimum steps are:

```bash
git pull origin main
uv sync                          # installs any new Python dependencies
sudo systemctl restart voxhumana
```

`uv sync` reads the committed `uv.lock` file and brings the environment into
exact agreement with it — new packages are installed, removed packages are
uninstalled. No manual pip commands needed.

**Check the CHANGELOG before restarting.** Some updates require extra steps
beyond `uv sync`. After each pull, skim `CHANGELOG.md` for notes flagged as
requiring manual action. Common cases:

- **New MFA acoustic model or dictionary** (e.g., adding a new language):
  ```bash
  conda run -n aligner mfa model download acoustic <model_name>
  conda run -n aligner mfa model download dictionary <model_name>
  ```

- **New Whisper model size added as a user option**: pre-download it so the
  first user doesn't wait:
  ```bash
  uv run python -c "import whisper; whisper.load_model('<model_name>')"
  ```

- **New system package required**: check the CHANGELOG note and install via
  `apt` before restarting.

If the CHANGELOG doesn't flag anything extra, `git pull && uv sync && restart`
is all that's needed.

---

## 13. Accessing log files

Every job writes a diagnostic log to `data/logs/` on the server. When users
report problems, you'll need these to diagnose what went wrong.

**Log locations on the server:**
- `data/logs/YYYY-MM/<job_id>.txt` — full log for one job (settings, step
  timings, tool versions, full error traceback if it failed)
- `data/logs/summary.jsonl`        — one JSON line per job, all-time

**Granting Joey SSH access [ONE-TIME]:**

Add Joey's public key to the server user's authorized keys:
```bash
# On the server, as the vxhuser (or ask IT to do this):
mkdir -p ~/.ssh
echo "<joey's public key>" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

Joey's public key is at `~/.ssh/id_ed25519.pub` (or `id_rsa.pub`) on his
machine. Run `cat ~/.ssh/id_ed25519.pub` locally to get it.

**Reading logs remotely:**

Once SSH access is set up, Joey can pull logs to his local machine with:
```bash
# Pull all logs for a given month
scp -r vxhuser@your-server:/path/to/VoxHumana/data/logs/2026-06 ~/vxh_logs/

# Or pull a single job's log by job ID
scp vxhuser@your-server:/path/to/VoxHumana/data/logs/2026-06/260601_Bourdon_Flute.txt .
```

Or read a log in-place over SSH without copying it:
```bash
ssh vxhuser@your-server "cat /path/to/VoxHumana/data/logs/2026-06/260601_Bourdon_Flute.txt"
```
