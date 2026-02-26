#!/usr/bin/env python3
"""
rainscatter_test_harness.py

Generates no-carrier noise-shift keyed WAV test files from an input OOK48 WAV
and evaluates decode stability with the GUI accumulator class using two
feature extractors:

- tone_peak: per-symbol peak in the narrow 800 Hz tone window
- wideband: per-symbol summed power across the full OOK analysis band
- wideband_norm: wideband power after subtracting per-symbol band floor

Signal model for generated files:
- No carrier tone is present.
- Entire receive channel is noise.
- Symbols that were tone-present in the original file are encoded as higher
    noise power; tone-absent symbols remain at baseline noise power.

Usage:
    python rainscatter_test_harness.py [input.wav]

Defaults:
    input.wav = OOK48Test.wav

Outputs:
    - Generated WAV files in ./rainscatter_tests/
    - Console summary comparing tone_peak vs wideband accumulator behavior
"""

import os
import re
import sys
import numpy as np
from scipy.io import wavfile
from scipy.signal import find_peaks, butter, sosfilt

HERE = os.path.dirname(os.path.abspath(__file__))
GUI_DIR = os.path.abspath(os.path.join(HERE, "..", "gui"))
if GUI_DIR not in sys.path:
    sys.path.insert(0, GUI_DIR)

from ook_accumulator import OOK48Accumulator, _decode_mags

SAMPLES_PER_SYM = 4900
FFT_LEN = SAMPLES_PER_SYM
TONE_FREQ = 800.0
TONE_TOLERANCE = 3
OOK_START_HZ = 495.0
OOK_END_HZ = 1098.0
DEFAULT_BANDWIDTH_HZ = 3500
DEFAULT_POWER_STEPS_DB = [10, 6, 3, 2, 1]
EXPECTED_MESSAGE = "OOK48 TEST\r"


def rms(x):
    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0


def dbfs(v, full_scale=1.0):
    if v <= 0:
        return -120.0
    return 20.0 * np.log10(v / full_scale)


def find_clicks(click_ch, sample_rate):
    click_abs = np.abs(click_ch)
    threshold = np.max(click_abs) * 0.3
    peaks, _ = find_peaks(click_abs, height=threshold, distance=int(sample_rate * 0.8))
    return peaks


def bandlimit_noise(noise, sample_rate, bandwidth_hz):
    nyquist = sample_rate / 2.0
    if bandwidth_hz >= nyquist:
        return noise
    sos = butter(8, bandwidth_hz / nyquist, btype="low", output="sos")
    filtered = sosfilt(sos, noise)
    in_rms = rms(noise)
    out_rms = rms(filtered)
    if out_rms > 0 and in_rms > 0:
        filtered *= in_rms / out_rms
    return filtered


def extract_symbol_vectors(tone_ch, click_positions, sample_rate, mode):
    tone_bin = round(TONE_FREQ * FFT_LEN / sample_rate)
    tone_lo = max(0, tone_bin - TONE_TOLERANCE)
    tone_hi = tone_bin + TONE_TOLERANCE + 1

    wb_lo = max(0, int(np.floor(OOK_START_HZ * FFT_LEN / sample_rate)))
    wb_hi = int(np.ceil(OOK_END_HZ * FFT_LEN / sample_rate)) + 1

    vectors = []
    window = np.hanning(FFT_LEN)

    for click in click_positions:
        v = np.zeros(8, dtype=np.float64)
        char_start = int(click)
        for sym in range(8):
            start = char_start + sym * SAMPLES_PER_SYM
            end = start + FFT_LEN
            if end > len(tone_ch):
                break
            seg = tone_ch[start:end] * window
            fft = np.abs(np.fft.rfft(seg, n=FFT_LEN))

            if mode == "tone_peak":
                v[sym] = float(np.max(fft[tone_lo:tone_hi]))
            elif mode == "wideband":
                v[sym] = float(np.sum(fft[wb_lo:wb_hi]))
            elif mode == "wideband_norm":
                band = fft[wb_lo:wb_hi]
                floor = float(np.percentile(band, 40.0))
                v[sym] = float(np.sum(np.maximum(band - floor, 0.0)))
            else:
                raise ValueError(f"Unknown mode: {mode}")

        vectors.append(v)

    return vectors


def detect_symbol_activity(tone_ch, click_positions, sample_rate):
    """
    Detect original symbol ON/OFF map from narrowband tone energy.
    Returns list[char][8] of booleans where True means symbol was tone-present.
    """
    tone_vectors = extract_symbol_vectors(tone_ch, click_positions, sample_rate, mode="tone_peak")
    flat = np.array([v for row in tone_vectors for v in row], dtype=np.float64)
    if flat.size == 0:
        return []

    lo = np.percentile(flat, 25)
    hi = np.percentile(flat, 75)
    threshold = (lo + hi) * 0.5

    activity = []
    for row in tone_vectors:
        activity.append([bool(x >= threshold) for x in row])
    return activity


def best_rotation_match(decoded, expected):
    if not decoded or not expected:
        return 0.0, 0
    n = min(len(decoded), len(expected))
    if n == 0:
        return 0.0, 0

    best = 0
    best_phase = 0
    for phase in range(n):
        score = sum(1 for i in range(n) if decoded[i] == expected[(i + phase) % n])
        if score > best:
            best = score
            best_phase = phase
    return best / n, best_phase


def run_accumulator(vectors):
    if vectors and len(vectors) < 120:
        reps = int(np.ceil(120 / len(vectors)))
        vectors = (vectors * reps)[:120]

    acc = OOK48Accumulator()
    for mags in vectors:
        acc.push(mags)
    state = acc.get_display_state()

    msg_len = state.get("msg_len")
    repeats = state.get("repeats", 0)
    mean_conf = state.get("mean_confidence", 0.0)
    decoded = ""
    unk_pct = 0.0

    chars = state.get("chars", [])
    if chars:
        decoded = "".join(c["char"] for c in chars)
        unk_count = sum(1 for c in chars if c["char"] == "~")
        unk_pct = 100.0 * unk_count / len(chars)

    copies = state.get("copies", [])
    confirmed = copies[-1]["message"] if copies else ""

    return {
        "state": state.get("state_label", "SEARCHING"),
        "msg_len": msg_len,
        "repeats": repeats,
        "mean_conf": mean_conf,
        "decoded": decoded,
        "unk_pct": unk_pct,
        "confirmed": confirmed,
    }


def decode_text_at_depth(vectors, msg_len, depth):
    if not vectors or not msg_len or msg_len <= 0:
        return ""

    need = depth * msg_len
    if len(vectors) < need:
        reps = int(np.ceil(need / len(vectors)))
        seq = (vectors * reps)[:need]
    else:
        seq = vectors[:need]

    accum = np.zeros((msg_len, 8), dtype=np.float64)
    counts = np.zeros(msg_len, dtype=np.int32)
    for i, mags in enumerate(seq):
        pos = i % msg_len
        accum[pos] += mags
        counts[pos] += 1

    out = []
    for pos in range(msg_len):
        if counts[pos] <= 0:
            out.append('?')
            continue
        avg = accum[pos] / counts[pos]
        ch, _ = _decode_mags(avg)
        out.append(ch)
    return "".join(out)


def _to_dtype_and_stereo(tone_out, click, src_dtype):
    if src_dtype == np.int16:
        tone_cast = np.clip(tone_out, -32768, 32767).astype(np.int16)
        click_cast = click.astype(np.int16) if click is not None else None
    elif src_dtype == np.int32:
        tone_cast = np.clip(tone_out, -2147483648, 2147483647).astype(np.int32)
        click_cast = click.astype(np.int32) if click is not None else None
    elif src_dtype == np.uint8:
        tone_cast = np.clip(tone_out, 0, 255).astype(np.uint8)
        click_cast = click.astype(np.uint8) if click is not None else None
    else:
        tone_cast = tone_out.astype(src_dtype)
        click_cast = click.astype(src_dtype) if click is not None else None

    if click_cast is not None:
        return np.column_stack((click_cast, tone_cast))
    return tone_cast


def make_noise_shift_files(input_file, out_dir, power_steps_db, bandwidth_hz):
    sr, data = wavfile.read(input_file)

    if data.ndim == 1:
        tone = data.astype(np.float64)
        click = None
    else:
        click = data[:, 0].astype(np.float64)
        tone = data[:, 1].astype(np.float64)

    if data.dtype == np.int16:
        full_scale = 32768.0
    elif data.dtype == np.int32:
        full_scale = 2147483648.0
    elif data.dtype == np.uint8:
        full_scale = 255.0
    else:
        full_scale = 1.0

    signal_rms = rms(tone)
    signal_peak = float(np.max(np.abs(tone))) if len(tone) else 0.0
    print(f"Reference RMS {dbfs(signal_rms, full_scale):+.1f} dBFS, peak {dbfs(signal_peak, full_scale):+.1f} dBFS")

    if click is None:
        raise RuntimeError("Need stereo WAV with click channel for symbol alignment")

    click_positions = find_clicks(click, sr)
    activity = detect_symbol_activity(tone, click_positions, sr)
    if not activity:
        raise RuntimeError("Could not derive symbol activity from source file")

    on_count = sum(sum(1 for b in row if b) for row in activity)
    total_count = len(activity) * 8
    print(f"Detected activity map: {on_count}/{total_count} symbols ON")

    base = os.path.splitext(os.path.basename(input_file))[0]
    os.makedirs(out_dir, exist_ok=True)

    out_files = []
    baseline_rms = signal_rms if signal_rms > 0 else (0.01 * full_scale)

    for step_db in power_steps_db:
        low_noise = np.random.normal(0.0, 1.0, len(tone))
        low_noise = bandlimit_noise(low_noise, sr, bandwidth_hz)
        low_r = rms(low_noise)
        if low_r > 0:
            low_noise *= baseline_rms / low_r

        tone_out = low_noise.copy()
        high_gain = 10 ** (step_db / 20.0)

        for cidx, click_pos in enumerate(click_positions[:len(activity)]):
            char_start = int(click_pos)
            row = activity[cidx]
            for sym in range(8):
                if not row[sym]:
                    continue
                start = char_start + sym * SAMPLES_PER_SYM
                end = min(start + SAMPLES_PER_SYM, len(tone_out))
                if start >= len(tone_out) or end <= start:
                    continue

                seg_len = end - start
                hi_seg = np.random.normal(0.0, 1.0, seg_len)
                hi_seg = bandlimit_noise(hi_seg, sr, bandwidth_hz)
                hi_r = rms(hi_seg)
                target_hi = baseline_rms * high_gain
                if hi_r > 0:
                    hi_seg *= target_hi / hi_r
                tone_out[start:end] = hi_seg

        out_name = f"{base}_RS_PWR+{step_db}dB.wav"
        out_path = os.path.join(out_dir, out_name)
        out_data = _to_dtype_and_stereo(tone_out, click, data.dtype)
        wavfile.write(out_path, sr, out_data)
        out_files.append(out_path)

    return out_files


def evaluate_file(path, expected):
    sr, data = wavfile.read(path)
    if data.ndim != 2:
        raise RuntimeError(f"Need stereo WAV for analysis: {path}")

    click_ch = data[:, 0].astype(np.float64)
    tone_ch = data[:, 1].astype(np.float64)

    clicks = find_clicks(click_ch, sr)

    tone_vectors = extract_symbol_vectors(tone_ch, clicks, sr, mode="tone_peak")
    wide_vectors = extract_symbol_vectors(tone_ch, clicks, sr, mode="wideband")
    wide_norm_vectors = extract_symbol_vectors(tone_ch, clicks, sr, mode="wideband_norm")

    tone_res = run_accumulator(tone_vectors)
    wide_res = run_accumulator(wide_vectors)
    wide_norm_res = run_accumulator(wide_norm_vectors)

    tone_match, tone_phase = best_rotation_match(tone_res["decoded"], expected)
    wide_match, wide_phase = best_rotation_match(wide_res["decoded"], expected)
    wide_norm_match, wide_norm_phase = best_rotation_match(wide_norm_res["decoded"], expected)

    tone_res["match"] = tone_match
    tone_res["phase"] = tone_phase
    wide_res["match"] = wide_match
    wide_res["phase"] = wide_phase
    wide_norm_res["match"] = wide_norm_match
    wide_norm_res["phase"] = wide_norm_phase

    return {
        "file": os.path.basename(path),
        "clicks": len(clicks),
        "tone": tone_res,
        "wide": wide_res,
        "wide_norm": wide_norm_res,
        "wide_text_x2": decode_text_at_depth(wide_vectors, wide_res.get("msg_len"), 2),
        "wide_text_x4": decode_text_at_depth(wide_vectors, wide_res.get("msg_len"), 4),
        "wide_text_x8": decode_text_at_depth(wide_vectors, wide_res.get("msg_len"), 8),
    }


def parse_power_step(name):
    m = re.search(r"PWR\+([0-9]+)dB", name)
    return int(m.group(1)) if m else -1


def printable_text(s):
    if s is None:
        return ""
    return s.replace("\r", "<CR>").replace("\n", "<LF>")


def main():
    input_file = sys.argv[1] if len(sys.argv) > 1 else "OOK48Test.wav"
    input_path = input_file if os.path.isabs(input_file) else os.path.join(HERE, input_file)

    if not os.path.exists(input_path):
        print(f"Input not found: {input_path}")
        sys.exit(1)

    out_dir = os.path.join(HERE, "rainscatter_tests")
    print(f"Generating noise-shift keyed test set from {os.path.basename(input_path)}")
    print(f"Output dir: {out_dir}")

    test_files = make_noise_shift_files(
        input_file=input_path,
        out_dir=out_dir,
        power_steps_db=DEFAULT_POWER_STEPS_DB,
        bandwidth_hz=DEFAULT_BANDWIDTH_HZ,
    )

    results = [evaluate_file(f, EXPECTED_MESSAGE) for f in test_files]
    results.sort(key=lambda r: parse_power_step(r["file"]), reverse=True)

    print("\n=== Accumulator comparison (tone_peak vs wideband vs wideband_norm) ===")
    print(f"{'PWRΔ':>5}  {'L_t':>4} {'L_w':>4} {'L_wn':>5} {'m_t':>6} {'m_w':>6} {'m_wn':>6} {'u_t':>6} {'u_w':>6} {'u_wn':>6} {'state_t':>9} {'state_w':>9} {'state_wn':>9}")

    for r in results:
        step = parse_power_step(r["file"])
        t = r["tone"]
        w = r["wide"]
        wn = r["wide_norm"]
        print(
            f"+{step:>3d}dB "
            f"{str(t['msg_len']):>4} {str(w['msg_len']):>4} {str(wn['msg_len']):>5} "
            f"{100.0*t['match']:>5.1f}% {100.0*w['match']:>5.1f}% {100.0*wn['match']:>5.1f}% "
            f"{t['unk_pct']:>5.1f}% {w['unk_pct']:>5.1f}% {wn['unk_pct']:>5.1f}% "
            f"{t['state']:>9} {w['state']:>9} {wn['state']:>9}"
        )
        print(
            f"         WB text x2='{printable_text(r['wide_text_x2'])}'  "
            f"x4='{printable_text(r['wide_text_x4'])}'  "
            f"x8='{printable_text(r['wide_text_x8'])}'"
        )

    print("\nDone. Use these WAVs for GUI/firmware spot checks:")
    for f in test_files:
        print(f"  - {f}")


if __name__ == "__main__":
    main()
