import re
import subprocess
import shutil
from pathlib import Path


def align_with_mfa(audio_path, job_dir, config=None):
    """
    Run Montreal Forced Aligner on an audio file using its Whisper TextGrid.

    Requires MFA installed in a conda environment (default env name: "aligner").
    To set up: conda create -n aligner -c conda-forge montreal-forced-aligner
    Then download models once: conda run -n aligner mfa model download acoustic english_us_arpa
                                conda run -n aligner mfa model download dictionary english_us_arpa

    MFA reads the Whisper utterance TextGrid (whisper_output/{stem}.TextGrid) as
    its transcript input. Each non-empty interval is aligned as a separate utterance,
    which is more efficient than a flat .lab file for long recordings.

    Config options:
        runner (str):         "conda" (default) or "docker"
        conda_env (str):      conda environment name, default "aligner"
        dictionary (str):     MFA dictionary name or path, default "english_us_arpa"
        acoustic_model (str): MFA acoustic model name or path, default "english_us_arpa"
        num_jobs (int):       parallel jobs, default 1
        output_format (str):  "long_textgrid" (default), "short_textgrid", "json", or "csv"
        docker_image (str):   Docker image, default "mmcauliffe/montreal-forced-aligner:latest"
        timeout (int):        seconds before giving up, default 7200 (2 hours)

    Returns:
        Path to the MFA output directory containing aligned TextGrid(s).
    """
    if config is None:
        config = {}

    audio_path = Path(audio_path)
    job_dir = Path(job_dir)

    # MFA corpus directory: audio file + matching TextGrid transcript.
    corpus_dir = job_dir / "mfa_corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(audio_path, corpus_dir / audio_path.name)

    # Copy the Whisper utterance TextGrid as MFA's transcript input.
    # MFA accepts .TextGrid files alongside audio; each non-empty interval is
    # treated as a separate utterance to align.
    textgrid_src = job_dir / "whisper_output" / f"{audio_path.stem}.TextGrid"
    shutil.copy2(textgrid_src, corpus_dir / textgrid_src.name)

    output_dir = job_dir / "mfa_output"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Job-specific temp dir prevents MFA from merging this corpus with cached
    # data from previous runs stored in ~/Documents/MFA (the global default).
    temp_dir = job_dir / "mfa_temp"

    dictionary = config.get("dictionary", "english_us_arpa")
    acoustic_model = config.get("acoustic_model", "english_us_arpa")
    fine_tune = config.get("fine_tune", False)
    num_jobs = config.get("num_jobs", 1)
    output_format = config.get("output_format", "long_textgrid")
    runner = config.get("runner", "conda")

    if runner == "docker":
        docker_image = config.get("docker_image", "mmcauliffe/montreal-forced-aligner:latest")
        cmd = [
            "docker", "run", "--rm",
            "-v", f"{job_dir.resolve()}:/data",
            docker_image,
            "mfa", "align",
            "/data/mfa_corpus",
            dictionary,
            acoustic_model,
            "/data/mfa_output",
            "--temporary_directory", "/data/mfa_temp",
            "--clean",
            "--num_jobs", str(num_jobs),
            "--output_format", output_format,
        ]
        if fine_tune:
            cmd.append("--fine_tune")
    else:
        conda_env = config.get("conda_env", "aligner")
        cmd = [
            "conda", "run", "-n", conda_env, "--no-capture-output",
            "mfa", "align",
            str(corpus_dir),
            dictionary,
            acoustic_model,
            str(output_dir),
            "--temporary_directory", str(temp_dir),
            "--clean",
            "--num_jobs", str(num_jobs),
            "--output_format", output_format,
        ]
        if fine_tune:
            cmd.append("--fine_tune")

    timeout = config.get("timeout", 7200)  # 2 hours default

    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as proc:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()  # drain pipes so the process exits cleanly
            raise RuntimeError(
                f"MFA alignment timed out after {timeout // 60} minutes. "
                "The recording may be too long. Try splitting it into shorter segments."
            )

    if proc.returncode != 0:
        # Surface MFA's own validator/aligner log for better diagnostics.
        mfa_log_snippets = []
        for log_file in sorted(temp_dir.rglob("*.log")):
            try:
                text = log_file.read_text(errors="replace").strip()
                if text:
                    mfa_log_snippets.append(f"--- {log_file.relative_to(temp_dir)} ---\n{text}")
            except Exception:
                pass
        mfa_log = ("\n\nMFA internal logs:\n" + "\n\n".join(mfa_log_snippets)) if mfa_log_snippets else ""

        raise RuntimeError(
            f"MFA alignment failed (exit code {proc.returncode}).\n"
            f"STDOUT:\n{stdout}\n"
            f"STDERR:\n{stderr}"
            f"{mfa_log}"
        )

    # Extract OOV words from MFA's internal log and write a clean summary to
    # mfa_output/ before the temp directory is deleted during cleanup.
    # MFA buries this in normalize_oov.log as a Python repr; we pull out the
    # 'word' values with regex and write one word per line.
    oov_logs = list(temp_dir.rglob("normalize_oov.log"))
    if oov_logs:
        raw = oov_logs[0].read_text(errors="replace")
        words = sorted(set(re.findall(r"'word':\s*'([^']+)'", raw)))
        if words:
            (output_dir / "oovs_found.txt").write_text("\n".join(words) + "\n")

    return output_dir
