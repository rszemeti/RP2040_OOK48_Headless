#!/usr/bin/env python3
"""
ook48_confidence.py
Analyses the confidence score distribution for correct vs incorrect decodes
across multiple SNR levels, to find the optimal threshold for flagging
uncertain decodes as '?' rather than forcing a character.

Confidence metric: gap between 4th and 5th ranked magnitudes,
normalised by the overall range (max - min).

  confidence = (mag[rank4] - mag[rank5]) / (mag[rank1] - mag[rank8])

Usage:
    python ook48_confidence.py OOK48Test_180s_SNR-17dB_3500Hz.wav ...

# Command used (180s set):
# c:/Users/robin/Documents/GitHub/RP2040_OOK48_Headless/.venv/Scripts/python.exe .\confidence_levels.py .\OOK48Test_180s_SNR-15dB_3500Hz.wav .\OOK48Test_180s_SNR-16dB_3500Hz.wav .\OOK48Test_180s_SNR-17dB_3500Hz.wav .\OOK48Test_180s_SNR-18dB_3500Hz.wav .\OOK48Test_180s_SNR-19dB_3500Hz.wav .\OOK48Test_180s_SNR-20dB_3500Hz.wav .\OOK48Test_180s_SNR-21dB_3500Hz.wav .\OOK48Test_180s_SNR-22dB_3500Hz.wav .\OOK48Test_180s_SNR-23dB_3500Hz.wav .\OOK48Test_180s_SNR-24dB_3500Hz.wav .\OOK48Test_180s_SNR-25dB_3500Hz.wav

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

KNOWN_MSG        = "OOK48 TEST\r"
MSG_LEN          = len(KNOWN_MSG)
SAMPLE_RATE      = 44100
SAMPLES_PER_SYM  = 4900
FFT_LEN          = SAMPLES_PER_SYM
TONE_BIN         = round(800 * FFT_LEN / SAMPLE_RATE)
TONE_TOLERANCE   = 3
MIN_MSG_LEN      = 3
MAX_MSG_LEN      = 30
CURRENT_THRESHOLD = 0.180

# ---------------------------------------------------------------------------
def find_clicks(click_ch):
    click_abs = np.abs(click_ch)
    peaks, _  = find_peaks(click_abs, height=np.max(click_abs)*0.3,
                            distance=int(SAMPLE_RATE*0.8))
    return peaks

def get_symbol_magnitudes(tone_ch, char_start):
    n_bins = TONE_TOLERANCE * 2 + 1
    cache  = np.zeros((8, n_bins))
    lo, hi = TONE_BIN - TONE_TOLERANCE, TONE_BIN + TONE_TOLERANCE + 1
    for sym in range(8):
        s = char_start + sym * SAMPLES_PER_SYM
        if s + FFT_LEN > len(tone_ch): break
        seg = tone_ch[s:s+FFT_LEN] * np.hanning(FFT_LEN)
        fft = np.abs(np.fft.rfft(seg, n=FFT_LEN))
        cache[sym] = fft[lo:hi]
    return cache.max(axis=1)

def decode_and_confidence(temp):
    """
    Decode from 8 magnitudes. Returns (char, confidence).
    confidence = gap between 4th and 5th ranked magnitudes,
                 normalised by full range.
    """
    ranked   = np.sort(temp)[::-1]   # descending
    gap      = ranked[3] - ranked[4]
    rng      = ranked[0] - ranked[7]
    confidence = gap / rng if rng > 0 else 0.0

    # Pick 4 largest
    bits      = [0] * 8
    temp_copy = temp.copy()
    for _ in range(4):
        idx       = int(np.argmax(temp_copy))
        bits[idx] = 1
        temp_copy[idx] = 0
    byte_val = sum(b << (7-i) for i,b in enumerate(bits))
    char_code = DECODE_4FROM8[byte_val] if byte_val < len(DECODE_4FROM8) else 0
    char = chr(char_code) if char_code > 0 else '?'
    return char, confidence

def detect_length(raw_mags):
    n = len(raw_mags)
    scores = {}
    for L in range(MIN_MSG_LEN, min(MAX_MSG_LEN+1, n//2+1)):
        corr, count = 0.0, 0
        for i in range(n - L):
            a, b = raw_mags[i], raw_mags[i+L]
            na, nb = np.linalg.norm(a), np.linalg.norm(b)
            if na > 0 and nb > 0:
                corr += np.dot(a,b) / (na*nb); count += 1
        scores[L] = corr/count if count > 0 else 0.0
    mean_s = np.mean(list(scores.values()))
    norm_s = {L: s-mean_s for L,s in scores.items()}
    best_L = max(norm_s, key=norm_s.get)
    for div in range(2, best_L):
        if best_L % div == 0:
            cand = best_L // div
            if cand >= MIN_MSG_LEN and norm_s.get(cand,0) >= 0.5*norm_s[best_L]:
                best_L = cand; break
    return best_L

def find_phase(chars, msg_len):
    best, phase = -1, 0
    for p in range(msg_len):
        s = sum(1 for i,c in enumerate(chars) if c == KNOWN_MSG[(i+p)%msg_len])
        if s > best: best, phase = s, p
    return phase

# ---------------------------------------------------------------------------
def collect_confidences(filename):
    """Returns list of (snr_db, confidence, is_correct) tuples."""
    _, data = wavfile.read(filename)
    if data.ndim == 1: return []
    click_ch = data[:,0].astype(np.float64)
    tone_ch  = data[:,1].astype(np.float64)
    clicks   = find_clicks(click_ch)
    if len(clicks) < MAX_MSG_LEN+1: return []

    raw_mags = [get_symbol_magnitudes(tone_ch, int(c)) for c in clicks]
    L        = detect_length(raw_mags)

    # Decode and collect confidence + correctness
    chars  = []
    confs  = []
    for mags in raw_mags:
        char, conf = decode_and_confidence(mags)
        chars.append(char)
        confs.append(conf)

    phase = find_phase(chars, MSG_LEN)
    m = re.search(r'SNR([+-]\d+)dB', os.path.basename(filename))
    snr_db = int(m.group(1)) if m else None

    records = []
    for i, (char, conf) in enumerate(zip(chars, confs)):
        expected   = KNOWN_MSG[(i+phase) % MSG_LEN]
        is_correct = (char == expected)
        records.append((snr_db, conf, is_correct))
    return records

# ---------------------------------------------------------------------------
def main():
    files = [f for f in sys.argv[1:] if os.path.exists(f)]
    if not files:
        print("No files found")
        return

    all_records = []
    for f in files:
        print(f"Processing {os.path.basename(f)}...")
        all_records.extend(collect_confidences(f))

    if not all_records:
        return

    confs_correct   = [c for _, c, ok in all_records if ok]
    confs_incorrect = [c for _, c, ok in all_records if not ok]

    print(f"\nCorrect decodes:   n={len(confs_correct):4d}  "
          f"mean conf={np.mean(confs_correct):.3f}  "
          f"median={np.median(confs_correct):.3f}")
    print(f"Incorrect decodes: n={len(confs_incorrect):4d}  "
          f"mean conf={np.mean(confs_incorrect):.3f}  "
          f"median={np.median(confs_incorrect):.3f}")

    # Find threshold that maximises (true accepts - false accepts)
    # i.e. rejects bad decodes while keeping good ones
    best_thresh, best_score = 0.0, -1e9
    for thresh in np.arange(0.0, 1.0, 0.005):
        accepted_correct   = sum(1 for c in confs_correct   if c >= thresh)
        accepted_incorrect = sum(1 for c in confs_incorrect if c >= thresh)
        score = accepted_correct - accepted_incorrect
        if score > best_score:
            best_score, best_thresh = score, thresh

    print(f"\nOptimal threshold: {best_thresh:.3f}")
    accepted_correct   = sum(1 for c in confs_correct   if c >= best_thresh)
    accepted_incorrect = sum(1 for c in confs_incorrect if c >= best_thresh)
    rejected_correct   = len(confs_correct)   - accepted_correct
    rejected_incorrect = len(confs_incorrect) - accepted_incorrect
    print(f"  Correct accepted  : {accepted_correct}/{len(confs_correct)} "
          f"({100*accepted_correct/len(confs_correct):.1f}%)")
    print(f"  Incorrect accepted: {accepted_incorrect}/{len(confs_incorrect)} "
          f"({100*accepted_incorrect/len(confs_incorrect):.1f}%)")
    print(f"  Correct rejected  : {rejected_correct} (shown as '?')")
    print(f"  Incorrect rejected: {rejected_incorrect} (shown as '?')")

    # Report current in-use threshold for direct comparison
    cur_thresh = CURRENT_THRESHOLD
    cur_acc_correct   = sum(1 for c in confs_correct   if c >= cur_thresh)
    cur_acc_incorrect = sum(1 for c in confs_incorrect if c >= cur_thresh)
    print(f"\nCurrent threshold: {cur_thresh:.3f}")
    print(f"  Correct accepted  : {cur_acc_correct}/{len(confs_correct)} "
          f"({100*cur_acc_correct/len(confs_correct):.1f}%)")
    print(f"  Incorrect accepted: {cur_acc_incorrect}/{len(confs_incorrect)} "
          f"({100*cur_acc_incorrect/len(confs_incorrect):.1f}%)")

    # --- Plots ---
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))

    # Left: confidence distributions
    ax = axes[0]
    bins = np.linspace(0, 1, 50)
    ax.hist(confs_correct,   bins=bins, alpha=0.6, color='steelblue',
            label=f'Correct (n={len(confs_correct)})',   density=True)
    ax.hist(confs_incorrect, bins=bins, alpha=0.6, color='crimson',
            label=f'Incorrect (n={len(confs_incorrect)})', density=True)
    ax.axvline(best_thresh, color='black', linestyle='--', linewidth=1.5,
               label=f'Threshold={best_thresh:.3f}')
    ax.axvline(CURRENT_THRESHOLD, color='darkgreen', linestyle=':', linewidth=1.8,
               label=f'Current={CURRENT_THRESHOLD:.3f}')
    ax.set_xlabel("Confidence score")
    ax.set_ylabel("Density")
    ax.set_title("Confidence score: correct vs incorrect decodes")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)

    # Right: acceptance rate vs threshold
    ax2 = axes[1]
    thresholds = np.arange(0.0, 1.0, 0.005)
    tp_rate = [sum(1 for c in confs_correct   if c >= t)/len(confs_correct)   for t in thresholds]
    fp_rate = [sum(1 for c in confs_incorrect if c >= t)/len(confs_incorrect) for t in thresholds]
    ax2.plot(thresholds, tp_rate, color='steelblue', linewidth=2,
             label='Correct accepted')
    ax2.plot(thresholds, fp_rate, color='crimson',   linewidth=2,
             label='Incorrect accepted')
    ax2.axvline(best_thresh, color='black', linestyle='--', linewidth=1.5,
                label=f'Optimal={best_thresh:.3f}')
    ax2.axvline(CURRENT_THRESHOLD, color='darkgreen', linestyle=':', linewidth=1.8,
                label=f'Current={CURRENT_THRESHOLD:.3f}')
    ax2.set_xlabel("Confidence threshold")
    ax2.set_ylabel("Acceptance rate")
    ax2.set_title("Acceptance rate vs threshold")
    ax2.legend(fontsize=9)
    ax2.grid(True, alpha=0.3)

    # Right: per-SNR confidence scatter (correct vs incorrect)
    ax3 = axes[2]
    valid_snrs = sorted({snr for snr, _, _ in all_records if snr is not None})
    if valid_snrs:
        cmap = plt.cm.viridis
        snr_to_color = {
            snr: cmap(i / max(len(valid_snrs) - 1, 1))
            for i, snr in enumerate(valid_snrs)
        }
        for snr in valid_snrs:
            corr = [c for s, c, ok in all_records if s == snr and ok]
            inc  = [c for s, c, ok in all_records if s == snr and not ok]
            if corr:
                ax3.scatter(corr, np.ones(len(corr)), s=10, alpha=0.55,
                            color=snr_to_color[snr], label=f"{snr:+d} dB")
            if inc:
                ax3.scatter(inc, np.zeros(len(inc)), s=10, alpha=0.55,
                            color=snr_to_color[snr])

        ax3.axvline(best_thresh, color='black', linestyle='--', linewidth=1.5,
                    label=f'Optimal={best_thresh:.3f}')
        ax3.axvline(CURRENT_THRESHOLD, color='darkgreen', linestyle=':', linewidth=1.8,
                    label=f'Current={CURRENT_THRESHOLD:.3f}')
        ax3.set_ylim(-0.35, 1.35)
        ax3.set_yticks([0, 1])
        ax3.set_yticklabels(['Incorrect', 'Correct'])
        ax3.set_xlabel('Confidence score')
        ax3.set_title('Per-SNR confidence scatter')
        ax3.grid(True, alpha=0.3)
        handles, labels = ax3.get_legend_handles_labels()
        if handles:
            dedup = dict(zip(labels, handles))
            ax3.legend(dedup.values(), dedup.keys(), fontsize=8, ncol=2)

    plt.tight_layout()
    plt.savefig("ook48_confidence.png", dpi=150)
    print(f"\nPlot saved to: ook48_confidence.png")
    plt.show()

if __name__ == "__main__":
    main()