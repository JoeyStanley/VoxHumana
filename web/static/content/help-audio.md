### Accepted formats

WAV, MP3, FLAC, M4A, OGG, and AAC. WAV is preferred — it is uncompressed and avoids any
quality loss from transcoding.

### File size

Maximum upload is 1 GB. A 60-minute WAV at 44.1 kHz stereo is about 600 MB — well within
the limit for most interviews.

### Files larger than 1 GB

Split the recording into segments and run each one separately, then combine the output CSVs.

Using Audacity: File → Export → Export Multiple, split by time interval.

Using ffmpeg (30-minute chunks):

```
ffmpeg -i interview.wav -f segment \
  -segment_time 1800 -c copy part%03d.wav
```

### Audio quality tips

16 kHz or higher sample rate is recommended. Mono and stereo both work. For two-channel
recordings with speaker and interviewer on separate channels, consider mixing down to mono or
extracting the speaker's channel for cleaner transcription.
