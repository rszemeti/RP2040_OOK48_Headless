#!/usr/bin/env python3
"""
Generate OOK48 test WAV files with bandlimited white noise mixed in at various SNR levels.
Reads OOK48Test.wav (tone on right channel, click sync on left) and produces
test files across a range of SNR conditions.

The signal is normalised to -12 dBFS peak before noise is added, giving
consistent headroom across all output files.

For positive SNR: signal at -12 dBFS peak, noise reduced proportionally.
For negative SNR: noise held at 0dB SNR level, signal attenuated further.
This models a realistic fading signal into a constant HF noise floor.

SNR levels: +20, +10, +6, +3, 0, -3 dB

Usage:
    python generate_noise_tests.py [input.wav] [bandwidth_hz] [target_dbfs]

    input.wav      - defaults to OOK48Test.wav
    bandwidth_hz   - noise bandwidth in Hz, defaults to 3500
    target_dbfs    - normalise signal peak to this dBFS level, defaults to -12

Examples:
    python generate_noise_tests.py
    python generate_noise_tests.py OOK48Test.wav 2400
    python generate_noise_tests.py OOK48Test.wav 3500 -18

Requirements: pip install numpy scipy
"""

import sys
import os
import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, sosfilt

# SNR values in dB - signal relative to noise
# +20dB = signal 20dB above noise (clean)
#   0dB = signal equals noise (marginal)
#  -3dB = signal below noise (should fail)
SNR_DB = [20, 10, 6, 3, 0, -3, -6, -10, -15, -16, -17, -18, -19, -20, -21, -22, -23, -24, -25]
DEFAULT_BANDWIDTH  = 3500
DEFAULT_TARGET_DBFS = -12

def bandlimit_noise(noise, sample_rate, bandwidth_hz):
    """Apply a lowpass filter to white noise to limit its bandwidth."""
    nyquist = sample_rate / 2.0
    if bandwidth_hz >= nyquist:
        print(f"  Note: requested bandwidth {bandwidth_hz}Hz >= Nyquist {nyquist:.0f}Hz, skipping filter")
        return noise
    # 8th order Butterworth - steep rolloff, minimal passband ripple
    sos = butter(8, bandwidth_hz / nyquist, btype='low', output='sos')
    filtered = sosfilt(sos, noise)
    # Rescale back to original RMS since filtering reduces power
    original_rms = np.sqrt(np.mean(noise ** 2))
    filtered_rms = np.sqrt(np.mean(filtered ** 2))
    if filtered_rms > 0:
        filtered *= original_rms / filtered_rms
    return filtered

def mix_noise(input_file, bandwidth_hz, target_dbfs):
    sample_rate, data = wavfile.read(input_file)
    nyquist = sample_rate / 2.0

    print(f"Sample rate:     {sample_rate} Hz  (Nyquist: {nyquist:.0f} Hz)")
    print(f"Noise bandwidth: {bandwidth_hz} Hz")
    print(f"Target peak:     {target_dbfs} dBFS")

    # Handle mono or stereo
    if data.ndim == 1:
        print("Input is mono - noise will be added to the single channel")
        tone_ch  = data.astype(np.float64)
        click_ch = None
    else:
        print("Input is stereo (right=tone, left=click)")
        tone_ch  = data[:, 1].astype(np.float64)
        click_ch = data[:, 0].astype(np.float64)

    # Full scale value for this dtype
    if data.dtype == np.int16:
        full_scale = 32768.0
    elif data.dtype == np.int32:
        full_scale = 2147483648.0
    elif data.dtype == np.uint8:
        full_scale = 128.0
    else:
        full_scale = 1.0

    def dbfs(linear):
        return 20 * np.log10(max(linear, 1e-10) / full_scale)

    # --- Source file analysis ---
    tone_rms  = np.sqrt(np.mean(tone_ch ** 2))
    tone_peak = np.max(np.abs(tone_ch))

    print(f"\n--- Source file analysis ---")
    print(f"Tone  channel:  RMS {dbfs(tone_rms):+.1f} dBFS   Peak {dbfs(tone_peak):+.1f} dBFS   "
          f"Headroom {-dbfs(tone_peak):.1f} dB")

    if click_ch is not None:
        click_rms  = np.sqrt(np.mean(click_ch ** 2))
        click_peak = np.max(np.abs(click_ch))
        print(f"Click channel:  RMS {dbfs(click_rms):+.1f} dBFS   Peak {dbfs(click_peak):+.1f} dBFS   "
              f"Headroom {-dbfs(click_peak):.1f} dB")

    # --- Normalise signal to target peak level ---
    target_peak  = full_scale * (10 ** (target_dbfs / 20.0))
    norm_scale   = target_peak / tone_peak
    tone_norm    = tone_ch * norm_scale
    norm_rms     = tone_rms * norm_scale
    norm_peak    = tone_peak * norm_scale

    print(f"\n--- After normalisation to {target_dbfs} dBFS peak ---")
    print(f"Tone  channel:  RMS {dbfs(norm_rms):+.1f} dBFS   Peak {dbfs(norm_peak):+.1f} dBFS   "
          f"Scale factor {norm_scale:.4f} ({20*np.log10(norm_scale):+.1f} dB)")

    # Noise RMS at 0dB SNR = normalised signal RMS
    noise_rms_0db = norm_rms

    # --- Clipping check ---
    print(f"\n--- Clipping check ---")
    for snr_db in SNR_DB:
        if snr_db >= 0:
            sig_peak = norm_peak
            nrms = noise_rms_0db / (10 ** (snr_db / 20.0))
        else:
            sig_peak = norm_peak * (10 ** (snr_db / 20.0))
            nrms = noise_rms_0db
        worst_peak = sig_peak + 3 * nrms
        clip_warn = "  *** WILL CLIP ***" if worst_peak > full_scale else ""
        print(f"  SNR {snr_db:+3d}dB:  estimated worst-case peak {dbfs(worst_peak):+.1f} dBFS{clip_warn}")
    print()

    # --- Generate output files ---
    base = os.path.splitext(input_file)[0]

    for snr_db in SNR_DB:
        if snr_db >= 0:
            # Positive SNR: normalised signal, noise below signal level
            signal    = tone_norm.copy()
            noise_rms = noise_rms_0db / (10 ** (snr_db / 20.0))
        else:
            # Negative SNR: noise fixed at 0dB level, signal attenuated further
            noise_rms = noise_rms_0db
            signal    = tone_norm * (10 ** (snr_db / 20.0))

        # Generate bandlimited white noise scaled to required RMS
        white_noise = np.random.normal(0, 1.0, len(tone_ch))
        noise = bandlimit_noise(white_noise, sample_rate, bandwidth_hz)
        noise = noise * (noise_rms / np.sqrt(np.mean(noise ** 2)))

        noisy_tone = signal + noise

        # Clip to dtype range (should not clip after normalisation, but just in case)
        if data.dtype == np.int16:
            noisy_tone = np.clip(noisy_tone, -32768, 32767).astype(np.int16)
        elif data.dtype == np.int32:
            noisy_tone = np.clip(noisy_tone, -2147483648, 2147483647).astype(np.int32)
        elif data.dtype == np.uint8:
            noisy_tone = np.clip(noisy_tone, 0, 255).astype(np.uint8)
        else:
            noisy_tone = noisy_tone.astype(data.dtype)

        # Reconstruct stereo file: click on ch0 (left), tone on ch1 (right)
        if click_ch is not None:
            out_data = np.column_stack((click_ch.astype(data.dtype), noisy_tone))
        else:
            out_data = noisy_tone

        out_file = f"{base}_SNR{snr_db:+d}dB_{bandwidth_hz}Hz.wav"
        wavfile.write(out_file, sample_rate, out_data)
        print(f"Written: {out_file}  (signal RMS: {dbfs(np.sqrt(np.mean(signal**2))):+.1f} dBFS   "
              f"noise RMS: {dbfs(noise_rms):+.1f} dBFS)")

    print("\nDone.")

if __name__ == "__main__":
    input_file   = sys.argv[1] if len(sys.argv) > 1 else "OOK48Test.wav"
    bandwidth_hz = int(sys.argv[2])   if len(sys.argv) > 2 else DEFAULT_BANDWIDTH
    target_dbfs  = float(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_TARGET_DBFS

    if not os.path.exists(input_file):
        print(f"Error: {input_file} not found")
        sys.exit(1)

    mix_noise(input_file, bandwidth_hz, target_dbfs)