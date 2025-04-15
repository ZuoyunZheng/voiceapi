# .mp3
ffmpeg -ss 0 -i data/council_010421_2022101V.mp3 -af "channelsplit=channel_layout=stereo:channels=FL" -t 48 -osr 16000 data/test.mp3
ffmpeg -ss 0 -i data/council_010421_2022101V.mp3 -af "channelsplit=channel_layout=stereo:channels=FL" -t 120 -osr 16000 data/test_long.mp3
# .wav
ffmpeg -ss 0 -i data/council_010421_2022101V.mp3 -af "channelsplit=channel_layout=stereo:channels=FL" -t 48 -osr 16000 data/test.wav
