form: "Generate transcript"
	sentence: "Job directory", "."
	word: "Speaker name", "WY001"
endform

only_tier = 1
tg = Read from file: "'job_directory$'/whisper_output/'speaker_name$'.TextGrid"

output_file$ = "'job_directory$'/whisper_output/'speaker_name$'_lines.txt"
writeFile: output_file$, ""

n_intervals = Get number of intervals: only_tier
for i from 1 to n_intervals

	@show_start_time

	text$ = Get label of interval: only_tier, i
	appendFileLine: output_file$, "'start_time$''tab$''text$'"

endfor

Remove

procedure show_start_time

	# Numeric version
	start_time = Get start time of interval: only_tier, i

	# Get the minutes
	min = start_time/60
	min = floor(min)
	if min < 1
		min$ = "0"
	else
		min$ = string$(min)
	endif

	# Get the seconds
	sec = start_time mod 60
	if sec < 1
		sec = 0
	endif
	sec$ = fixed$(sec, 0)
	if sec < 10
		sec$ = "0'sec$'"
	endif

	# Get the display version
	start_time$ = "'min$':'sec$'"

endproc
