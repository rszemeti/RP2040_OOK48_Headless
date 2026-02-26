#!/usr/bin/env python3
"""
tile_wav.py
Extends an OOK48 test WAV by tiling the signal content to a longer duration.

The GPS click channel (ch0) is regenerated at exactly 1-second intervals.
The tone channel (ch1) is tiled by repeating the message cycle.

The script detects the message boundaries from the click positions,
extracts one complete number-of-message-characters worth of tone data,
then repeats it for the desired duration.

Usage:
    python tile_wav.py [input.wav] [duration_seconds]
    Defaults to OOK48Test.wav, 180 seconds

Requirements: pip install numpy scipy
"""

import sys
import os
import numpy as np
from scipy.io import wavfile
from scipy.signal import find_peaks

TARGET_DURATION = 180   # seconds
MSG_LEN         = 11    # OOK48 TEST\r

def tile_wav(input_file, target_seconds):
    print(f"Reading {input_file}...")
    sample_rate, data = wavfile.read(input_file)

    if data.ndim == 1:
        print("Error: need stereo file")
        return

    click_ch = data[:, 0].astype(np.float64)
    tone_ch  = data[:, 1].astype(np.float64)

    if data.dtype == np.int16:
        full_scale = 32768.0
    else:
        full_scale = 1.0

    duration = len(data) / sample_rate
    print(f"Source duration: {duration:.2f}s  Target: {target_seconds}s")

    # Find clicks
    click_abs = np.abs(click_ch)
    threshold = np.max(click_abs) * 0.3
    peaks, _  = find_peaks(click_abs, height=threshold, distance=int(sample_rate * 0.8))
    print(f"Found {len(peaks)} GPS clicks")

    # Extract one complete message cycle from the tone channel
    # Use MSG_LEN seconds worth of tone starting from the first complete message
    # (skip the first partial message if click 0 isn't at a message boundary)
    samples_per_char = sample_rate   # exactly 1 char/second at 44100Hz
    cycle_samples    = MSG_LEN * samples_per_char

    # Find start of first complete message cycle
    # The first click is at sample 45 (~1ms in), so message starts effectively at 0
    # Use clicks[0] as our anchor
    cycle_start = int(peaks[0])
    cycle_end   = cycle_start + cycle_samples

    if cycle_end > len(tone_ch):
        print("Error: source file too short for one complete message cycle")
        return

    tone_cycle = tone_ch[cycle_start:cycle_end]
    print(f"Extracted message cycle: {len(tone_cycle)} samples ({len(tone_cycle)/sample_rate:.2f}s)")

    # Build output arrays
    target_samples = target_seconds * sample_rate
    out_tone       = np.zeros(target_samples)
    out_click      = np.zeros(target_samples)

    # Tile the tone cycle
    pos = 0
    while pos < target_samples:
        end = min(pos + cycle_samples, target_samples)
        out_tone[pos:end] = tone_cycle[:end - pos]
        pos += cycle_samples

    # Regenerate GPS clicks at exactly 1-second intervals
    # Use the original click shape (extract a single click waveform)
    click_half_width = int(sample_rate * 0.002)  # 2ms either side
    first_click      = int(peaks[0])
    click_template   = click_ch[max(0, first_click - click_half_width):
                                first_click + click_half_width]

    for sec in range(target_seconds):
        click_pos = sec * sample_rate
        end_pos   = min(click_pos + len(click_template), target_samples)
        length    = end_pos - click_pos
        out_click[click_pos:end_pos] = click_template[:length]

    # Clip and convert back to int16
    out_tone  = np.clip(out_tone,  -full_scale, full_scale - 1).astype(np.int16)
    out_click = np.clip(out_click, -full_scale, full_scale - 1).astype(np.int16)

    out_data = np.column_stack((out_click, out_tone))

    base     = os.path.splitext(input_file)[0]
    out_file = f"{base}_{target_seconds}s.wav"
    wavfile.write(out_file, sample_rate, out_data)

    print(f"Written: {out_file}  ({target_samples/sample_rate:.1f}s, "
          f"{len(out_data)/sample_rate/MSG_LEN:.1f} complete message repeats)")

if __name__ == "__main__":
    input_file      = sys.argv[1] if len(sys.argv) > 1 else "OOK48Test.wav"
    target_seconds  = int(sys.argv[2]) if len(sys.argv) > 2 else TARGET_DURATION

    if not os.path.exists(input_file):
        print(f"Error: {input_file} not found")
        sys.exit(1)

    tile_wav(input_file, target_seconds)