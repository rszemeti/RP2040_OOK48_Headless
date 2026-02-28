#!/usr/bin/env python3
"""
Bayesian optimisation of MorseDecoder hyperparameters.

Uses scikit-optimize (skopt) if available, falls back to random search.

Install deps:
    pip install scikit-optimize numpy

Usage:
    python morse_optimiser.py [options]

    --dir          Directory containing morse_tests/ WAV files (default: ./morse_tests)
    --iterations   Number of optimisation calls (default: 100)
    --objective    One of: mean, low_snr, jitter_only, harmonic (default: harmonic)
    --profiles     Comma-separated profiles to include, e.g. steady,var_low,var_high (default: all)
    --wpm-min      WPM search min (default: 5.0)
    --wpm-max      WPM search max (default: 20.0)
    --wpm-step     WPM search step (default: 0.25)
    --output       Where to write best params JSON (default: best_params.json)
    --jobs         Parallel workers for evaluation (default: 1; set to cpu count for speed)

Parameters being optimised (11 total):
    schmitt_hyst        Schmitt trigger hysteresis fraction of dynamic range [0.02, 0.30]
    morph_thresh        Morphological filter min-run as fraction of dit [0.15, 0.60]
    space_word_weight   WPM estimator weight for word-gap runs [0.05, 0.40]
    space_letter_weight WPM estimator weight for letter-gap runs [0.10, 0.60]
    hist_reward         Histogram peak bonus weight [0.0, 1.0]
    hist_tol            Histogram dit/dash match tolerance (fraction of unit) [0.15, 0.55]
    word_gap_thr        Word-gap threshold in units (vs letter-gap at 3) [4.0, 6.5]
    alpha_mark          PLL learning rate for mark runs [0.04, 0.25]
    alpha_space         PLL learning rate for space runs [0.02, 0.15]
    pll_lo              PLL lower unit bound as fraction of initial [0.45, 0.80]
    pll_hi              PLL upper unit bound as fraction of initial [1.20, 1.80]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
import wave
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Inline self-contained decoder — avoids import path issues when running
# locally. Parameterised version of morse_decoder_v2.
# ---------------------------------------------------------------------------

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
MORSE_REVERSE = {v: k for k, v in MORSE_MAP.items()}


def _dit_sec(wpm: float) -> float:
    return 1.2 / float(wpm)


def _runs(binary: np.ndarray) -> List[Tuple[int, int]]:
    if len(binary) == 0:
        return []
    vals = binary.astype(np.int8)
    out: List[Tuple[int, int]] = []
    cur = int(vals[0])
    n = 1
    for v in vals[1:]:
        vi = int(v)
        if vi == cur:
            n += 1
        else:
            out.append((cur, n))
            cur = vi
            n = 1
    out.append((cur, n))
    return out


def _morph_filter(run_list: List[Tuple[int, int]], min_run: int) -> List[Tuple[int, int]]:
    if not run_list or min_run <= 1:
        return run_list
    changed = True
    runs = list(run_list)
    while changed:
        changed = False
        new_runs: List[Tuple[int, int]] = []
        i = 0
        while i < len(runs):
            state, n = runs[i]
            if n < min_run and len(runs) > 1:
                if i == 0:
                    ns, nn = runs[i + 1]
                    new_runs.append((ns, n + nn))
                    i += 2
                elif i == len(runs) - 1:
                    ps, pn = new_runs[-1]
                    new_runs[-1] = (ps, pn + n)
                    i += 1
                else:
                    ps, pn = new_runs[-1]
                    ns, nn = runs[i + 1]
                    if pn >= nn:
                        new_runs[-1] = (ps, pn + n)
                        i += 1
                    else:
                        new_runs.append((ns, n + nn))
                        i += 2
                changed = True
            else:
                new_runs.append((state, n))
                i += 1
        merged: List[Tuple[int, int]] = []
        for s, n in new_runs:
            if merged and merged[-1][0] == s:
                merged[-1] = (s, merged[-1][1] + n)
            else:
                merged.append((s, n))
        runs = merged
    return runs


def _tone_envelope(samples: np.ndarray, sample_rate: int, tone_hz: float,
                   frame_rate: int, fft_len: int, tone_search_hz: float) -> np.ndarray:
    x = samples.astype(np.float64)
    if x.ndim != 1:
        x = x[:, 0]
    block = max(1, int(round(sample_rate / frame_rate)))
    n_blocks = len(x) // block
    if n_blocks <= 0:
        return np.zeros(1, dtype=np.float64)
    fft_len = max(256, fft_len)
    if fft_len % 2 != 0:
        fft_len += 1
    half = fft_len // 2
    freq_res = sample_rate / fft_len
    center_bin = int(round(tone_hz / freq_res))
    span_bins = max(1, int(round(tone_search_hz / freq_res)))
    search_lo = max(1, center_bin - span_bins)
    search_hi = min(fft_len // 2 - 1, center_bin + span_bins)
    window = np.hanning(fft_len)
    spec_rows = np.zeros((n_blocks, search_hi - search_lo + 1), dtype=np.float64)
    for i in range(n_blocks):
        c = i * block + (block // 2)
        start = c - half
        end = start + fft_len
        seg = np.zeros(fft_len, dtype=np.float64)
        src_lo = max(0, start)
        src_hi = min(len(x), end)
        dst_lo = src_lo - start
        dst_hi = dst_lo + (src_hi - src_lo)
        if src_hi > src_lo:
            seg[dst_lo:dst_hi] = x[src_lo:src_hi]
        spec = np.abs(np.fft.rfft(seg * window, n=fft_len))
        spec_rows[i, :] = spec[search_lo:search_hi + 1]
    col_peak = spec_rows.max(axis=0)
    col_median = np.median(spec_rows, axis=0)
    col_noise = np.percentile(spec_rows, 10.0, axis=0)
    snr_score = (col_peak - col_median) / (col_noise + 1e-9)
    best_idx = int(np.argmax(snr_score))
    env = spec_rows[:, best_idx]
    p95 = np.percentile(env, 95.0)
    if p95 > 0:
        env = env / p95
    return np.clip(env, 0.0, 3.0)


def _binarize_schmitt(env: np.ndarray, hyst_frac: float) -> Tuple[np.ndarray, float]:
    p20 = np.percentile(env, 20.0)
    p80 = np.percentile(env, 80.0)
    mid = 0.5 * (p20 + p80)
    half_hyst = hyst_frac * (p80 - p20)
    lo_thr = mid - half_hyst
    hi_thr = mid + half_hyst
    binary = np.zeros(len(env), dtype=np.int8)
    state = 1 if env[0] >= mid else 0
    for i, v in enumerate(env):
        if state == 0 and v >= hi_thr:
            state = 1
        elif state == 1 and v <= lo_thr:
            state = 0
        binary[i] = state
    return binary, float(mid)


def _estimate_wpm(run_list, frame_rate, wpm_min, wpm_max, wpm_step,
                  space_word_weight, space_letter_weight, hist_reward, hist_tol):
    mark_runs = [n for s, n in run_list if s == 1 and n >= 2]
    best_wpm = float(wpm_min)
    best_score = -1e9
    for wpm in np.arange(wpm_min, wpm_max + 1e-9, wpm_step):
        unit_frames = max(1, int(round(_dit_sec(float(wpm)) * frame_rate)))
        total_weight = 0.0
        penalty = 0.0
        for state, n in run_list:
            units = n / unit_frames
            if units < 0.5:
                continue
            weight = float(min(n, 10 * unit_frames))
            if state == 1:
                err = min(abs(units - 1.0), abs(units - 3.0))
                tw = 1.0
            else:
                if units >= 6.0:
                    err = abs(units - 7.0)
                    tw = space_word_weight
                else:
                    err = min(abs(units - 1.0), abs(units - 3.0))
                    tw = space_letter_weight
            penalty += weight * tw * err
            total_weight += weight * tw
        if total_weight <= 1e-9:
            continue
        mean_err = penalty / total_weight
        dit_tol_f = hist_tol * unit_frames
        dash_frames = 3 * unit_frames
        hist_score = sum(
            1 for n in mark_runs
            if abs(n - unit_frames) <= dit_tol_f or abs(n - dash_frames) <= dit_tol_f
        )
        hist_frac = hist_score / max(1, len(mark_runs))
        score = -mean_err + hist_reward * hist_frac
        if score > best_score:
            best_score = score
            best_wpm = float(wpm)
    return best_wpm, best_score


def _decode(binary, wpm, frame_rate, alpha_mark, alpha_space, pll_lo, pll_hi, word_gap_thr, morph_thresh):
    unit_frames = max(1, int(round(_dit_sec(wpm) * frame_rate)))
    run_list = _runs(binary)
    min_run = max(2, int(round(morph_thresh * unit_frames)))
    run_list = _morph_filter(run_list, min_run)
    symbols: List[str] = []
    current = ""
    unit_est = float(unit_frames)
    unit_min = pll_lo * unit_frames
    unit_max = pll_hi * unit_frames
    for state, n in run_list:
        if unit_est <= 1e-6:
            unit_est = float(unit_frames)
        units_f = n / unit_est
        units = max(1, int(round(units_f)))
        if state == 1:
            is_dash = units >= 2
            target = 3.0 if is_dash else 1.0
            current += "-" if is_dash else "."
            obs_unit = n / target
            unit_est = (1.0 - alpha_mark) * unit_est + alpha_mark * obs_unit
        else:
            update = True
            if units_f >= word_gap_thr:
                if current:
                    symbols.append(current)
                    current = ""
                symbols.append("/")
                update = False
                target = 7.0
            elif units >= 3:
                if current:
                    symbols.append(current)
                    current = ""
                target = 3.0
            else:
                target = 1.0
            if update:
                obs_unit = n / target
                unit_est = (1.0 - alpha_space) * unit_est + alpha_space * obs_unit
        unit_est = max(unit_min, min(unit_max, unit_est))
    if current:
        symbols.append(current)
    chars: List[str] = []
    for sym in symbols:
        if sym == "/":
            chars.append(" ")
        else:
            chars.append(MORSE_REVERSE.get(sym, "?"))
    return "".join(chars).strip()


def decode_file(path: str, params: dict, wpm_min: float, wpm_max: float, wpm_step: float) -> str:
    with wave.open(path, "rb") as wf:
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        sr = wf.getframerate()
        n = wf.getnframes()
        raw = wf.readframes(n)
    if sw != 2:
        return ""
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch)[:, 0]

    env = _tone_envelope(data, sr, 800.0, 200, 2048, 120.0)
    binary, _ = _binarize_schmitt(env, params["schmitt_hyst"])
    run_list = _runs(binary)

    # Coarse morph filter for WPM estimator
    coarse_unit = max(1, int(round(_dit_sec(0.5 * (wpm_min + wpm_max)) * 200)))
    min_run_coarse = max(2, int(round(params["morph_thresh"] * coarse_unit)))
    filtered = _morph_filter(run_list, min_run_coarse)

    est_wpm, _ = _estimate_wpm(
        filtered, 200, wpm_min, wpm_max, wpm_step,
        params["space_word_weight"], params["space_letter_weight"],
        params["hist_reward"], params["hist_tol"],
    )
    return _decode(
        binary, est_wpm, 200,
        params["alpha_mark"], params["alpha_space"],
        params["pll_lo"], params["pll_hi"],
        params["word_gap_thr"], params["morph_thresh"],
    )


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

KNOWN_TEXT = "CQ CQ DE OOK48 TEST K"


def score_text(decoded: str, known: str = KNOWN_TEXT) -> float:
    d = (decoded or "").strip().upper()
    k = (known or "").strip().upper()
    if not d or not k:
        return 0.0
    klen = len(k)
    blocks = [d[i:i + klen] for i in range(0, len(d), klen)]
    wm, wt = 0, 0
    for blk in blocks:
        n = len(blk)
        best = max(
            sum(1 for a, b in zip(blk, (k[p:] + k[:p])[:n]) if a == b)
            for p in range(klen)
        )
        wm += best
        wt += n
    return 100.0 * wm / wt if wt else 0.0


def parse_snr(name: str) -> float:
    m = re.search(r"SNR([+-]\d+)dB", name)
    return float(m.group(1)) if m else float("nan")


def parse_profile(path: str) -> str:
    base = os.path.basename(path)
    m = re.search(r"_(steady|var_low|var_high)_SNR", base)
    if m:
        return m.group(1)
    return os.path.basename(os.path.dirname(path))


def compute_objective(scores_by_profile: Dict[str, List[float]],
                      snrs_by_profile: Dict[str, List[float]],
                      objective: str,
                      target_profiles: List[str]) -> float:
    """Compute the objective score from per-file results."""
    if objective == "mean":
        all_scores = []
        for p in target_profiles:
            all_scores.extend(scores_by_profile.get(p, []))
        return float(np.mean(all_scores)) if all_scores else 0.0

    elif objective == "low_snr":
        # Weight files by exp(-snr/6) so low-SNR files dominate
        weighted, total_w = 0.0, 0.0
        for p in target_profiles:
            for sc, snr in zip(scores_by_profile.get(p, []), snrs_by_profile.get(p, [])):
                if np.isfinite(snr):
                    w = np.exp(-snr / 6.0)
                    weighted += w * sc
                    total_w += w
        return weighted / total_w if total_w > 0 else 0.0

    elif objective == "jitter_only":
        all_scores = []
        for p in ["var_low", "var_high"]:
            if p in target_profiles:
                all_scores.extend(scores_by_profile.get(p, []))
        return float(np.mean(all_scores)) if all_scores else 0.0

    elif objective == "harmonic":
        profile_means = []
        for p in target_profiles:
            sc = scores_by_profile.get(p, [])
            if sc:
                profile_means.append(float(np.mean(sc)))
        if not profile_means:
            return 0.0
        return len(profile_means) / sum(1.0 / max(s, 0.01) for s in profile_means)

    return 0.0


# ---------------------------------------------------------------------------
# Main evaluation function
# ---------------------------------------------------------------------------

def evaluate(params: dict, files: List[str], target_profiles: List[str],
             objective: str, wpm_min: float, wpm_max: float, wpm_step: float) -> float:
    scores_by_profile: Dict[str, List[float]] = {}
    snrs_by_profile: Dict[str, List[float]] = {}

    for path in files:
        prof = parse_profile(path)
        if prof not in target_profiles:
            continue
        snr = parse_snr(os.path.basename(path))
        try:
            decoded = decode_file(path, params, wpm_min, wpm_max, wpm_step)
            sc = score_text(decoded)
        except Exception:
            sc = 0.0
        scores_by_profile.setdefault(prof, []).append(sc)
        snrs_by_profile.setdefault(prof, []).append(snr)

    return compute_objective(scores_by_profile, snrs_by_profile, objective, target_profiles)


# ---------------------------------------------------------------------------
# Parameter space definition
# ---------------------------------------------------------------------------

PARAM_NAMES = [
    "schmitt_hyst",
    "morph_thresh",
    "space_word_weight",
    "space_letter_weight",
    "hist_reward",
    "hist_tol",
    "word_gap_thr",
    "alpha_mark",
    "alpha_space",
    "pll_lo",
    "pll_hi",
]

PARAM_BOUNDS = [
    (0.02, 0.30),   # schmitt_hyst
    (0.15, 0.60),   # morph_thresh
    (0.05, 0.40),   # space_word_weight
    (0.10, 0.60),   # space_letter_weight
    (0.00, 1.00),   # hist_reward
    (0.15, 0.55),   # hist_tol
    (4.00, 6.50),   # word_gap_thr
    (0.04, 0.25),   # alpha_mark
    (0.02, 0.15),   # alpha_space
    (0.45, 0.80),   # pll_lo
    (1.20, 1.80),   # pll_hi
]

# v2 baseline values (starting point for warm start)
V2_DEFAULTS = [0.12, 0.38, 0.15, 0.30, 0.40, 0.35, 5.50, 0.12, 0.06, 0.60, 1.55]


def vec_to_params(vec: List[float]) -> dict:
    return {name: val for name, val in zip(PARAM_NAMES, vec)}


# ---------------------------------------------------------------------------
# Optimiser
# ---------------------------------------------------------------------------

def run_optimisation(
    files: List[str],
    target_profiles: List[str],
    objective: str,
    n_calls: int,
    wpm_min: float,
    wpm_max: float,
    wpm_step: float,
    output_path: str,
    jobs: int = 1,
) -> dict:
    try:
        from skopt import gp_minimize
        from skopt.space import Real
        USE_BAYES = True
        print("Using Bayesian optimisation (scikit-optimize)")
    except ImportError:
        USE_BAYES = False
        print("scikit-optimize not found — falling back to random search.")
        print("Install it with:  pip install scikit-optimize")

    best_score = -1e9
    best_params = vec_to_params(V2_DEFAULTS)
    iteration = [0]
    t0 = time.time()

    def objective_fn(vec):
        iteration[0] += 1
        params = vec_to_params(vec)
        score = evaluate(params, files, target_profiles, objective, wpm_min, wpm_max, wpm_step)
        elapsed = time.time() - t0
        rate = iteration[0] / elapsed if elapsed > 0 else 0
        eta = (n_calls - iteration[0]) / rate if rate > 0 else 0
        nonlocal best_score, best_params
        marker = ""
        if score > best_score:
            best_score = score
            best_params = params
            marker = "  *** NEW BEST ***"
            # Save immediately so you have results even if you Ctrl+C
            with open(output_path, "w") as f:
                json.dump({"best_score": best_score, "params": best_params}, f, indent=2)
        print(f"  [{iteration[0]:>4}/{n_calls}]  score={score:6.2f}  best={best_score:6.2f}"
              f"  {elapsed:.0f}s elapsed  ETA {eta:.0f}s{marker}")
        return -score  # skopt minimises

    if USE_BAYES:
        from skopt import gp_minimize
        from skopt.space import Real
        space = [Real(lo, hi, name=name) for name, (lo, hi) in zip(PARAM_NAMES, PARAM_BOUNDS)]
        # Warm-start with v2 defaults as first point
        x0 = [V2_DEFAULTS]
        y0 = [-evaluate(best_params, files, target_profiles, objective, wpm_min, wpm_max, wpm_step)]
        print(f"v2 baseline score: {-y0[0]:.2f}")
        result = gp_minimize(
            objective_fn,
            space,
            n_calls=n_calls,
            x0=x0,
            y0=y0,
            random_state=42,
            n_jobs=1,  # GP itself is single-threaded; parallelism is in evaluation
            verbose=False,
        )
        best_vec = result.x
        best_params = vec_to_params(best_vec)
        best_score = -result.fun
    else:
        # Random search fallback
        rng = np.random.default_rng(42)
        # Always evaluate v2 baseline first
        base_score = evaluate(best_params, files, target_profiles, objective, wpm_min, wpm_max, wpm_step)
        print(f"v2 baseline score: {base_score:.2f}")
        best_score = base_score
        with open(output_path, "w") as f:
            json.dump({"best_score": best_score, "params": best_params}, f, indent=2)

        for _ in range(n_calls):
            vec = [rng.uniform(lo, hi) for lo, hi in PARAM_BOUNDS]
            objective_fn(vec)

    print(f"\nOptimisation complete.")
    print(f"Best score: {best_score:.2f}")
    print(f"Best params: {json.dumps(best_params, indent=2)}")
    with open(output_path, "w") as f:
        json.dump({"best_score": best_score, "params": best_params}, f, indent=2)
    print(f"Saved to {output_path}")
    return best_params


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Bayesian hyperparameter optimiser for MorseDecoder")
    parser.add_argument("--dir", default="./morse_tests", help="Directory containing WAV test files")
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--objective", default="harmonic",
                        choices=["mean", "low_snr", "jitter_only", "harmonic"])
    parser.add_argument("--profiles", default="steady,var_low,var_high",
                        help="Comma-separated profiles to target")
    parser.add_argument("--wpm-min", type=float, default=5.0)
    parser.add_argument("--wpm-max", type=float, default=20.0)
    parser.add_argument("--wpm-step", type=float, default=0.25)
    parser.add_argument("--output", default="best_params.json")
    parser.add_argument("--jobs", type=int, default=1, help="Parallel workers (not yet used)")
    args = parser.parse_args()

    target_profiles = [p.strip() for p in args.profiles.split(",")]
    print(f"Target profiles: {target_profiles}")
    print(f"Objective:       {args.objective}")
    print(f"Iterations:      {args.iterations}")
    print(f"WAV directory:   {args.dir}")
    print()

    pattern = os.path.join(args.dir, "**", "*.wav")
    files = sorted(glob.glob(pattern, recursive=True))
    if not files:
        # try flat directory
        pattern = os.path.join(args.dir, "*.wav")
        files = sorted(glob.glob(pattern))
    if not files:
        print(f"No WAV files found under {args.dir}")
        sys.exit(1)
    print(f"Found {len(files)} WAV files\n")

    run_optimisation(
        files=files,
        target_profiles=target_profiles,
        objective=args.objective,
        n_calls=args.iterations,
        wpm_min=args.wpm_min,
        wpm_max=args.wpm_max,
        wpm_step=args.wpm_step,
        output_path=args.output,
        jobs=args.jobs,
    )


if __name__ == "__main__":
    main()