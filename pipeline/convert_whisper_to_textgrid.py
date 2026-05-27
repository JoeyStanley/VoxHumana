import tgt                  # for working with textgrids
import librosa              # for getting duration of audio
from pathlib import Path    # for working with paths

def convert_whisper_to_textgrid(result, audio_path, job_dir):

     # Get audio duration.
    duration = librosa.get_duration(path=audio_path)

    # Create a new TextGrid object.
    tg = tgt.TextGrid()
    
    # Round duration to 3 decimal places (1 ms) for consistent float comparison.
    duration = round(duration, 3)

    # Create an interval tier.
    tier = tgt.IntervalTier(name='utterances', start_time=0, end_time=duration)

    # Add each segment as an interval.
    # Whisper timestamps are raw floats and can differ by sub-millisecond amounts
    # (e.g. 25.06 vs 25.060000000000002). Rounding to 3 decimal places prevents
    # tiny float overlaps that praatio/MFA reject as malformed TextGrids.
    # Also clamp end to duration in case Whisper overshoots by a few ms.
    for segment in result["segments"]:
        start = round(segment["start"], 3)
        end = round(min(segment["end"], duration), 3)
        if end <= start:
            continue
        tier.add_interval(tgt.Interval(start, end, segment["text"].strip()))
    
    # Add tier to TextGrid 
    tg.add_tier(tier)

    # Write it out
    whisper_dir = Path(job_dir) / "whisper_output"
    whisper_dir.mkdir(parents=True, exist_ok=True)
    output_path = whisper_dir / f"{Path(audio_path).stem}.TextGrid"
    tgt.write_to_file(tg, str(output_path))
    
    return output_path
