import tgt                  # for working with textgrids
import librosa              # for getting duration of audio
from pathlib import Path    # for working with paths

def convert_whisper_to_textgrid(result, audio_path, job_dir):

     # Get audio duration.
    duration = librosa.get_duration(path=audio_path)

    # Create a new TextGrid object.
    tg = tgt.TextGrid()
    
    # Create an interval tier.
    tier = tgt.IntervalTier(name='utterances', start_time=0, end_time=duration)

    # Add each segment as an interval.
    # Whisper occasionally places the final segment's end past the true audio
    # duration by a few milliseconds. Clamp to avoid tgt rejecting the interval.
    for segment in result["segments"]:
        start = segment["start"]
        end = min(segment["end"], duration)
        if end <= start:
            continue
        tier.add_interval(tgt.Interval(start, end, segment["text"].strip()))
    
    # Add tier to TextGrid 
    tg.add_tier(tier)

    # Write it out
    whisper_dir = Path(job_dir) / "whisper"
    whisper_dir.mkdir(parents=True, exist_ok=True)
    output_path = whisper_dir / f"{Path(audio_path).stem}.TextGrid"
    tgt.write_to_file(tg, str(output_path))
    
    return output_path
