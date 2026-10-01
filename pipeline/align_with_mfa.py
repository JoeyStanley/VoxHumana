import re
import subprocess
import shutil
from pathlib import Path

from pipeline.languages import MFA_G2P_MODEL_BY_DICTIONARY, mfa_dictionary_file, mfa_g2p_model_file


def merge_or_validate_pronunciations(dictionary_name, new_pronunciations_path, target_dir,
                                      conda_env="aligner", timeout=300):
    """
    Copy the installed `dictionary_name` dictionary into `target_dir` and merge
    in the entries from `new_pronunciations_path` via `mfa model add_words`.

    Always operates on a fresh copy of the installed dictionary - never
    mutates the shared, globally installed model. Used both to build a real
    merged dictionary (out-of-vocabulary "upload + merge" and "type" modes)
    and, for "upload + replace", purely to validate that the uploaded
    pronunciations' phones are compatible with the dictionary's phone set
    (the caller discards the merged copy in that case and uses the original
    upload as-is).

    Returns the path to the merged copy (target_dir / "<dictionary_name>_merged.dict").

    Raises RuntimeError with a user-facing message - naming the specific bad
    phone(s) when MFA reports a PhoneMismatchError - on any failure. Safe to
    catch and re-raise as an HTTP 400 from a request handler.
    """
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    builtin_dict = mfa_dictionary_file(dictionary_name)
    if not builtin_dict.exists():
        raise RuntimeError(
            f"Could not find the installed '{dictionary_name}' dictionary on the server "
            f"(expected at {builtin_dict}). Contact the site administrator."
        )

    target = target_dir / f"{dictionary_name}_merged.dict"
    shutil.copy2(builtin_dict, target)

    cmd = [
        "conda", "run", "-n", conda_env, "--no-capture-output",
        "mfa", "model", "add_words",
        str(target), str(new_pronunciations_path), "--overwrite",
    ]

    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as proc:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise RuntimeError("Adding custom pronunciations to the dictionary timed out.")

    if proc.returncode != 0:
        combined = f"{stdout}\n{stderr}"
        if "PhoneMismatchError" in combined:
            # MFA reports unrecognized phones one per line immediately after
            # this header, with no blank-line separator before `conda run`'s
            # own "ERROR conda.cli.main_run: ... failed" line gets appended
            # right after them - stop there so that trailing line doesn't
            # get mistaken for a phone. Pulling out just the phone names lets
            # the message name the exact symbol that's wrong (e.g. a
            # shortened ARPABET symbol like "H" instead of "HH").
            after = combined.split(
                "There were extra phones that were not in the dictionary:", 1
            )
            phones = []
            if len(after) == 2:
                for line in after[1].splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("ERROR conda"):
                        break
                    phones.append(line)
            phones_str = ", ".join(phones) if phones else "(see details below)"
            raise RuntimeError(
                "One or more custom pronunciations use phone symbols that aren't part of "
                f"the '{dictionary_name}' dictionary's phone set (unrecognized: {phones_str}). "
                "Double check each phone against that dictionary's phone inventory — a common "
                "mistake is a shortened ARPABET symbol (e.g. 'H' instead of 'HH')."
            )
        raise RuntimeError(
            f"Could not add your custom pronunciations to the dictionary (exit code "
            f"{proc.returncode}).\n{stderr.strip()}"
        )

    return target


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
        oov_mode (str):       "guess" (default), "upload", or "type" - how out-of-
                               vocabulary words are handled. "guess" uses MFA's G2P
                               model (see MFA_G2P_MODEL_BY_DICTIONARY) if one is
                               available for `dictionary`. "upload"/"type" expect
                               oov_resolved_dictionary_path to already point at a
                               dictionary file prepared (and phone-validated) by
                               merge_or_validate_pronunciations() before this
                               function is ever called - see web/app.py's
                               create_job, which runs that synchronously at job
                               submission time so a bad phone fails fast instead
                               of surfacing deep into a background alignment run.
        oov_resolved_dictionary_path (str): path to a pre-built dictionary file
                               to use instead of `dictionary`, for oov_mode
                               "upload"/"type".

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

    # Out-of-vocabulary word handling: a pre-resolved custom dictionary
    # (already merged/validated by merge_or_validate_pronunciations() at job
    # submission time, see web/app.py) takes precedence over the plain
    # dictionary name. Otherwise, fall back to MFA's own G2P guessing - but
    # only if that G2P model is actually downloaded on this server.
    # MFA_G2P_MODEL_BY_DICTIONARY only records that a model exists *in MFA's
    # catalog* under this name; passing --g2p_model_path for a model that
    # isn't installed makes `mfa align` fail outright instead of degrading
    # gracefully, so the file's presence must be checked here too. Unlike
    # DICTIONARY_PATH/ACOUSTIC_MODEL_PATH, --g2p_model_path takes an actual
    # file path, not a bare model name - confirmed live: passing the name
    # alone fails with "File 'english_us_arpa' does not exist."
    oov_resolved_dictionary_path = config.get("oov_resolved_dictionary_path")
    g2p_model_path = None
    if oov_resolved_dictionary_path:
        dictionary_arg = str(oov_resolved_dictionary_path)
    else:
        dictionary_arg = dictionary
        g2p_model_name = MFA_G2P_MODEL_BY_DICTIONARY.get(dictionary)
        # The docker runner doesn't mount the host's pretrained-models
        # directory into the container, so a host-side g2p file path
        # wouldn't resolve in there - skip G2P entirely for that runner
        # rather than pass a path the container can't see.
        if g2p_model_name and runner != "docker":
            candidate = mfa_g2p_model_file(g2p_model_name)
            if candidate.exists():
                g2p_model_path = str(candidate)

    if runner == "docker":
        docker_image = config.get("docker_image", "mmcauliffe/montreal-forced-aligner:latest")
        cmd = [
            "docker", "run", "--rm",
            "-v", f"{job_dir.resolve()}:/data",
            docker_image,
            "mfa", "align",
            "/data/mfa_corpus",
            dictionary_arg,
            acoustic_model,
            "/data/mfa_output",
            "--temporary_directory", "/data/mfa_temp",
            "--clean",
            "--num_jobs", str(num_jobs),
            "--output_format", output_format,
        ]
        if fine_tune:
            cmd.append("--fine_tune")
        if g2p_model_path:
            cmd += ["--g2p_model_path", g2p_model_path]
    else:
        conda_env = config.get("conda_env", "aligner")
        cmd = [
            "conda", "run", "-n", conda_env, "--no-capture-output",
            "mfa", "align",
            str(corpus_dir),
            dictionary_arg,
            acoustic_model,
            str(output_dir),
            "--temporary_directory", str(temp_dir),
            "--clean",
            "--num_jobs", str(num_jobs),
            "--output_format", output_format,
        ]
        if fine_tune:
            cmd.append("--fine_tune")
        if g2p_model_path:
            cmd += ["--g2p_model_path", g2p_model_path]

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
