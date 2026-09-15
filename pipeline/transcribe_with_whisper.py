import whisper
import json
from pathlib import Path


def transcribe(audio_path, job_dir, config=None):

    if not Path(audio_path).exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    if config is None:
        config = {}

    model_size = config.get("model", "turbo")
    language = config.get("language", None)
    initial_prompt = config.get("initial_prompt", None)
    condition_on_previous_text = config.get("condition_on_previous_text", True)

    model = whisper.load_model(model_size)
    result = model.transcribe(
        audio_path,
        language=language,
        initial_prompt=initial_prompt,
        condition_on_previous_text=condition_on_previous_text,
    )

    if not result["segments"]:
        raise RuntimeError(
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
