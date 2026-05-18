import whisper
import json

# For concatinating paths
from pathlib import Path


def transcribe(audio_path, job_dir, config=None):

    # Security checks
    if not Path(audio_path).exists():
        raise FileNotFoundError(f"Unable to transcribe audio because there was no audio file at {audio_path}")

    # If there are no config, use an empty dict to avoid errors
    if config is None:
        config = {}
    # But if there are, use them. Use "turbo" and None.
    model_size = config.get("model", "turbo")
    language = config.get("language", None)

    # Load the model and transcribe the audio
    model = whisper.load_model(model_size)
    result = model.transcribe(audio_path, language=language)
    
    # Save the result as a JSON file in the job directory 
    output_path = Path(job_dir) / "transcript.json"
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    return result
