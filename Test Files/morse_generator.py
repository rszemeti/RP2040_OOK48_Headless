#!/usr/bin/env python3
"""
Generate Morse-in-noise WAV test files.

Creates mono WAV files containing:
- Constant-level white Gaussian noise floor
- 800 Hz Morse tone keyed at fixed 8 WPM
- SNR set: +12 down to -24 dB in 3 dB steps
- Three timing profiles:
    - steady (no timing jitter)
    - var_low (moderate element-duration jitter)
    - var_high (stronger element-duration jitter)

SNR is defined as tone-RMS relative to noise-RMS over the full file.

Usage:
    python morse_generator.py
    python morse_generator.py "CQ TEST DE OOK48" out_dir
    python morse_generator.py --duration 45 --out-dir morse_tests_fast
    python morse_generator.py --duration 45 --wpm 12 --snr-list "12,6,0,-6,-12,-18"

Arguments (all optional, positional or named):
    message         Text to encode (default: "CQ CQ DE OOK48 TEST K")
    out_dir         Output directory  (default: ./morse_tests)
    --duration N    Recording length in seconds (default: 180)
    --wpm N         Morse speed in WPM (default: 8)
    --snr-list CSV  Comma-separated SNR dB values (default: 12 down to -24 step 3)

Requirements:
    pip install numpy
"""

from __future__ import annotations

import os
import sys
import wave
from typing import Dict

import numpy as np

SAMPLE_RATE = 44_100
DURATION_SEC = 180
TONE_HZ = 800.0
WPM = 8.0
SNR_DB_LIST = [10, 6, 3, 0, -3, -6, -10, -12]
SNR_DB_LIST = list(range(12, -25, -3))
NOISE_RMS = 0.08
PEAK_LIMIT = 0.98
DEFAULT_MESSAGE = "CQ CQ DE OOK48 TEST K"
INTER_MESSAGE_GAP_UNITS = 7.0
TIMING_PROFILES = [
    ("steady", 0.00),
    ("var_low", 0.08),
    ("var_high", 0.16),
]

MORSE_MAP: Dict[str, str] = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.",
    "G": "--.", "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..",
    "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
    "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
    "Y": "-.--", "Z": "--..",
    "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-",
    "5": ".....", "6": "-....", "7": "--...", "8": "---..", "9": "----.",
    "/": "-..-.", "?": "..--..", ".": ".-.-.-", ",": "--..--", "-": "-....-",
    "+": ".-.-.", "=": "-...-",
}


def dit_seconds(wpm: float) -> float:
    return 1.2 / wpm


def build_morse_key(
    message: str,
    sample_rate: int,
    duration_sec: int,
    wpm: float,
    timing_jitter_sigma: float = 0.0,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    unit = dit_seconds(wpm)
    total_samples = int(sample_rate * duration_sec)

    segments: list[tuple[float, bool]] = []
    words = [word for word in message.upper().split() if word]

    for wi, word in enumerate(words):
        for ci, ch in enumerate(word):
            pattern = MORSE_MAP.get(ch)
            if not pattern:
                continue

            for ei, elem in enumerate(pattern):
                on_len = unit * (3.0 if elem == "-" else 1.0)
                segments.append((on_len, True))

                if ei < len(pattern) - 1:
                    segments.append((unit, False))

            if ci < len(word) - 1:
                segments.append((3.0 * unit, False))

        if wi < len(words) - 1:
            segments.append((7.0 * unit, False))

    # Important: when pattern repeats to fill long files, include a clean
    # inter-message word gap so the end of one message does not run into
    # the start of the next and create deterministic boundary symbol errors.
    segments.append((INTER_MESSAGE_GAP_UNITS * unit, False))

    if not segments:
        return np.zeros(total_samples, dtype=np.float64)

    key = np.zeros(total_samples, dtype=np.float64)
    i = 0
    while i < total_samples:
        for seg_len_sec, is_on in segments:
            seg_len = seg_len_sec
            if timing_jitter_sigma > 0.0 and rng is not None:
                factor = float(rng.normal(1.0, timing_jitter_sigma))
                factor = min(1.8, max(0.5, factor))
                seg_len *= factor

            seg_n = max(1, int(round(seg_len * sample_rate)))
            j = min(total_samples, i + seg_n)
            if is_on:
                key[i:j] = 1.0
            i = j
            if i >= total_samples:
                break

    fade_n = max(4, int(0.005 * sample_rate))
    if fade_n * 2 < total_samples:
        window = np.ones(total_samples, dtype=np.float64)
        ramp = np.linspace(0.0, 1.0, fade_n, endpoint=False)
        edges = np.diff(np.concatenate(([0.0], key, [0.0])))
        on_edges = np.where(edges == 1.0)[0]
        off_edges = np.where(edges == -1.0)[0]

        for start in on_edges:
            end = min(total_samples, start + fade_n)
            window[start:end] *= ramp[: end - start]
        for stop in off_edges:
            start = max(0, stop - fade_n)
            tail = np.linspace(1.0, 0.0, stop - start, endpoint=False)
            window[start:stop] *= tail

        key *= window

    return key


def write_wav_mono_int16(path: str, samples: np.ndarray, sample_rate: int) -> None:
    clipped = np.clip(samples, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype(np.int16)

    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())


def make_drifting_tone(
    key: np.ndarray,
    sample_rate: int,
    start_hz: float,
    end_hz: float,
) -> np.ndarray:
    """
    Generate a sinusoidal tone whose instantaneous frequency sweeps linearly
    from start_hz to end_hz over the duration of key.

    Uses cumulative-phase integration so phase is continuous even when
    instantaneous frequency changes rapidly.
    """
    n = len(key)
    t = np.arange(n, dtype=np.float64) / sample_rate
    # Instantaneous frequency at each sample
    inst_freq = np.linspace(start_hz, end_hz, n, dtype=np.float64)
    # Integrate phase (trapezoidal)
    phase = 2.0 * np.pi * np.cumsum(inst_freq) / sample_rate
    return np.sin(phase) * key


# QSB envelope profiles: (name, description, builder_fn)
# Each builder takes (n_samples, sample_rate, rng) and returns a float64
# amplitude envelope in [0, 1].  The envelope is multiplied onto the tone.

def _qsb_slow_sine(n, sr, rng):
    """Gentle sine fade: one full cycle over the file, depth ~10dB."""
    t = np.linspace(0, 2 * np.pi, n)
    depth = 0.7  # amplitude ratio at trough (0.7 ≈ -3dB)
    return 0.5 * (1 + depth) + 0.5 * (1 - depth) * np.cos(t)

def _qsb_multi_sine(n, sr, rng):
    """Multiple overlapping sine fades at different rates."""
    t = np.arange(n) / sr
    env = np.ones(n, dtype=np.float64)
    for period, depth in [(8.0, 0.5), (15.0, 0.3), (30.0, 0.4)]:
        phase = rng.uniform(0, 2 * np.pi)
        env *= 1.0 - depth * 0.5 * (1 - np.cos(2 * np.pi * t / period + phase))
    return np.clip(env, 0.05, 1.0)

def _qsb_random_walk(n, sr, rng):
    """Slow random-walk fading, bandwidth ~0.1Hz."""
    # Generate low-pass noise via cumsum then normalise
    steps = rng.normal(0, 1, n)
    # Low-pass: smooth over ~1s
    from scipy.ndimage import uniform_filter1d
    smooth = uniform_filter1d(steps, size=int(sr * 1.5))
    smooth -= smooth.min()
    smooth /= smooth.max() + 1e-9
    # Map to amplitude range [0.1, 1.0]
    return 0.1 + 0.9 * smooth

def _qsb_sudden_drop(n, sr, rng):
    """Signal drops 15dB for a few seconds then recovers."""
    env = np.ones(n, dtype=np.float64)
    drop_start = int(rng.uniform(0.2, 0.5) * n)
    drop_len   = int(rng.uniform(3, 8) * sr)
    fade       = int(0.3 * sr)
    drop_amp   = 10 ** (-15 / 20)  # -15dB
    env[drop_start: drop_start + drop_len] = drop_amp
    # Smooth transitions
    ramp_dn = np.linspace(1.0, drop_amp, fade)
    ramp_up = np.linspace(drop_amp, 1.0, fade)
    env[drop_start - fade: drop_start] = ramp_dn
    env[drop_start + drop_len: drop_start + drop_len + fade] = ramp_up
    return np.clip(env, 0.0, 1.0)

QSB_PROFILES = [
    ("qsb_slow",   _qsb_slow_sine),
    ("qsb_multi",  _qsb_multi_sine),
    ("qsb_walk",   _qsb_random_walk),
    ("qsb_drop",   _qsb_sudden_drop),
]


def main() -> None:
    import argparse

    default_out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "morse_tests")

    # Support original positional usage AND new named args side-by-side.
    # Positional args (message, out_dir) are kept for backward compatibility.
    parser = argparse.ArgumentParser(
        description="Generate Morse-in-noise WAV test files.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("message", nargs="?", default=DEFAULT_MESSAGE,
                        help="Text to encode")
    parser.add_argument("out_dir", nargs="?", default=None,
                        help="Output directory (positional, overridden by --out-dir)")
    parser.add_argument("--out-dir", default=default_out_dir, dest="out_dir_named",
                        help="Output directory")
    parser.add_argument("--duration", type=int, default=DURATION_SEC,
                        help="Recording length in seconds")
    parser.add_argument("--wpm", type=float, default=WPM,
                        help="Morse speed in WPM")
    parser.add_argument("--snr-list", default=None,
                        help="Comma-separated SNR dB values, e.g. '12,6,0,-6,-12,-18'")
    parser.add_argument("--drift", action="store_true",
                        help="Generate drift test files instead of standard SNR sweep")
    parser.add_argument("--drift-snr", type=float, default=-6.0,
                        help="Fixed SNR (dB) for drift test files")
    parser.add_argument("--drift-max-hz", type=float, default=200.0,
                        help="Maximum total frequency drift in Hz")
    parser.add_argument("--drift-steps", type=int, default=10,
                        help="Number of drift files (0 Hz to --drift-max-hz inclusive)")
    parser.add_argument("--qsb", action="store_true",
                        help="Generate QSB (fading) test files instead of standard SNR sweep")
    parser.add_argument("--qsb-snr-list", default="-6",
                        help="Comma-separated SNR values for QSB files")
    args = parser.parse_args()

    # Resolve output directory (positional wins if given, else --out-dir)
    out_dir = args.out_dir if args.out_dir is not None else args.out_dir_named
    os.makedirs(out_dir, exist_ok=True)

    duration_sec = args.duration
    wpm = args.wpm
    message = args.message

    # -----------------------------------------------------------------------
    # Drift test mode
    # -----------------------------------------------------------------------
    if args.drift:
        drift_values = np.linspace(0.0, args.drift_max_hz, args.drift_steps)
        snr_db = args.drift_snr
        n = int(SAMPLE_RATE * duration_sec)
        base_seed = 48

        drift_dir = os.path.join(out_dir, "drift")
        os.makedirs(drift_dir, exist_ok=True)

        print(f"Generating drift test set: {duration_sec}s, {TONE_HZ:.0f}Hz start, "
              f"{wpm:.1f}WPM, SNR{snr_db:+.0f}dB")
        print(f"Drift range: 0–{args.drift_max_hz:.0f}Hz in {args.drift_steps} steps")
        print(f"Output dir: {drift_dir}")

        # Use steady timing only for drift tests — we want to isolate the
        # frequency tracking variable, not mix in timing jitter
        key_rng = np.random.default_rng(base_seed)
        key = build_morse_key(message, SAMPLE_RATE, duration_sec, wpm,
                               timing_jitter_sigma=0.0, rng=key_rng)
        tone_rms_raw = float(np.sqrt(np.mean(key * key)))  # key RMS as reference

        for step_idx, drift_hz in enumerate(drift_values):
            noise_rng = np.random.default_rng(base_seed + step_idx)
            noise = noise_rng.normal(0.0, 1.0, n)
            noise *= NOISE_RMS / float(np.sqrt(np.mean(noise * noise)))

            # Tone sweeps from TONE_HZ to TONE_HZ + drift_hz
            tone = make_drifting_tone(key, SAMPLE_RATE,
                                       start_hz=TONE_HZ,
                                       end_hz=TONE_HZ + drift_hz)

            tone_rms = float(np.sqrt(np.mean(tone * tone)))
            target_rms = NOISE_RMS * (10.0 ** (snr_db / 20.0))
            if tone_rms > 0:
                tone = tone * (target_rms / tone_rms)

            mix = noise + tone
            peak = float(np.max(np.abs(mix)))
            if peak > PEAK_LIMIT:
                mix *= PEAK_LIMIT / peak

            fn = (f"morse_{TONE_HZ:.0f}Hz_{wpm:.0f}wpm_{duration_sec}s"
                  f"_drift{drift_hz:+.0f}Hz_SNR{snr_db:+.0f}dB.wav")
            path = os.path.join(drift_dir, fn)
            write_wav_mono_int16(path, mix, SAMPLE_RATE)
            print(f"  wrote drift/{fn}  "
                  f"(start={TONE_HZ:.0f}Hz end={TONE_HZ+drift_hz:.0f}Hz "
                  f"drift={drift_hz:+.0f}Hz)")

        print("Done.")
        return

    # -----------------------------------------------------------------------
    # QSB (fading) test mode
    # -----------------------------------------------------------------------
    if args.qsb:
        try:
            from scipy.ndimage import uniform_filter1d  # needed by qsb_walk
        except ImportError:
            pass  # qsb_walk will fail gracefully if scipy missing

        qsb_snr_list = [float(x) for x in args.qsb_snr_list.split(",")]
        n = int(SAMPLE_RATE * duration_sec)
        t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
        base_seed = 48

        qsb_dir = os.path.join(out_dir, "qsb")
        os.makedirs(qsb_dir, exist_ok=True)

        print(f"Generating QSB test set: {duration_sec}s, {TONE_HZ:.0f}Hz, {wpm:.1f}WPM")
        print(f"Profiles: {[p for p,_ in QSB_PROFILES]}")
        print(f"SNR list: {qsb_snr_list}")
        print(f"Output dir: {qsb_dir}")

        key_rng = np.random.default_rng(base_seed)
        key = build_morse_key(message, SAMPLE_RATE, duration_sec, wpm,
                               timing_jitter_sigma=0.0, rng=key_rng)
        base_tone = np.sin(2.0 * np.pi * TONE_HZ * t) * key
        tone_rms_raw = float(np.sqrt(np.mean(base_tone * base_tone)))

        for prof_idx, (prof_name, qsb_fn) in enumerate(QSB_PROFILES):
            for snr_idx, snr_db in enumerate(qsb_snr_list):
                env_rng   = np.random.default_rng(base_seed + prof_idx * 100 + snr_idx)
                noise_rng = np.random.default_rng(base_seed + 9999 + prof_idx * 100 + snr_idx)

                # Build QSB amplitude envelope
                try:
                    qsb_env = qsb_fn(n, SAMPLE_RATE, env_rng)
                except Exception as e:
                    print(f"  WARNING: {prof_name} failed ({e}), using flat envelope")
                    qsb_env = np.ones(n, dtype=np.float64)

                # Apply envelope to tone
                tone_faded = base_tone * qsb_env

                # Scale to target SNR (SNR defined relative to unfaded tone RMS
                # so the *average* level matches; instantaneous SNR varies with fade)
                tone_faded_rms = float(np.sqrt(np.mean(tone_faded * tone_faded)))
                noise = noise_rng.normal(0.0, 1.0, n)
                noise *= NOISE_RMS / float(np.sqrt(np.mean(noise * noise)))

                target_rms = NOISE_RMS * (10.0 ** (snr_db / 20.0))
                if tone_faded_rms > 0:
                    tone_faded = tone_faded * (target_rms / tone_faded_rms)

                mix = noise + tone_faded
                peak = float(np.max(np.abs(mix)))
                if peak > PEAK_LIMIT:
                    mix *= PEAK_LIMIT / peak

                fn = (f"morse_{TONE_HZ:.0f}Hz_{wpm:.0f}wpm_{duration_sec}s"
                      f"_{prof_name}_SNR{snr_db:+.0f}dB.wav")
                path = os.path.join(qsb_dir, fn)
                write_wav_mono_int16(path, mix, SAMPLE_RATE)
                env_min_db = 20 * np.log10(float(qsb_env.min()) + 1e-9)
                env_max_db = 20 * np.log10(float(qsb_env.max()) + 1e-9)
                print(f"  wrote qsb/{fn}  "
                      f"(env range: {env_min_db:.1f}→{env_max_db:.1f}dB)")

        print("Done.")
        return

    # -----------------------------------------------------------------------
    # Standard SNR sweep mode
    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # Standard SNR sweep mode
    # -----------------------------------------------------------------------
    snr_db_list = (
        [int(x) for x in args.snr_list.split(",")]
        if args.snr_list
        else list(range(12, -25, -3))
    )

    n = int(SAMPLE_RATE * duration_sec)
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE

    print(f"Generating Morse test set: {duration_sec}s, {TONE_HZ:.0f}Hz, {wpm:.1f}WPM")
    print(f"Message: {message}")
    print(f"SNR list: {snr_db_list}")
    print(f"Output dir: {out_dir}")

    base_seed = 48

    for profile_idx, (profile_name, timing_sigma) in enumerate(TIMING_PROFILES):
        profile_dir = os.path.join(out_dir, profile_name)
        os.makedirs(profile_dir, exist_ok=True)

        key_rng = np.random.default_rng(base_seed + 1000 * profile_idx)
        key = build_morse_key(
            message,
            SAMPLE_RATE,
            duration_sec,
            wpm,
            timing_jitter_sigma=timing_sigma,
            rng=key_rng,
        )
        tone = np.sin(2.0 * np.pi * TONE_HZ * t) * key

        tone_rms_raw = np.sqrt(np.mean(tone * tone))
        if tone_rms_raw <= 0:
            raise RuntimeError("Tone RMS is zero; message produced no keyed symbols.")

        print(f"\nProfile: {profile_name} (timing jitter sigma={timing_sigma:.2f})")

        for snr_idx, snr_db in enumerate(snr_db_list):
            noise_rng = np.random.default_rng(base_seed + profile_idx * 100 + snr_idx)
            noise = noise_rng.normal(0.0, 1.0, n)
            noise *= NOISE_RMS / np.sqrt(np.mean(noise * noise))

            target_tone_rms = NOISE_RMS * (10.0 ** (snr_db / 20.0))
            tone_scaled = tone * (target_tone_rms / tone_rms_raw)

            mix = noise + tone_scaled
            peak = float(np.max(np.abs(mix)))
            if peak > PEAK_LIMIT:
                mix *= PEAK_LIMIT / peak

            # Filename reflects actual duration and WPM
            fn = f"morse_{TONE_HZ:.0f}Hz_{wpm:.0f}wpm_{duration_sec}s_{profile_name}_SNR{snr_db:+d}dB.wav"
            path = os.path.join(profile_dir, fn)
            write_wav_mono_int16(path, mix, SAMPLE_RATE)

            print(
                f"  wrote {profile_name}/{fn}  "
                f"(tone_rms={target_tone_rms:.6f}, noise_rms={NOISE_RMS:.6f}, peak={np.max(np.abs(mix)):.3f})"
            )

    print("Done.")


if __name__ == "__main__":
    main()