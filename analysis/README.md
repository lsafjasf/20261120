# G.722 verification harness

The implementation follows ITU-T G.722 fixed-point QMF/ADPCM arithmetic and was
cross-checked against FFmpeg:

- 64 kbit/s payload: byte-identical to the FFmpeg G.722 encoder.
- 64/56/48 kbit/s PCM: sample-identical to the FFmpeg decoder using 8/7/6
  audio bits per codeword.

## Run

```bash
python3 analysis/run_g722_analysis.py
python3 analysis/verify_g722.py
```

`run_g722_analysis.py` reads
`artifacts/g722/audio/speech_hf_16k.wav`, writes all measurements under
`artifacts/g722_verified/`, and recreates the plots.

The 2 s input is a speech-like voiced/fricative test clip. Its final portion has
strong energy above 4 kHz, which is used to check that the high band is retained
rather than low-pass truncated at 4 kHz.

## Mode field

A G.722 codeword does not carry an embedded `mode` field. The mode is selected
out of band:

| external mode | bitrate | low bits | high bits | auxiliary LSBs |
|---:|---:|---:|---:|---:|
| 1 | 64 kbit/s | 6 | 2 | 0 |
| 2 | 56 kbit/s | 5 | 2 | 1 |
| 3 | 48 kbit/s | 4 | 2 | 2 |

The CSV `mode` column is therefore the externally known mode, not a value
parsed from a per-byte mode field.

## Main outputs

- `data/mode_summary.csv`: levels, quantizer scale, gain, SNR, and band errors.
- `data/frames_mode1_64k.csv`: per-frame fields and both predictors.
- `data/quantizer_levels.csv`: all low/high quantizer reconstruction levels.
- `data/bit_error_summary.csv`: single-bit error propagation and SNR changes.
- `data/isolation_summary.csv`: high-band noise isolation measurements.
- `data/high_frequency_speech.csv`: high-band retention for the speech clip.
- `audio/`: decoded, bit-error, and isolation WAV files.
- `plots/`: SNR, predictor gain, error support, and spectra.
- `REPORT.md`: concise result tables and conclusions.

## Interpretation

Low codeword fine bits do not enter the predictor adaptation and therefore only
affect the current ADPCM frame. Core and high-band bits alter adaptive state and
can persist for hundreds of milliseconds. The 16 kHz output has an additional
finite QMF ringing tail.

The high-band bit-error test leaves both the low-band reconstruction and the
low-band QMF component bit-exact. Spectral energy below 3.5 kHz in the combined
16 kHz output comes from the QMF synthesis filter stopband, not from high-band
state coupling into the low-band ADPCM predictor.
