## Automated sociophonetic analysis

VoxHumana (VxH) is a web-based pipeline for processing sociolinguistic speech recordings.
With just a few clicks, VxH will transcribe your audio, force-align it, and extract vowel formant
measurements for you automatically. In a short time, you can go from raw audio to a spreadsheet
of formant measurements without having to download software or code anything yourself.

### The pipeline

Currently, every recording submitted to VxH goes through three steps:

1. **Transcription** — [Whisper](https://openai.com/index/whisper/) converts your audio to text with word-level timestamps.
2. **Forced alignment** — [Montreal Forced Aligner (MFA)](https://montreal-forced-aligner.readthedocs.io) gets the precise start and end time of every word and phone.
3. **Formant extraction** — [new-fave](https://forced-alignment-and-vowel-extraction.github.io/new-fave/) measures F1, F2, F3, and F4 for every vowel token.

The result is a CSV of vowel measurements ready for analysis in R, Python, or other statistical software.

### Comparison to DARLA

VxH is a [spiritual sucessor](https://en.wikipedia.org/wiki/Spiritual_successor)
to [DARLA](http://darla.dartmouth.edu). It is clearly inspired by DARLA and seeks to replicate
much of its functionality. However, the creators of DARLA were not involved in the development of VxH (outside of playing a small consulting role) 
and it is an independent development by its creator, Joey Stanley.

Here are some key differences between VxH and DARLA:

- Transcriptions are now done using Whisper instead of an in-house system, YouTube, or BedWord.
- Formant extractions are done using new-fave, the modern successor to [FAVE-Extract](https://github.com/JoFrhwld/FAVE).
- VxH is full open-source; everything is available on [GitHub](https://github.com/JoeyStanley/VoxHumana), which means you can download it and use it offline if you have the necessary software.

### The name

*Vox Humana* is Latin for "human voice" (Modern Ecclesiastical [pronunciation](https://en.wiktionary.org/wiki/vox_humana): [ˈvɔks uˈmaː.na]).
It is also the name of a classic pipe organ stop. This organ motif appears in several places in VxH. The color palette is inspired by the façade of [Salt Lake Tabernacle organ](https://en.wikipedia.org/wiki/Salt_Lake_Tabernacle_organ). 
This theme is shared with with [Pipeline](https://stanley.byu.edu/pipeline/), another web-based tool that takes the output of VxH and create interactive vowel plots. 

I chose the organ theme in part because of the play on words with *pipeline* but also because I play the organ and I wanted to add a personal flair to this tool. *Vox Humana* was a natural choice for the name of a project that intersects nerdy organ knowledge with the human voice; you can learn more about the Vox Humana organ stop [here](http://www.organstops.org/v/VoxHumana.html).

### AI disclosure

Much of VxH was written using [Claude](https://claude.ai/) by [Anthropic](https://www.anthropic.com). This includes pretty much all of the back-end and the UI. The documentation is my own. 

I used AI to write VxH because I felt that there was great need for a replacement for DARLA and that it would be best written in Python. But my Python skills are extremely limited. Ideally, I would learn Python well enough to code everything by hand or hire someone else to do so. But I do not have the time or resources for those options, and the growing pressure for a tool like this means the field couldn't wait. 

I acknowledge that some users may feel uncomfortable with this use of AI; I recognize your concerns. Please make the best choice for you and your data. 

### About the project

VoxHumana is developed and maintained by [Joey Stanley](https://joeystanley.com), associate professor in the [linguistics department](https://ling.byu.edu) at [Brigham Young University]
(https://www.byu.edu). Please send questions and bug reports to [joey_stanley@byu.edu](mailto:joey_stanley@byu.edu).
