########################################################################
#
# Convert a plain-text file into a basic TextGrid (Thank, Monica!)
#
# Joey Stanley
# 8:58am, Tuesday, September 8, 2026
# 822 bus, Springville, Utah
# While recovering from allergies over Labor Day
#
# NOT CURRENTLY WIRED IN. Parked alongside pipeline/txt_to_textgrid.py,
# which is the only thing that calls this script -- and it isn't called
# from anywhere either. See TODO.md, "Support for .txt transcriptions".
#
########################################################################

form: "Convert a plain-text transcript into a scratch TextGrid"
	real: "Duration", "1.0"
	sentence: "Text file", "test.txt"
	sentence: "Output TextGrid", "test.TextGrid"
endform

# Duration comes in as a form argument (VoxHumana already computes it via
# librosa for other pipeline steps) rather than opening the audio here,
# which would mean reading a potentially long file a second time just to
# get its length.


# Get information about the txt file
strings = Read Strings from raw text file: text_file$
n_strings = Get number of strings


# Start with an empty string and add to it with each line in the txt file.
cat_text$ = ""
for i from 1 to n_strings
	this_string$ = Get string: i

	# Exclude ones with comments at the beginning
	if left$(this_string$, 1) = "#"
		this_string$ = ""
	elsif this_string$ == ""
		# do nothing
	else

		# Add a space at the end if there isn't one.
		if right$(this_string$, 1) <> " "
			this_string$ = this_string$ + " "
		endif

	endif

	cat_text$ = cat_text$ + this_string$
endfor
Remove


# Now create a TextGrid with that duration and string information.
tg = Create TextGrid: 0, duration, "utterances", ""
Set interval text: 1, 1, cat_text$
Save as text file: output_TextGrid$
Remove
