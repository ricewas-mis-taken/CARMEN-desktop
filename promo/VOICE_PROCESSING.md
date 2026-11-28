# Voice processing for promo videos

How Lucas's recorded voice lines are cleaned up for the short videos. Tools:
ffmpeg (imageio_ffmpeg build) and Python (numpy, scipy).

## 1. Convert
Recording (mp3 from Windows Sound Recorder) → mono, 48 kHz WAV.

## 2. Slice into lines
Transcribe with faster-whisper (`small.en`, CPU, int8, word timestamps) and cut
one clip per script line.

## 3. Remove breaths and dead air (gentle gate)
Measured every 10 ms:
- `L` = level of the 90–900 Hz band (voice body), `Hh` = level of the 2–8 kHz band (hiss / "s").
- Speech where `(L > peak − 34 dB and L > Hh + 2 dB) or Hh > peak − 26 dB`
  (keeps "s" sounds, drops breaths, which are hissy but weak).
- Clean the mask: closing over 14 frames, opening over 3.
- Keep 80 ms before and 220 ms after each speech frame so word starts and ends aren't clipped.
- Outside speech, turn the audio down to 12% (6% for the hard/soft lock lines), not to silence; smooth over 50 ms.
- 15 ms fade in and out on each clip.

## 4. Podcast chain (dry, no reverb)
```
afftdn=nr=12:nf=-45,
highpass=f=85:poles=2,
equalizer=f=380:t=q:w=1.4:g=-3.5,
equalizer=f=140:t=q:w=0.9:g=2.5,
equalizer=f=3200:t=q:w=1:g=2.5,
equalizer=f=6500:t=q:w=1:g=2,
treble=g=1.5:f=10000:t=s,
acompressor=threshold=-22dB:ratio=3:attack=12:release=180:makeup=5,
deesser=i=0.15,
alimiter=limit=0.89,
loudnorm=I=-16:TP=-1.5:LRA=7,
aresample=48000
```
Hard/soft lock explainer lines use a stronger version: `afftdn=nr=18:nf=-42:tn=1`,
`highpass=f=90`, 380 Hz cut −4 dB, 3.2 kHz +3 dB, compressor threshold −24 dB, ratio 3.5,
attack 10, release 160, makeup 6.

## 5. Character voices (second recording)
The podcast chain first, then:
- **Brain:** pitch ×1.32 (`asetrate`+`atempo`), `chorus=0.6:0.8:30:0.3:0.25:2`, 250 Hz −4 dB.
- **Announcer:** pitch ×0.84, compressor −18 dB / 5:1, 120 Hz +3 dB, `aecho=0.8:0.6:70|140:0.22|0.12`.
- **Boss:** pitch ×0.72, `acrusher=bits=10:mix=0.25`, 90 Hz +4 dB, `aecho=0.8:0.7:90|180:0.3|0.18`.
- **Librarian:** extra `highpass=f=200`, 2.5 kHz +3 dB.
Each ends with `loudnorm=I=-16:TP=-1.5:LRA=7`.

## 6. Mix
- Voice at full level. Music is ducked by up to 65% while a voice is speaking (50 ms envelope).
- Final video audio: `loudnorm=I=-14:TP=-1.5:LRA=11` (social video level), AAC 192 kbps stereo.

## Recording tips that matter more than any filter
Small soft room (closet or bedroom with curtains), phone a hand's width from your mouth,
2-second pauses between lines, redo a fluffed line after a pause.
