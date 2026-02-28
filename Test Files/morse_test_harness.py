#!/usr/bin/env python3
"""
Streaming Morse decoder test harness.

Runs StreamingMorseDecoder against all WAV files found under --dir,
and produces a results table broken down by WPM, profile, and SNR.

Usage:
    python morse_stream_harness.py
    python morse_stream_harness.py --dir morse_tests_fast
    python morse_stream_harness.py --dir morse_tests_fast --score-text "CQ CQ DE OOK48 TEST K"
    python morse_stream_harness.py --dir morse_tests_fast --wpm 8 12   # only these WPMs
    python morse_stream_harness.py --dir morse_tests_fast --no-summary  # skip summary tables

Columns:
    WPM     true WPM from filename
    Profile steady / var_low / var_high
    SNR     signal-to-noise ratio in dB
    Lock    WPM at which decoder locked, or NO
    LkFr    frame number at which lock was acquired
    #Lost   number of lock-loss events
    Score%  cyclic character match score against known text
    Decoded first 50 chars of decoded output
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from morse_stream_decoder import stream_from_wav, EventKind, Event


# ---------------------------------------------------------------------------
# Known text for scoring
# ---------------------------------------------------------------------------

DEFAULT_SCORE_TEXT = "CQ CQ DE OOK48 TEST K"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Result:
    path:       str
    true_wpm:   int
    profile:    str
    snr_db:     float
    lock_wpm:   Optional[float]   # None = never locked
    lock_frame: Optional[int]     # frame at which lock occurred
    n_lost:     int
    score_pct:  float
    decoded:    str


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_against_known(decoded: str, known: str) -> float:
    """
    Cyclic phase-robust scoring.
    Splits decoded into blocks of len(known), finds best cyclic phase for each,
    returns weighted match percentage.
    """
    d = (decoded or "").strip().upper()
    k = (known  or "").strip().upper()
    if not d or not k:
        return 0.0
    klen = len(k)
    blocks = [d[i:i + klen] for i in range(0, len(d), klen)]
    wm = wt = 0
    for blk in blocks:
        n = len(blk)
        best = max(
            sum(1 for a, b in zip(blk, (k[p:] + k[:p])[:n]) if a == b)
            for p in range(klen)
        )
        wm += best
        wt += n
    return 100.0 * wm / wt if wt else 0.0


# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------

def parse_true_wpm(name: str) -> int:
    m = re.search(r"_(\d+)wpm_", name)
    return int(m.group(1)) if m else 0


def parse_snr(name: str) -> float:
    m = re.search(r"SNR([+-]?\d+)dB", name)
    return float(m.group(1)) if m else float("nan")


def parse_profile(path: str) -> str:
    m = re.search(r"_(steady|var_low|var_high)_SNR", os.path.basename(path))
    if m:
        return m.group(1)
    return os.path.basename(os.path.dirname(path))


# ---------------------------------------------------------------------------
# Run one file
# ---------------------------------------------------------------------------

def run_file(path: str, score_text: str, wpm_min: float, wpm_max: float) -> Result:
    true_wpm = parse_true_wpm(os.path.basename(path))
    profile  = parse_profile(path)
    snr_db   = parse_snr(os.path.basename(path))

    events, _ = stream_from_wav(
        path,
        wpm_min = wpm_min,
        wpm_max = wpm_max,
        verbose = False,
    )

    # Extract decoded text (chars + word separators)
    decoded = "".join(
        " "         if e.kind == EventKind.WORD_SEP else
        e.payload   if e.kind == EventKind.CHAR     else
        ""
        for e in events
    ).strip()

    # Lock info — first LOCKED event
    lock_ev = next((e for e in events if e.kind == EventKind.LOCKED), None)
    lock_wpm   = lock_ev.payload if lock_ev else None

    # Frame at lock: count non-status events up to first LOCKED
    lock_frame = None
    if lock_ev:
        # Events don't carry frame numbers directly — use STATUS events
        # emitted by stream_from_wav which include frame info... they don't.
        # Best proxy: count CHAR+WORD_SEP events before first LOCKED isn't meaningful.
        # Leave as None; the stream simulator doesn't expose frame count easily.
        # TODO: add frame counter to Event in a future pass.
        lock_frame = None

    n_lost  = sum(1 for e in events if e.kind == EventKind.LOST)
    score   = score_against_known(decoded, score_text)

    return Result(
        path       = path,
        true_wpm   = true_wpm,
        profile    = profile,
        snr_db     = snr_db,
        lock_wpm   = lock_wpm,
        lock_frame = lock_frame,
        n_lost     = n_lost,
        score_pct  = score,
        decoded    = decoded,
    )


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

PROFILE_ORDER = ["steady", "var_low", "var_high"]
PROFILE_WIDTH = 8


def _fmt_lock(r: Result) -> str:
    if r.lock_wpm is None:
        return "  NO  "
    return f"{r.lock_wpm:5.1f} "


def _sort_key(r: Result):
    prof_idx = PROFILE_ORDER.index(r.profile) if r.profile in PROFILE_ORDER else 99
    return (r.true_wpm, prof_idx, r.snr_db)


# ---------------------------------------------------------------------------
# Print tables
# ---------------------------------------------------------------------------

def print_full_table(results: List[Result], score_text: str) -> None:
    print(f"\n{'WPM':>4}  {'Profile':>{PROFILE_WIDTH}}  {'SNR':>5}  {'Lock':>6}  {'Lost':>4}  {'Score%':>7}  Decoded")
    print("-" * 105)
    for r in sorted(results, key=_sort_key, reverse=False):
        lock_str  = _fmt_lock(r)
        score_str = f"{r.score_pct:6.1f}" if np.isfinite(r.score_pct) else "   n/a"
        preview   = r.decoded[:50]
        print(f"{r.true_wpm:>4}  {r.profile:>{PROFILE_WIDTH}}  {r.snr_db:>5.0f}  {lock_str}  {r.n_lost:>4}  {score_str}  {preview}")


def print_snr_pivot(results: List[Result], score_text: str) -> None:
    """Score% pivot table: rows=SNR, cols=WPM×Profile."""
    snrs     = sorted(set(r.snr_db for r in results), reverse=True)
    wpms     = sorted(set(r.true_wpm for r in results))
    profiles = [p for p in PROFILE_ORDER if any(r.profile == p for r in results)]

    # Build column headers
    cols = [(w, p) for w in wpms for p in profiles]
    col_w = 8

    # Header
    hdr1 = " " * 7
    hdr2 = " " * 7
    for w, p in cols:
        lbl = f"{w}w/{p[:3]}"
        hdr1 += f" {lbl:>{col_w}}"
    print("\nScore% pivot  (rows=SNR dB, cols=WPM/Profile)")
    print(hdr1)
    print("-" * (7 + len(cols) * (col_w + 1)))

    lookup = {(r.true_wpm, r.profile, r.snr_db): r for r in results}
    for snr in snrs:
        row = f"{snr:>6.0f} "
        for w, p in cols:
            r = lookup.get((w, p, snr))
            if r is None:
                row += f" {'---':>{col_w}}"
            elif not np.isfinite(r.score_pct):
                row += f" {'n/a':>{col_w}}"
            else:
                row += f" {r.score_pct:>{col_w}.1f}"
        print(row)


def print_summary(results: List[Result]) -> None:
    """Summary by WPM, by Profile, by WPM×Profile."""
    wpms     = sorted(set(r.true_wpm for r in results))
    profiles = [p for p in PROFILE_ORDER if any(r.profile == p for r in results)]

    def stats(sub: List[Result]) -> str:
        locked     = sum(1 for r in sub if r.lock_wpm is not None)
        scores     = [r.score_pct for r in sub if np.isfinite(r.score_pct)]
        mean_sc    = np.mean(scores) if scores else float("nan")
        lock_wpms  = [r.lock_wpm for r in sub if r.lock_wpm is not None]
        mean_lk    = np.mean(lock_wpms)  if lock_wpms else float("nan")
        wpm_errs   = [abs(r.lock_wpm - r.true_wpm) for r in sub if r.lock_wpm is not None]
        mean_err   = np.mean(wpm_errs)  if wpm_errs else float("nan")
        mean_lost  = np.mean([r.n_lost for r in sub])
        return (f"locked={locked:2d}/{len(sub)}"
                f"  lockWPM={mean_lk:5.1f}  |err|={mean_err:.2f}"
                f"  score={mean_sc:5.1f}%  lost/file={mean_lost:.2f}")

    print("\nBy WPM:")
    for w in wpms:
        sub = [r for r in results if r.true_wpm == w]
        print(f"  {w:>3} wpm: {stats(sub)}")

    print("\nBy Profile:")
    for p in profiles:
        sub = [r for r in results if r.profile == p]
        print(f"  {p:>{PROFILE_WIDTH}}: {stats(sub)}")

    print("\nBy WPM × Profile:")
    for w in wpms:
        for p in profiles:
            sub = [r for r in results if r.true_wpm == w and r.profile == p]
            if sub:
                print(f"  {w:>3} wpm  {p:>{PROFILE_WIDTH}}: {stats(sub)}")

    print("\nOverall:")
    print(f"  {stats(results)}")

    # SNR breakdown — where does it fall apart?
    print("\nBy SNR (mean score across all WPM/Profile):")
    snrs = sorted(set(r.snr_db for r in results), reverse=True)
    for snr in snrs:
        sub    = [r for r in results if r.snr_db == snr]
        scores = [r.score_pct for r in sub if np.isfinite(r.score_pct)]
        locks  = sum(1 for r in sub if r.lock_wpm is not None)
        mean_s = np.mean(scores) if scores else 0.0
        bar    = "█" * int(mean_s / 5)
        print(f"  {snr:>5.0f} dB  score={mean_s:5.1f}%  locked={locks:2d}/{len(sub)}  {bar}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    default_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "morse_tests_fast")

    parser = argparse.ArgumentParser(
        description="Test harness for StreamingMorseDecoder",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--dir",        default=default_dir,        help="Root directory for WAV files")
    parser.add_argument("--pattern",    default="**/*.wav",         help="Glob pattern for WAV files")
    parser.add_argument("--score-text", default=DEFAULT_SCORE_TEXT, help="Known text for scoring")
    parser.add_argument("--wpm",        type=int, nargs="+",        help="Filter to specific WPM values, e.g. --wpm 8 12")
    parser.add_argument("--profile",    nargs="+",                  help="Filter to profiles, e.g. --profile steady var_low")
    parser.add_argument("--snr-min",    type=float, default=-99,    help="Minimum SNR to include")
    parser.add_argument("--snr-max",    type=float, default=+99,    help="Maximum SNR to include")
    parser.add_argument("--wpm-min",    type=float, default=5.0,    help="Decoder WPM search min")
    parser.add_argument("--wpm-max",    type=float, default=35.0,   help="Decoder WPM search max")
    parser.add_argument("--no-summary", action="store_true",        help="Skip summary tables, just print full results")
    parser.add_argument("--no-full",    action="store_true",        help="Skip full per-file table")
    parser.add_argument("--pivot",      action="store_true",        help="Print SNR×WPM/Profile pivot table")
    args = parser.parse_args()

    # Find files
    pattern = os.path.join(args.dir, args.pattern)
    files   = sorted(glob.glob(pattern, recursive=True))
    if not files:
        print(f"No WAV files found: {pattern}")
        sys.exit(1)

    # Apply filters
    if args.wpm:
        files = [f for f in files if parse_true_wpm(os.path.basename(f)) in args.wpm]
    if args.profile:
        files = [f for f in files if parse_profile(f) in args.profile]
    files = [f for f in files if args.snr_min <= parse_snr(os.path.basename(f)) <= args.snr_max]

    print(f"StreamingMorseDecoder test harness")
    print(f"  Directory  : {args.dir}")
    print(f"  Files      : {len(files)}")
    print(f"  Score text : {args.score_text}")
    print(f"  WPM range  : {args.wpm_min}–{args.wpm_max}")
    if args.wpm:
        print(f"  WPM filter : {args.wpm}")

    # Run
    results: List[Result] = []
    for i, path in enumerate(files):
        name = os.path.basename(path)
        print(f"\r  Decoding [{i+1:3d}/{len(files)}] {name:<60}", end="", flush=True)
        results.append(run_file(path, args.score_text, args.wpm_min, args.wpm_max))
    print()  # newline after progress

    # Output
    if not args.no_full:
        print_full_table(results, args.score_text)

    if args.pivot:
        print_snr_pivot(results, args.score_text)

    if not args.no_summary:
        print_summary(results)


if __name__ == "__main__":
    main()