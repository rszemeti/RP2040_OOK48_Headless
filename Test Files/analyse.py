#!/usr/bin/env python3
# ook48_decode.py  v1.4
"""
ook48_decode.py
Software simulation of the OOK48 decoder with blind accumulation.

Two decoders run in parallel:
  - Single pass: matches original firmware, decodes each character independently
  - Accumulated: discovers message length by autocorrelation, then accumulates
    soft magnitudes across repeats before deciding - no prior knowledge needed

Usage:
    python ook48_decode.py [file1.wav] [file2.wav] ...
    Defaults to OOK48Test.wav

Requirements: pip install numpy scipy matplotlib
"""

import sys
import os
import re
import numpy as np
from scipy.io import wavfile
from scipy.signal import find_peaks
import matplotlib.pyplot as plt

# ---------------------------------------------------------------------------
# 4-from-8 decode table (from firmware globals.cpp)
# ---------------------------------------------------------------------------
DECODE_4FROM8 = [
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,13,0,0,0,0,
    0,0,0,32,0,0,0,33,0,34,35,0,0,0,0,0,0,0,0,36,
    0,0,0,37,0,38,39,0,0,0,0,40,0,41,42,0,0,43,44,0,
    45,0,0,0,0,0,0,0,0,0,0,46,0,0,0,47,0,48,49,0,
    0,0,0,50,0,51,52,0,0,53,54,0,55,0,0,0,0,0,0,56,
    0,57,58,0,0,59,60,0,61,0,0,0,0,62,63,0,64,0,0,0,
    65,0,0,0,0,0,0,0,0,0,0,0,0,0,0,66,0,0,0,67,
    0,68,69,0,0,0,0,70,0,71,72,0,0,73,74,0,75,0,0,0,
    0,0,0,76,0,77,78,0,0,79,80,0,81,0,0,0,0,82,83,0,
    84,0,0,0,85,0,0,0,0,0,0,0,0,0,0,86,0,87,88,0,
    0,89,90,0,91,0,0,0,0,92,93,0,94,0,0,0,95,0,0,0,
    0,0,0,0,0,126,126,0,126,0,0,0,126,0,0,0,0,0,0,0,
    126,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
]

KNOWN_MSG        = "OOK48 TEST\r"   # for scoring only
MSG_LEN          = len(KNOWN_MSG)
SAMPLE_RATE      = 44100
SAMPLES_PER_SYM  = 4900
FFT_LEN          = SAMPLES_PER_SYM
TONE_FREQ        = 800
TONE_BIN         = round(TONE_FREQ * FFT_LEN / SAMPLE_RATE)
TONE_TOLERANCE   = 3
MIN_MSG_LEN      = 3
MAX_MSG_LEN      = 30

CONFIDENCE_THRESHOLD = 0.180   # from empirical analysis - below this, return UNK
UNK_CODEWORD         = 0xF0    # spare 4-from-8 pattern (11110000) = uncertain
UNK_CHAR             = '\x7e'  # displayed as ~ in serial stream as <UNK>

print(f"FFT length: {FFT_LEN}  Tone bin: {TONE_BIN} ({TONE_BIN * SAMPLE_RATE / FFT_LEN:.1f} Hz)  "
      f"Search range: bins {TONE_BIN-TONE_TOLERANCE}-{TONE_BIN+TONE_TOLERANCE}")

# ---------------------------------------------------------------------------
def find_clicks(click_ch, sample_rate):
    click_abs = np.abs(click_ch)
    threshold = np.max(click_abs) * 0.3
    min_dist  = int(sample_rate * 0.8)
    peaks, _  = find_peaks(click_abs, height=threshold, distance=min_dist)
    return peaks

def symbol_magnitudes_allbins(tone_ch, start_sample):
    end = start_sample + FFT_LEN
    if end > len(tone_ch):
        return None
    window  = np.hanning(FFT_LEN)
    segment = tone_ch[start_sample:end] * window
    fft     = np.abs(np.fft.rfft(segment, n=FFT_LEN))
    lo = TONE_BIN - TONE_TOLERANCE
    hi = TONE_BIN + TONE_TOLERANCE + 1
    return fft[lo:hi]

def get_symbol_magnitudes(tone_ch, char_start):
    """8 per-symbol magnitudes for one character, max across bins."""
    n_bins = TONE_TOLERANCE * 2 + 1
    cache  = np.zeros((8, n_bins))
    for sym in range(8):
        mags = symbol_magnitudes_allbins(tone_ch, char_start + sym * SAMPLES_PER_SYM)
        if mags is not None:
            cache[sym] = mags
    return cache.max(axis=1)

def decode_from_magnitudes(temp, use_confidence=True):
    """
    Pick 4 largest magnitudes, decode. Matches firmware exactly.
    If use_confidence=True and confidence is below threshold, return UNK_CHAR.
    Returns (char, bits, confidence)
    """
    # Confidence: gap between 4th and 5th ranked magnitudes / full range
    ranked     = np.sort(temp)[::-1]
    gap        = ranked[3] - ranked[4]
    rng        = ranked[0] - ranked[7]
    confidence = gap / rng if rng > 0 else 0.0

    bits      = [0] * 8
    temp_copy = temp.copy()
    for _ in range(4):
        idx       = int(np.argmax(temp_copy))
        bits[idx] = 1
        temp_copy[idx] = 0

    byte_val  = 0
    for b in bits:
        byte_val = (byte_val << 1) | b

    if use_confidence and confidence < CONFIDENCE_THRESHOLD:
        return UNK_CHAR, bits, confidence

    char_code = DECODE_4FROM8[byte_val] if byte_val < len(DECODE_4FROM8) else 0
    char      = chr(char_code) if char_code > 0 else '?'
    return char, bits, confidence

def find_phase(decoded_chars, msg_len):
    """Try all phases, return the one with most matches to KNOWN_MSG. Skips UNK."""
    best_score, phase = -1, 0
    for p in range(msg_len):
        score = sum(1 for i, c in enumerate(decoded_chars)
                    if c != UNK_CHAR and c == KNOWN_MSG[(i + p) % msg_len])
        if score > best_score:
            best_score, phase = score, p
    return phase, best_score

def score_decodes(decoded_chars, phase, msg_len):
    correct = sum(1 for i, c in enumerate(decoded_chars)
                  if c != UNK_CHAR and c == KNOWN_MSG[(i + phase) % msg_len])
    scored  = sum(1 for c in decoded_chars if c != UNK_CHAR)
    unk     = sum(1 for c in decoded_chars if c == UNK_CHAR)
    return correct, scored, unk

# ---------------------------------------------------------------------------
def detect_message_length(raw_mags, min_l=MIN_MSG_LEN, max_l=MAX_MSG_LEN, verbose=True):
    """
    Blind message length detection by autocorrelation.
    Subtracts mean score to remove noise floor bias, then picks the peak.
    If the winner is a multiple of a smaller candidate with similar normalised
    score, prefer the smaller one (avoids detecting 22 instead of 11).
    """
    n = len(raw_mags)
    scores = {}

    for L in range(min_l, min(max_l + 1, n // 2 + 1)):
        corr = 0.0
        count = 0
        for i in range(n - L):
            a = raw_mags[i]
            b = raw_mags[i + L]
            na = np.linalg.norm(a)
            nb = np.linalg.norm(b)
            if na > 0 and nb > 0:
                corr += np.dot(a, b) / (na * nb)
                count += 1
        scores[L] = corr / count if count > 0 else 0.0

    # Normalise: subtract mean to expose the true signal above noise floor
    mean_score = np.mean(list(scores.values()))
    norm_scores = {L: s - mean_score for L, s in scores.items()}

    best_L = max(norm_scores, key=norm_scores.get)

    # Check if any divisor of best_L has a norm_score within 50% of the winner
    # If so, prefer the smallest such divisor (avoids 22 when 11 is the answer)
    for divisor in range(2, best_L):
        if best_L % divisor == 0:
            candidate = best_L // divisor
            if candidate >= min_l and candidate in norm_scores:
                if norm_scores[candidate] >= 0.5 * norm_scores[best_L]:
                    best_L = candidate
                    break

    if verbose:
        print(f"\n--- Message length detection ---")
        print(f"{'L':>4}  {'Score':>8}  {'Norm':>8}")
        for L in range(min_l, min(max_l + 1, n // 2 + 1)):
            marker = " ◄ best" if L == best_L else ""
            print(f"{L:>4}  {scores[L]:>8.4f}  {norm_scores[L]:>8.4f}{marker}")
        print(f"Detected message length: {best_L}")

    return best_L, scores

# ---------------------------------------------------------------------------
def accumulate_and_decode(raw_mags, msg_len, n_acc):
    """
    Accumulate soft magnitudes across n_acc repeats then decode.
    Phase-free: just fold modulo msg_len and sum.
    Returns list of decoded characters.
    """
    # Accumulate first n_acc * msg_len characters
    limit       = n_acc * msg_len
    accumulated = np.zeros((msg_len, 8))
    counts      = np.zeros(msg_len)

    for i, mags in enumerate(raw_mags):
        if i >= limit:
            break
        pos = i % msg_len
        accumulated[pos] += mags
        counts[pos] += 1

    # Decode every character using the accumulated magnitudes for its position
    decoded = []
    for i, mags in enumerate(raw_mags):
        pos = i % msg_len
        if counts[pos] > 0:
            acc_mag = accumulated[pos] / counts[pos]
        else:
            acc_mag = mags
        char, _, _ = decode_from_magnitudes(acc_mag, use_confidence=False)
        decoded.append(char)

    return decoded

# ---------------------------------------------------------------------------
def decode_file(filename, plot=True, verbose=True, max_accumulate=8):
    if verbose:
        print(f"\n{'='*60}")
        print(f"Decoding: {os.path.basename(filename)}")
        print(f"{'='*60}")

    sample_rate, data = wavfile.read(filename)
    if data.ndim == 1:
        print("Error: need stereo file (ch0=GPS, ch1=tone)")
        return None

    click_ch = data[:, 0].astype(np.float64)
    tone_ch  = data[:, 1].astype(np.float64)

    clicks = find_clicks(click_ch, sample_rate)
    if verbose:
        print(f"GPS clicks found: {len(clicks)}")
    if len(clicks) < MAX_MSG_LEN + 1:
        print("Not enough clicks")
        return None

    # Collect raw magnitude arrays
    raw_mags = [get_symbol_magnitudes(tone_ch, int(c)) for c in clicks]

    # Single pass decode
    single_chars = [decode_from_magnitudes(m, use_confidence=True)[0] for m in raw_mags]
    phase_s, _   = find_phase(single_chars, MSG_LEN)
    correct_s, total_s, unk_s = score_decodes(single_chars, phase_s, MSG_LEN)
    single_err   = 1.0 - correct_s / total_s if total_s > 0 else 1.0

    if verbose:
        print(f"\n--- Single pass (confidence threshold={CONFIDENCE_THRESHOLD:.3f}) ---")
        for i, (char, mags) in enumerate(zip(single_chars, raw_mags)):
            _, bits, conf = decode_from_magnitudes(mags)
            display  = '<CR>' if char == '\r' else ('<UNK>' if char == UNK_CHAR else repr(char))
            expected = KNOWN_MSG[(i + phase_s) % MSG_LEN]
            ok       = '✓' if char == expected else ('~' if char == UNK_CHAR else '✗')
            print(f"  Char {i:3d}: bits={''.join(str(b) for b in bits)}  conf={conf:.3f}  {display:8s}  {ok}")
        print(f"Correct: {correct_s}/{total_s}  UNK: {unk_s}  error: {100*single_err:.1f}%")

    # Blind message length detection
    detected_L, length_scores = detect_message_length(raw_mags, verbose=verbose)

    # Accumulated decode at various repeat counts
    acc_results = {}
    for n_acc in range(1, max_accumulate + 1):
        acc_chars          = accumulate_and_decode(raw_mags, detected_L, n_acc)
        phase_a, _         = find_phase(acc_chars, MSG_LEN)
        correct_a, total_a, unk_a = score_decodes(acc_chars, phase_a, MSG_LEN)
        err                = 1.0 - correct_a / total_a if total_a > 0 else 1.0
        acc_results[n_acc] = (correct_a, total_a, err, unk_a)

    if verbose:
        print(f"\n--- Accumulated decode (detected L={detected_L}) ---")
        print(f"{'Repeats':>8}  {'Correct':>8}  {'UNK':>6}  {'Error%':>8}")
        for n, (c, t, e, u) in acc_results.items():
            print(f"{n:>8}  {c:>4}/{t:<4}  {u:>6}  {100*e:>7.1f}%")

    # Check if detected_L is a multiple of the true length
    # Prefer the smallest divisor that still gives a good autocorrelation score
    # (handles the case where L=22 is detected instead of L=11)
    best_L = detected_L
    for divisor in range(2, detected_L):
        if detected_L % divisor == 0:
            candidate = detected_L // divisor
            if candidate >= MIN_MSG_LEN:
                # Check if this candidate also has a good score
                if length_scores.get(candidate, 0) > 0.7 * length_scores[detected_L]:
                    best_L = candidate
                    break
    if best_L != detected_L and verbose:
        print(f"  Note: reducing detected L={detected_L} to L={best_L} (factor of original)")

    return {
        'file'         : filename,
        'clicks'       : len(clicks),
        'detected_L'   : detected_L,
        'length_scores': length_scores,
        'single_err'   : single_err,
        'acc_results'  : acc_results,
        'raw_mags_len' : len(raw_mags),
    }

# ---------------------------------------------------------------------------
def main():
    files = sys.argv[1:] if len(sys.argv) > 1 else ["OOK48Test.wav"]
    files = [f for f in files if os.path.exists(f)]
    if not files:
        print("No files found")
        return

    results = []
    for f in files:
        r = decode_file(f, plot=False, verbose=(len(files) == 1))
        if r:
            results.append(r)

    if not results:
        return

    # Summary table
    print(f"\n{'='*85}")
    print(f"{'File':<35} {'L':>3} {'Single':>8} {'x2':>7} {'x4':>7} {'x8':>7} {'UNK%':>7}")
    print(f"{'-'*85}")
    for r in results:
        name = os.path.basename(r['file'])[:34]
        L    = r['detected_L']
        Lstr = f"{L}{'*' if L != MSG_LEN else ''}"
        se   = f"{100*r['single_err']:5.1f}%"
        a2   = f"{100*r['acc_results'].get(2,(0,0,1,0))[2]:5.1f}%"
        a4   = f"{100*r['acc_results'].get(4,(0,0,1,0))[2]:5.1f}%"
        a8   = f"{100*r['acc_results'].get(8,(0,0,1,0))[2]:5.1f}%"
        unk_s  = r['acc_results'].get(1,(0,0,0,0))[3]
        n_tot  = r.get('raw_mags_len', 180)
        unk_pct = f"{100*unk_s/n_tot:5.1f}%" if n_tot > 0 else "  0.0%"
        print(f"{name:<35} {Lstr:>4} {se:>8} {a2:>7} {a4:>7} {a8:>7} {unk_pct:>7}")

    # SNR curve
    snr_vals = []
    for r in results:
        m = re.search(r'SNR([+-]\d+)dB', os.path.basename(r['file']))
        if m:
            snr_vals.append((int(m.group(1)), r))

    if len(snr_vals) > 1:
        snr_vals.sort(key=lambda x: x[0], reverse=True)
        snrs   = [s for s, _ in snr_vals]
        single = [100 * r['single_err'] for _, r in snr_vals]
        acc2   = [100 * r['acc_results'].get(2,(0,0,1))[2] for _, r in snr_vals]
        acc4   = [100 * r['acc_results'].get(4,(0,0,1))[2] for _, r in snr_vals]
        acc8   = [100 * r['acc_results'].get(8,(0,0,1))[2] for _, r in snr_vals]

        fig, axes = plt.subplots(1, 2, figsize=(14, 5))

        # Left: error rate curves
        ax = axes[0]
        ax.plot(snrs, single, 'o-',  color='steelblue',  linewidth=2, label='Single pass')
        ax.plot(snrs, acc2,   's--', color='darkorange', linewidth=2, label='Accumulated x2')
        ax.plot(snrs, acc4,   '^--', color='darkgreen',  linewidth=2, label='Accumulated x4')
        ax.plot(snrs, acc8,   'D--', color='crimson',    linewidth=2, label='Accumulated x8')
        ax.axhline(10, color='grey', linestyle='--', linewidth=0.8, alpha=0.7, label='10% threshold')
        ax.set_xlabel("SNR (dB)")
        ax.set_ylabel("Character error rate (%)")
        ax.set_title("OOK48 Decoder Performance vs SNR")
        ax.invert_xaxis()
        ax.legend(fontsize=9)
        ax.grid(True, alpha=0.3)

        # Right: detected message length vs SNR
        ax2 = axes[1]
        detected_Ls = [r['detected_L'] for _, r in snr_vals]
        ax2.plot(snrs, detected_Ls, 'o-', color='purple', linewidth=2)
        ax2.axhline(MSG_LEN, color='grey', linestyle='--', linewidth=1,
                    label=f'True length ({MSG_LEN})')
        ax2.set_xlabel("SNR (dB)")
        ax2.set_ylabel("Detected message length")
        ax2.set_title("Blind Message Length Detection")
        ax2.invert_xaxis()
        ax2.legend(fontsize=9)
        ax2.grid(True, alpha=0.3)
        ax2.set_ylim(0, MAX_MSG_LEN + 2)

        plt.tight_layout()
        plt.savefig("ook48_snr_curve.png", dpi=150)
        print(f"\nSNR curve saved to: ook48_snr_curve.png")
        plt.show()

if __name__ == "__main__":
    main()