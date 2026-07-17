form: "Combine TextGrids"
	sentence: "Job directory", "."
	word: "Speaker name", "WY001"
endform

mfa_tg = Read from file: "'job_directory$'/mfa_output/'speaker_name$'.TextGrid"

# MFA writes tier 1 = words, tier 2 = phones. Extract them separately, in
# reverse order, so the final tier order is phones, then words. (Merge
# orders tiers by object creation order, not by selection order, so the
# order these Extract/Read calls run in is what determines the output order.)
selectObject: mfa_tg
phone_tier = Extract one tier: 2
selectObject: mfa_tg
word_tier  = Extract one tier: 1

whisper_tg = Read from file: "'job_directory$'/whisper_output/'speaker_name$'.TextGrid"

selectObject: phone_tier
plusObject: word_tier
plusObject: whisper_tg
merged = Merge: "yes"

Save as text file: "'job_directory$'/mfa_output/'speaker_name$'.TextGrid"

selectObject: mfa_tg
plusObject: whisper_tg
plusObject: phone_tier
plusObject: word_tier
plusObject: merged
Remove
