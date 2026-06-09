### Accepted formats

WAV, MP3, FLAC, M4A, OGG, and AAC. WAV is best because it is uncompressed and usually returns 
the best results. 

### File size

Maximum upload is 1 GB. A 60-minute WAV at 44.1 kHz stereo is about 600 MB. If you have
a larger file, you'll need to manually split the recording into segments, run each 
one separately, and then combine the output CSVs. Here are some instructions on how to 
do the splitting.

Using Audacity: 

1. Zoom in to where you want the split to happen and click on a period of silence.
1. Click Edit → Audio Clips → Split
1. Highlight the first half, save it, and run it through VoxHumana.
1. Repeat for the second half.   

Using Praat: [TODO]

It is recommended that you do these splits manually, rather than programatically (i.e.
using a script to cut the file in half) so that you can ensure a split doesn't happen in 
the middle of a word.

### Audio quality tips

16 kHz or higher sample rate is recommended. Mono and stereo both work. For two-channel
recordings with speaker and interviewer on separate channels, consider mixing down to mono or
extracting the speaker's channel for cleaner transcription.
