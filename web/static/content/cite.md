## How to cite

If you use VoxHumana in published research, please cite both the tool itself and the underlying
components that you used to do the work. Verify all citations before submission since software versions and
publication details may change.

### VoxHumana

> Stanley, Joey (in preparation). *VoxHumana: Automated sociophonetic analysis.* Brigham Young University.

### Whisper

OpenAI's automatic speech recognition model, used for transcription.

> Radford, Alec, Jong Wook Kim, Tao Xu, Greg Brockman, Christine McLeavey, & Ilya Sutskever (2022). Robust speech recognition via large-scale weak supervision. *Proceedings of the 40th International Conference on Machine Learning (ICML).* <https://arxiv.org/abs/2212.04356>

The output of Whisper is converted into a Praat TextGrid using the [TextGridTools](https://textgridtools.readthedocs.io/en/stable/index.html) package in Python.

> Buschmeier, Hendrik and Marcin Włodarczak (2013). TextGridTools: A TextGrid Processing and Analysis Toolkit for Python. In Petra Wagner (ed.) *Tagungsband der 24. Konferenz zur Elektronischen Sprachsignalverarbeitung (ESSV 2013)*. Dresden: TUDpress, pp. 152-157. <https://pub.uni-bielefeld.de/download/2561620/2563287>

### Montreal Forced Aligner (MFA)

Used for forced alignment to produce word- and phone-level timing.

> McAuliffe, Michael, Michaela Socolof, Sarah Mihuc, Michael Wagner, and Morgan Sonderegger (2017). Montreal Forced Aligner: trainable text-speech alignment using Kaldi. In *Proceedings of the 18th Conference of the International Speech Communication Association*. DOI: [10.21437/Interspeech.2017-1386](https://www.isca-archive.org/interspeech_2017/mcauliffe17_interspeech.html)

### new-fave

Used for vowel formant extraction.

> Fruehwald, J. (2026). *new-fave: Forced alignment and vowel extraction* [Software]. <https://forced-alignment-and-vowel-extraction.github.io/new-fave/>

new-fave uses [Fast Track](https://github.com/santiagobarreda/FastTrack) to help with formant extraction. 

> Barreda, Santiago. (2021). Fast Track: fast (nearly) automatic formant-tracking using Praat. *Linguistics Vanguard*, 7(1). <https://doi.org/10.1515/lingvan-2020-0051>

Both new-fave and Fast Track use [Praat](https://www.praat.org) for acoustic measurements. If your work depends on Praat directly, you may also wish to cite:

> Paul Boersma, David Weenink, & Anastasia Shchupak (2026). Praat: doing phonetics by computer [Computer program]. Version 6.4.67, retrieved 21 May 2026 from https://praat.org.

