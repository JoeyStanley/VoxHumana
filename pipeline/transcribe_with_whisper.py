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

    output_path = Path(job_dir) / "transcript.json"
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    return result
