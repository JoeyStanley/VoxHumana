import subprocess
import shutil
from pathlib import Path


def align_with_mfa(audio_path, whisper_result, job_dir, config=None):
    """
    Run Montreal Forced Aligner on an audio file using its Whisper transcription.

    Requires MFA installed in a conda environment (default env name: "aligner").
    To set up: conda create -n aligner -c conda-forge montreal-forced-aligner
    Then download models once: conda run -n aligner mfa model download acoustic english_us_arpa
                                conda run -n aligner mfa model download dictionary english_us_arpa

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

    # MFA needs a corpus directory containing the audio file and a matching
    # .lab file (plain-text transcription with the same stem as the audio).
    corpus_dir = job_dir / "mfa_corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(audio_path, corpus_dir / audio_path.name)

    lab_path = corpus_dir / (audio_path.stem + ".lab")
    lab_path.write_text(whisper_result["text"].strip())

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
        # Mount job_dir as /data inside the container so MFA can read/write it.
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
            "--num_jobs", str(num_jobs),
            "--output_format", output_format,
        ]
        if fine_tune:
            cmd.append("--fine_tune")
    else:
        conda_env = config.get("conda_env", "aligner")
        # --no-capture-output lets MFA's stdout/stderr pass through conda
        # so Popen's communicate() can collect it.
        cmd = [
            "conda", "run", "-n", conda_env, "--no-capture-output",
            "mfa", "align",
            str(corpus_dir),
            dictionary,
            acoustic_model,
            str(output_dir),
            "--temporary_directory", str(temp_dir),
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
        raise RuntimeError(
            f"MFA alignment failed (exit code {proc.returncode}).\n"
            f"STDOUT:\n{stdout}\n"
            f"STDERR:\n{stderr}"
        )

    return output_dir
