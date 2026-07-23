################################################################################
#
# Add a new tier, "phones - combined - liquids", that's a copy of the phone tier
# with each word-internal vowel+liquid (L/R) pair merged into a single
# combined interval, e.g. "UH1" + "L" -> "UHL1" (stress marker moves to the
# end). The original phone tier is left untouched. Meant to be used in
# VoxHumana ahead of new-fave formant extraction, so pre-liquid/pre-rhotic
# vowels can be analyzed as a single unit together with the following liquid.
#
# Vowels are identified by the presence of a stress digit (0/1/2) in the
# label, matching CMU ARPABET conventions (english_us_arpa).
#
# include_intervocalic controls what happens when the liquid is itself
# followed by another vowel in the same word (e.g. the L in "yellow", or in
# "fuller"/"pooling" where a vowel-initial suffix follows a coda liquid).
# Phone-adjacency alone can't tell those two cases apart, so this is a
# blunt on/off switch rather than a syllable-aware rule: "on" merges both;
# "off" skips both.
#
# Joey Stanley
# BYU, Provo, Utah
#
################################################################################

form: "Combine pre-liquid vowel sequences"
	sentence: "TextGrid path", "./sample.TextGrid"
	natural: "Word tier", "1"
	natural: "Phone tier", "2"
	boolean: "Include intervocalic", 1
endform

tg = Read from file: textGrid_path$

# Work on a duplicate of the phone tier, appended as the last tier, so the
# original phone tier is left completely untouched.
n_tiers = Get number of tiers
combined_tier = n_tiers + 1
Duplicate tier: phone_tier, combined_tier, "phones - combined - liquids"

n_phones = Get number of intervals: combined_tier
for i from 1 to n_phones - 1

	# Work backwards so things don't get messed up when we remove boundaries
	i_phone = n_phones - i + 1
	if i_phone > 1

		# Make sure we're working with laterals or rhotics
		this_phone_label$ = Get label of interval: combined_tier, i_phone
		is_liquid = this_phone_label$ = "L" or this_phone_label$ = "R"

		# Make sure the previous sound is a vowel
		# Note that vowels are determined by having a number in them. Therefore stress markers are required.
		prev_phone_label$ = Get label of interval: combined_tier, i_phone - 1
		prev_is_vowel = index_regex(prev_phone_label$, "\d") > 0

		if is_liquid and prev_is_vowel

			# Get the start times
			this_phone_start = Get start time of interval: combined_tier, i_phone
			prev_phone_start = Get start time of interval: combined_tier, i_phone - 1

			# Get what words these phonemes are in
			this_phone_word = Get interval at time: word_tier, this_phone_start
			prev_phone_word = Get interval at time: word_tier, prev_phone_start
			prev_in_same_word = this_phone_word = prev_phone_word

			# Only continue if this phoneme and the previous phoneme are in the same word.
			# i.e., we only want word-internal preliquids.
			if prev_in_same_word

				# See if there's a following phone in the same word, and whether
				# it's a vowel. A liquid at the very end of the tier has no
				# following phone, so it can't be intervocalic -- treat it the
				# same as a word-final liquid.
				if i_phone < n_phones
					next_phone_start = Get start time of interval: combined_tier, i_phone + 1
					next_phone_word  = Get interval at time: word_tier, next_phone_start
					next_in_same_word = this_phone_word = next_phone_word

					next_phone_label$ = Get label of interval: combined_tier, i_phone + 1
					next_is_vowel = index_regex(next_phone_label$, "\d") > 0
				else
					next_in_same_word = 0
					next_is_vowel = 0
				endif

				# If it's intervocalic but we don't want it, skip it.
				if not include_intervocalic and next_in_same_word and next_is_vowel
					# Do nothing. These are the intervocalics we want to skip.
				# Otherwise, process it.
				else
					@combine_and_remove_tier
				endif

			endif

		endif

	endif

endfor

Save as text file: textGrid_path$

removeObject: tg


procedure combine_and_remove_tier
	# Get the combined label
	vowel$  = replace_regex$(prev_phone_label$, "\d", "", 0)
	stress$ = replace_regex$(prev_phone_label$, "\D", "", 0)
	combined_label$ = vowel$ + this_phone_label$ + stress$

	# Remove the boundary to the left of this one.
	Remove left boundary: combined_tier, i_phone

	# There is now one fewer intervals in the tier.
	# i_phone now refers to the *following* interval, so i_phone-1 now refers to the combined interval.
	Set interval text: combined_tier, i_phone - 1, combined_label$

endproc
