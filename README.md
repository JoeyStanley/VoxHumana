# VoxHumana

VoxHumana (VxH) is an all-in-one sociophonetics tool for processing speech. It transcribes audio using [Whisper](https://openai.com/index/whisper/), force aligns using [MFA](https://montreal-forced-aligner.readthedocs.io/en/latest/), and extracts formants using [new-fave](https://forced-alignment-and-vowel-extraction.github.io/new-fave/). 

VxH is inteded to be the spiritual successor to [DARLA](http://darla.dartmouth.edu) (Dartmouth Linguistic Automation). The main benefits of using VxH over DARLA is that it uses Whisper for transcription, which is state-of-the-art, and uses new-fave for formant extraction. 

*Vox Humana* is Latin for "human voice". I'm opting for the Modern Ecclesiastical [pronunciation](https://en.wiktionary.org/wiki/vox_humana) [ˈvɔks uˈmaː.na]. I chose this name because this tool will feed into (if not fully integrate with) [Pipeline](https://github.com/JoeyStanley/pipeline). Pipeline is literally a pipeline of sociophonetic data processing, and since I play the organ, I thought I'd go with a subtle nod at a classic pipe organ look when I designed the colors. *Vox Humana* is the name of one of the stops on many pipe organs, which you can learn more about [here](http://www.organstops.org/v/VoxHumana.html), and I think seves as as suitable name for a tool that processes audio of the human voice.


## System Dependencies
- ffmpeg (install via Homebrew on Mac, apt on Linux)
- [MFA will go here later]