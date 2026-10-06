import whisper
import json
import torch
from pathlib import Path

from pipeline.errors import UserFacingError


def _follow_thread_target(model, thread_target):
    """Re-check how many CPU threads to use before each 30-second window.

    `thread_target` is a shared multiprocessing.Value that the web app raises
    and lowers as its alignment queue goes idle or busy (see web/app.py), so
    Whisper can borrow the core set aside for MFA/new-fave and give it back.
    Whisper calls model.decode() once per window (again on a temperature
    fallback), so that's the natural place to check. If a future Whisper
    version stopped calling it, transcription just keeps its starting count.
    """
    decode = model.decode

    def decode_with_thread_check(*args, **kwargs):
        n = thread_target.value
        if n > 0 and n != torch.get_num_threads():
            torch.set_num_threads(n)
            print(f"Whisper: now using {n} CPU thread{'s' if n != 1 else ''}", flush=True)
        return decode(*args, **kwargs)

    model.decode = decode_with_thread_check


def transcribe(audio_path, job_dir, config=None, thread_target=None):

    if not Path(audio_path).exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    if config is None:
        config = {}

    model_size = config.get("model", "turbo")
    language = config.get("language", None)
    initial_prompt = config.get("initial_prompt", None)
    condition_on_previous_text = config.get("condition_on_previous_text", True)

    # Decode up front rather than letting model.transcribe() do it: on a bad
    # file, Whisper's loader raises with ffmpeg's entire stderr (version,
    # build flags, server paths) as the message.
    try:
        audio = whisper.load_audio(audio_path)
    except RuntimeError as exc:
        raise UserFacingError(
            "VoxHumana couldn't decode this audio file. It may be damaged, or in a "
            "format the server can't read. Try re-exporting it as a WAV file."
        ) from exc

    model = whisper.load_model(model_size)
    if thread_target is not None:
        torch.set_num_threads(max(1, thread_target.value))
        _follow_thread_target(model, thread_target)
    result = model.transcribe(
        audio,
        language=language,
        initial_prompt=initial_prompt,
        condition_on_previous_text=condition_on_previous_text,
    )

    if not result["segments"]:
        raise UserFacingError(
            "No speech was detected in this audio. If this is a stereo file, check "
            "that the two channels aren't out of phase -- mixing to mono for "
            "transcription can cancel the audio out entirely."
        )

    stem = Path(audio_path).stem
    whisper_dir = Path(job_dir) / "whisper_output"
    whisper_dir.mkdir(parents=True, exist_ok=True)

    output_path = whisper_dir / f"{stem}.json"
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    (whisper_dir / f"{stem}.txt").write_text(result["text"].strip())

    return result
