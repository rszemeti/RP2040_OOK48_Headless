#!/usr/bin/env python3
"""
StreamingMorseDecoder
=====================
Frame-by-frame Morse decoder designed for real-time operation.

Interface
---------
    dec = StreamingMorseDecoder(
        frame_rate=62,          # FFT frames/sec delivered by hardware
        wpm_min=5, wpm_max=35,  # search range for acquisition
        tone_bin=None,          # None = auto-detect, int = fixed bucket index
        n_fft_bins=256,         # total FFT bins (for auto-detect search range)
        sample_rate=8000,       # audio sample rate (for Hz↔bin conversion)
    )

    # Feed one FFT frame (array of bin magnitudes, or single float if tone_bin fixed)
    events = dec.feed(frame)    # returns list of Event objects

    # Steer to a specific frequency (e.g. from UI or second signal detection)
    dec.steer(frequency_hz=850.0)
    dec.steer(frequency_hz=None)  # revert to auto

Event types
-----------
    Event.CHAR     payload = decoded character string
    Event.WORD_SEP payload = ' '
    Event.LOCKED   payload = estimated WPM (float)
    Event.LOST     payload = None
    Event.STATUS   payload = status string (debug/info)

State machine
-------------
    ACQUIRE → LOCKED → TRACKING
                 ↑          |
                 └──────────┘  (re-acquire on lock loss)

    ACQUIRE:  accumulate runs into a ring buffer, run WPM estimator every
              REESTIMATE_INTERVAL frames once we have MIN_ACQUIRE_RUNS mark runs.
              Transition to LOCKED when estimator confidence exceeds LOCK_THRESHOLD.

    LOCKED/TRACKING: PLL tracks unit length frame by frame.
              Emit characters as symbols complete.
              Declare LOST if no mark run seen for LOST_TIMEOUT frames, or if
              PLL unit drifts outside sanity bounds.

Designed to be portable to C++:
    - No heap allocation in hot path (fixed-size ring buffers, pre-allocated lists)
    - All state is explicit in the class
    - No numpy in the core state machine (only in the FFT-to-envelope helpers)
    - feed() is O(1) amortised
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Morse tables
# ---------------------------------------------------------------------------

MORSE_REVERSE = {
    ".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E",
    "..-.": "F", "--.": "G", "....": "H", "..": "I", ".---": "J",
    "-.-": "K", ".-..": "L", "--": "M", "-.": "N", "---": "O",
    ".--.": "P", "--.-": "Q", ".-.": "R", "...": "S", "-": "T",
    "..-": "U", "...-": "V", ".--": "W", "-..-": "X", "-.--": "Y",
    "--..": "Z",
    "-----": "0", ".----": "1", "..---": "2", "...--": "3", "....-": "4",
    ".....": "5", "-....": "6", "--...": "7", "---..": "8", "----.": "9",
    ".-.-.-": ".", "--..--": ",", "..--..": "?", "-....-": "-",
    "-..-.": "/", ".-.-.": "+", "-...-": "=",
}


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

class EventKind(Enum):
    CHAR     = auto()
    WORD_SEP = auto()
    LOCKED   = auto()
    LOST     = auto()
    STATUS   = auto()


@dataclass
class Event:
    kind: EventKind
    payload: object = None

    def __str__(self):
        if self.kind == EventKind.CHAR:
            return str(self.payload)
        if self.kind == EventKind.WORD_SEP:
            return " "
        if self.kind == EventKind.LOCKED:
            return f"[LOCKED {self.payload:.1f} WPM]"
        if self.kind == EventKind.LOST:
            return "[LOST LOCK — reacquiring]"
        if self.kind == EventKind.STATUS:
            return f"[{self.payload}]"
        return ""


# ---------------------------------------------------------------------------
# Tunable constants  (candidates for optimiser later)
# ---------------------------------------------------------------------------

# Acquisition
MIN_ACQUIRE_MARK_RUNS  = 20    # minimum mark runs before attempting WPM estimate
REESTIMATE_INTERVAL    = 6     # re-run estimator every N frames during acquire
ACQUIRE_RING_SIZE      = 600   # ~10 seconds at 62fps — long enough for Schmitt to see both states
LOCK_THRESHOLD         = 0.65  # histogram alignment fraction to declare lock
                                # (fraction of mark runs landing on dit or dash)

# Schmitt trigger
SCHMITT_HYST_FRAC      = 0.12  # hysteresis as fraction of envelope dynamic range

# Morphological filter
MORPH_THRESH_FRAC      = 0.38  # merge runs shorter than this fraction of unit

# WPM estimator weights
SPACE_WORD_WEIGHT      = 0.15
SPACE_LETTER_WEIGHT    = 0.30
HIST_REWARD            = 0.40
HIST_TOL_FRAC          = 0.35  # ±fraction of unit for histogram match

# PLL
ALPHA_MARK             = 0.12
ALPHA_SPACE            = 0.06
PLL_LO_FRAC            = 0.60
PLL_HI_FRAC            = 1.55

# Word gap threshold in units
WORD_GAP_THR           = 5.5

# Lock loss
LOST_TIMEOUT_DITS      = 60    # declare lost after this many dits with no mark
LOST_PLL_DRIFT         = 0.45  # declare lost if unit drifts > this fraction from locked value

# Envelope smoothing — light IIR just to suppress single-frame spikes
# Keep alpha low: at 62fps a dit is ~9 frames, we don't want to smear edges
ENV_ALPHA              = 0.60  # higher = less smoothing lag (0.6 = ~1.7 frame TC)

# Auto-detect: search window around nominal tone (bins)
AUTO_DETECT_SPAN_FRAC  = 0.15  # search ± this fraction of total bins


# ---------------------------------------------------------------------------
# Core run-length helpers (no numpy — portable to C++)
# ---------------------------------------------------------------------------

def _dit_sec(wpm: float) -> float:
    return 1.2 / wpm


def _runs_from_binary(binary: list) -> List[Tuple[int, int]]:
    """Convert binary sequence to (state, length) run-length list."""
    if not binary:
        return []
    out = []
    cur = binary[0]
    n = 1
    for v in binary[1:]:
        if v == cur:
            n += 1
        else:
            out.append((cur, n))
            cur = v
            n = 1
    out.append((cur, n))
    return out


def _morph_filter(runs: List[Tuple[int, int]], min_run: int) -> List[Tuple[int, int]]:
    """Merge runs shorter than min_run into neighbours."""
    if not runs or min_run <= 1:
        return runs
    changed = True
    while changed:
        changed = False
        new: List[Tuple[int, int]] = []
        i = 0
        while i < len(runs):
            s, n = runs[i]
            if n < min_run and len(runs) > 1:
                if i == 0:
                    ns, nn = runs[i + 1]
                    new.append((ns, n + nn)); i += 2
                elif i == len(runs) - 1:
                    ps, pn = new[-1]
                    new[-1] = (ps, pn + n); i += 1
                else:
                    ps, pn = new[-1]
                    ns, nn = runs[i + 1]
                    if pn >= nn:
                        new[-1] = (ps, pn + n); i += 1
                    else:
                        new.append((ns, n + nn)); i += 2
                changed = True
            else:
                new.append((s, n)); i += 1
        # merge adjacent same-state runs
        merged: List[Tuple[int, int]] = []
        for s, n in new:
            if merged and merged[-1][0] == s:
                merged[-1] = (s, merged[-1][1] + n)
            else:
                merged.append((s, n))
        runs = merged
    return runs


def _estimate_wpm(runs: List[Tuple[int, int]],
                  frame_rate: int,
                  wpm_min: float, wpm_max: float, wpm_step: float = 0.5
                  ) -> Tuple[float, float]:
    """
    Returns (best_wpm, confidence) where confidence is the histogram
    alignment fraction [0..1] — fraction of mark runs landing near a
    dit or dash at the best WPM.
    """
    mark_runs = [n for s, n in runs if s == 1 and n >= 2]
    if not mark_runs:
        return wpm_min, 0.0

    total_runs = len(runs)
    best_wpm   = wpm_min
    best_score = -1e9
    best_conf  = 0.0

    wpm = wpm_min
    while wpm <= wpm_max + 1e-9:
        uf = max(1, round(_dit_sec(wpm) * frame_rate))

        # Count runs that fall below 0.5 units (invisible / sub-threshold)
        # A good WPM estimate should leave few runs below this cutoff.
        sub_thresh = sum(1 for s, n in runs if n / uf < 0.5)
        sub_frac   = sub_thresh / max(1, total_runs)

        # Mean-error score
        pen = 0.0; tw = 0.0
        for state, n in runs:
            units = n / uf
            if units < 0.5:
                continue
            weight = min(n, 10 * uf)
            if state == 1:
                err = min(abs(units - 1.0), abs(units - 3.0))
                w = 1.0
            else:
                if units >= 6.0:
                    err = abs(units - 7.0); w = SPACE_WORD_WEIGHT
                else:
                    err = min(abs(units - 1.0), abs(units - 3.0)); w = SPACE_LETTER_WEIGHT
            pen += weight * w * err; tw += weight * w

        if tw <= 1e-9:
            wpm += wpm_step
            continue

        tol = HIST_TOL_FRAC * uf
        dash_f = 3 * uf
        hits = sum(1 for n in mark_runs if abs(n - uf) <= tol or abs(n - dash_f) <= tol)
        conf = hits / len(mark_runs)

        # Penalise solutions that discard many runs as sub-threshold —
        # this catches false aliases (e.g. 6.5wpm matching 20wpm dashes)
        score = -(pen / tw) + HIST_REWARD * conf - 1.5 * sub_frac

        if score > best_score:
            best_score = score
            best_wpm   = wpm
            best_conf  = conf

        wpm += wpm_step

    return best_wpm, best_conf


# ---------------------------------------------------------------------------
# Main streaming decoder
# ---------------------------------------------------------------------------

class State(Enum):
    ACQUIRE  = auto()
    LOCKED   = auto()


class StreamingMorseDecoder:
    """
    Feed FFT frames one at a time. Receives list of bin magnitudes (or a
    single float if tone_bin is fixed). Returns list of Events.

    Parameters
    ----------
    frame_rate      : int   — FFT frames per second
    wpm_min/max     : float — WPM search range for acquisition
    tone_bin        : int|None — fixed FFT bin index, or None for auto-detect
    n_fft_bins      : int   — total number of FFT bins (for auto-detect)
    sample_rate     : int   — audio sample rate in Hz (for Hz↔bin conversion)
    nominal_tone_hz : float — nominal tone frequency for auto-detect centre
    """

    def __init__(
        self,
        frame_rate:      int   = 62,
        wpm_min:         float = 5.0,
        wpm_max:         float = 35.0,
        tone_bin:        Optional[int] = None,
        n_fft_bins:      int   = 256,
        sample_rate:     int   = 8000,
        nominal_tone_hz: float = 800.0,
    ):
        self.frame_rate      = int(frame_rate)
        self.wpm_min         = float(wpm_min)
        self.wpm_max         = float(wpm_max)
        self.n_fft_bins      = int(n_fft_bins)
        self.sample_rate     = int(sample_rate)
        self.nominal_tone_hz = float(nominal_tone_hz)

        # Fixed or auto tone bin
        self._fixed_tone_bin: Optional[int] = tone_bin
        self._active_tone_bin: Optional[int] = tone_bin  # resolved at first frame

        # Envelope state
        self._env_hist:  deque = deque(maxlen=ACQUIRE_RING_SIZE)
        self._peak_hold: float = 0.0   # slow-decay peak tracker
        self._peak_decay: float = 0.9995  # per-frame decay (~600 frames to halve)

        self._bin_hist:  deque = deque(maxlen=64)   # raw per-bin frames for auto-detect

        # Schmitt trigger state
        self._schmitt_state: int   = 0
        self._schmitt_lo:    float = 0.0
        self._schmitt_hi:    float = 0.0
        self._schmitt_valid: bool  = False
        self._schmitt_frame: int   = 0   # throttle recalculation

        # Run tracking (current in-progress run)
        self._cur_state:  int = 0
        self._cur_len:    int = 0

        # Completed run ring for acquisition and re-estimation
        self._run_buf: deque = deque(maxlen=500)

        # Binary frame ring (for Schmitt threshold recalculation)
        self._binary_hist: deque = deque(maxlen=ACQUIRE_RING_SIZE)

        # State machine
        self._state: State = State.ACQUIRE
        self._frames_since_acquire: int = 0

        # Lock state
        self._locked_wpm:    float = 0.0
        self._unit_est:      float = 0.0   # current PLL unit estimate (frames)
        self._unit_locked:   float = 0.0   # unit at lock time (for drift detection)
        self._unit_min:      float = 0.0
        self._unit_max:      float = 0.0

        # Symbol accumulation
        self._current_symbol: str = ""

        # Lock-loss watchdog: counts frames since last mark run
        self._frames_since_mark: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def feed(self, frame) -> List[Event]:
        """
        Feed one FFT frame. frame can be:
          - a list/array of bin magnitudes (auto-detect or fixed-bin mode)
          - a single float (pre-selected magnitude, fixed-bin mode)
        Returns a (possibly empty) list of Event objects.
        """
        # --- 1. Extract magnitude for our tone bin ---
        mag = self._extract_magnitude(frame)

        # --- 2. Store raw magnitude; update slow-decay peak-hold ---
        self._peak_hold = max(mag, self._peak_hold * self._peak_decay)
        self._env_hist.append(mag)

        # --- 3. Update Schmitt thresholds every 8 frames ---
        self._schmitt_frame += 1
        if self._schmitt_frame % 8 == 0:
            self._update_schmitt()

        # --- 4. Schmitt trigger → binary ---
        if not self._schmitt_valid:
            return []   # not enough history yet

        bit = self._schmitt_step(mag)
        self._binary_hist.append(bit)

        # --- 5. Run-length tracking ---
        events: List[Event] = []
        run_complete = self._update_run(bit)

        if run_complete is not None:
            run_state, run_len = run_complete
            self._run_buf.append(run_complete)
            self._frames_since_acquire += 1

            if self._state == State.ACQUIRE:
                events.extend(self._acquire_step())
            else:
                events.extend(self._track_step(run_state, run_len))

        # --- 6. Lock-loss watchdog ---
        if self._state == State.LOCKED:
            if bit == 1:
                self._frames_since_mark = 0
            else:
                self._frames_since_mark += 1
            lost_timeout = int(LOST_TIMEOUT_DITS * self._unit_est)
            if self._frames_since_mark > lost_timeout:
                events.extend(self._declare_lost("timeout"))
            elif not (self._unit_min <= self._unit_est <= self._unit_max):
                events.extend(self._declare_lost("PLL drift"))

        return events

    def steer(self, frequency_hz: Optional[float]) -> List[Event]:
        """
        Steer decoder to a specific frequency bin.
        Pass None to revert to auto-detect.
        Triggers re-acquisition.
        """
        events: List[Event] = []
        if frequency_hz is None:
            self._fixed_tone_bin = None
            self._active_tone_bin = None
            events.append(Event(EventKind.STATUS, "Frequency steer: AUTO — reacquiring"))
        else:
            bin_idx = self._hz_to_bin(frequency_hz)
            self._fixed_tone_bin = bin_idx
            self._active_tone_bin = bin_idx
            events.append(Event(EventKind.STATUS,
                                f"Frequency steer: {frequency_hz:.0f} Hz (bin {bin_idx}) — reacquiring"))
        self._reset_to_acquire()
        return events

    @property
    def state(self) -> State:
        return self._state

    @property
    def locked_wpm(self) -> float:
        return self._locked_wpm

    # ------------------------------------------------------------------
    # Internal: magnitude extraction
    # ------------------------------------------------------------------

    def _extract_magnitude(self, frame) -> float:
        """Pull magnitude for the active tone bin from a frame."""
        # Scalar — already the magnitude
        if isinstance(frame, (int, float)):
            return float(frame)

        arr = frame  # list or numpy array

        # Auto-detect: on first call, or if not yet resolved
        if self._active_tone_bin is None:
            self._bin_hist.append(arr)
            if len(self._bin_hist) >= 32:
                self._active_tone_bin = self._auto_detect_bin()
            # Until detected, use nominal bin
            return float(arr[self._hz_to_bin(self.nominal_tone_hz)])

        return float(arr[self._active_tone_bin])

    def _auto_detect_bin(self) -> int:
        """
        Find the tone bin with highest peak magnitude in the search window.
        Peak is robust even if the stream starts mid-mark (all ON frames),
        unlike SNR-contrast which requires OFF frames to calibrate noise floor.
        """
        centre = self._hz_to_bin(self.nominal_tone_hz)
        span   = max(1, int(self.n_fft_bins * AUTO_DETECT_SPAN_FRAC))
        lo     = max(1, centre - span)
        hi     = min(self.n_fft_bins - 1, centre + span)

        mat    = np.array([list(f)[lo:hi+1] for f in self._bin_hist], dtype=np.float32)
        peaks  = mat.max(axis=0)
        return lo + int(np.argmax(peaks))

    def _hz_to_bin(self, hz: float) -> int:
        bin_hz = self.sample_rate / (2.0 * self.n_fft_bins)
        return max(0, min(self.n_fft_bins - 1, round(hz / bin_hz)))

    # ------------------------------------------------------------------
    # Internal: Schmitt trigger
    # ------------------------------------------------------------------

    def _update_schmitt(self):
        """
        Schmitt thresholds using peak-hold (upper level) + noise percentile (lower level).

        Peak-hold decays slowly so it survives long spaces without collapsing
        to the noise floor, which was the failure mode of pure percentile tracking.
        """
        if len(self._env_hist) < 20:
            self._schmitt_valid = False
            return

        vals  = np.array(self._env_hist, dtype=np.float32)
        noise = float(np.percentile(vals, 20.0))  # noise floor from recent history
        peak  = self._peak_hold                    # slow-decay peak tracker

        # Require at least 6:1 ratio before trusting thresholds
        if noise <= 0 or peak / (noise + 1e-9) < 6.0:
            self._schmitt_valid = False
            return

        mid  = 0.5 * (noise + peak)
        hyst = SCHMITT_HYST_FRAC * (peak - noise)
        self._schmitt_lo    = mid - hyst
        self._schmitt_hi    = mid + hyst
        self._schmitt_valid = True

    def _schmitt_step(self, val: float) -> int:
        if self._schmitt_state == 0 and val >= self._schmitt_hi:
            self._schmitt_state = 1
        elif self._schmitt_state == 1 and val <= self._schmitt_lo:
            self._schmitt_state = 0
        return self._schmitt_state

    # ------------------------------------------------------------------
    # Internal: run-length tracking
    # ------------------------------------------------------------------

    def _update_run(self, bit: int) -> Optional[Tuple[int, int]]:
        """
        Update in-progress run. Returns completed (state, length) or None.
        """
        if bit == self._cur_state:
            self._cur_len += 1
            return None
        else:
            if self._cur_len > 0:
                completed = (self._cur_state, self._cur_len)
            else:
                completed = None
            self._cur_state = bit
            self._cur_len   = 1
            return completed

    # ------------------------------------------------------------------
    # Internal: acquisition
    # ------------------------------------------------------------------

    def _acquire_step(self) -> List[Event]:
        events: List[Event] = []

        mark_count = sum(1 for s, n in self._run_buf if s == 1)
        if mark_count < MIN_ACQUIRE_MARK_RUNS:
            return events

        if self._frames_since_acquire % REESTIMATE_INTERVAL != 0:
            return events

        runs = list(self._run_buf)
        # Coarse morph filter with mid-range WPM assumption
        coarse_uf = max(1, round(_dit_sec(0.5 * (self.wpm_min + self.wpm_max)) * self.frame_rate))
        min_run   = max(2, round(MORPH_THRESH_FRAC * coarse_uf))
        runs      = _morph_filter(runs, min_run)

        wpm, conf = _estimate_wpm(runs, self.frame_rate, self.wpm_min, self.wpm_max)

        if conf >= LOCK_THRESHOLD:
            events.extend(self._declare_locked(wpm))

        return events

    # ------------------------------------------------------------------
    # Internal: tracking (LOCKED state)
    # ------------------------------------------------------------------

    def _track_step(self, run_state: int, run_len: int) -> List[Event]:
        events: List[Event] = []

        uf = self._unit_est
        if uf <= 1e-6:
            return events

        units_f = run_len / uf
        units   = max(1, round(units_f))

        if run_state == 1:
            # Mark run → dit or dash
            is_dash = units >= 2
            self._current_symbol += "-" if is_dash else "."
            target = 3.0 if is_dash else 1.0
            # PLL update
            obs = run_len / target
            self._unit_est = (1.0 - ALPHA_MARK) * uf + ALPHA_MARK * obs
            self._frames_since_mark = 0

        else:
            # Space run → inter-element, letter gap, or word gap
            if units_f >= WORD_GAP_THR:
                if self._current_symbol:
                    events.extend(self._emit_symbol())
                events.append(Event(EventKind.WORD_SEP, " "))
                # Don't steer PLL from word gaps
            elif units >= 3:
                if self._current_symbol:
                    events.extend(self._emit_symbol())
                target = 3.0
                obs = run_len / target
                self._unit_est = (1.0 - ALPHA_SPACE) * uf + ALPHA_SPACE * obs
            else:
                target = 1.0
                obs = run_len / target
                self._unit_est = (1.0 - ALPHA_SPACE) * uf + ALPHA_SPACE * obs

        # Clamp PLL
        self._unit_est = max(self._unit_min, min(self._unit_max, self._unit_est))

        return events

    def _emit_symbol(self) -> List[Event]:
        sym = self._current_symbol
        self._current_symbol = ""
        ch = MORSE_REVERSE.get(sym, "?")
        return [Event(EventKind.CHAR, ch)]

    # ------------------------------------------------------------------
    # Internal: state transitions
    # ------------------------------------------------------------------

    def _declare_locked(self, wpm: float) -> List[Event]:
        self._state       = State.LOCKED
        self._locked_wpm  = wpm
        uf                = _dit_sec(wpm) * self.frame_rate
        self._unit_est    = uf
        self._unit_locked = uf
        self._unit_min    = PLL_LO_FRAC * uf
        self._unit_max    = PLL_HI_FRAC * uf
        self._current_symbol    = ""
        self._frames_since_mark = 0
        return [Event(EventKind.LOCKED, wpm)]

    def _declare_lost(self, reason: str = "") -> List[Event]:
        self._reset_to_acquire()
        return [Event(EventKind.LOST, None),
                Event(EventKind.STATUS, f"Lost lock ({reason}) — reacquiring")]

    def _reset_to_acquire(self):
        self._state               = State.ACQUIRE
        self._frames_since_acquire = 0
        self._run_buf.clear()
        self._current_symbol      = ""
        self._frames_since_mark   = 0
        self._cur_state           = 0
        self._cur_len             = 0
        # Keep envelope history — helps Schmitt retrain quickly


# ---------------------------------------------------------------------------
# WAV simulation driver  (replaces real serial/FFT hardware feed)
# ---------------------------------------------------------------------------

def stream_from_wav(
    wav_path:       str,
    frame_rate:     int   = 62,
    fft_size:       Optional[int] = None,
    tone_hz:        float = 800.0,
    tone_bin:       Optional[int] = None,
    wpm_min:        float = 5.0,
    wpm_max:        float = 35.0,
    verbose:        bool  = True,
) -> Tuple[List[Event], float]:
    """
    Simulate hardware FFT stream from a WAV file.
    Computes overlapping FFT frames at frame_rate fps and feeds them
    to StreamingMorseDecoder one at a time.

    fft_size is auto-chosen based on sample rate if not specified:
    targets ~20-35 Hz/bin resolution (same as Pico at 8kHz/256pt).

    Returns (all_events, decode_time_seconds).
    """
    import wave as wavemod
    with wavemod.open(wav_path, "rb") as wf:
        sr      = wf.getframerate()
        n_ch    = wf.getnchannels()
        raw     = wf.readframes(wf.getnframes())
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if n_ch > 1:
        data = data.reshape(-1, n_ch)[:, 0]

    # Auto-size FFT to get ~31 Hz/bin (matching Pico 8kHz/256pt)
    if fft_size is None:
        target_bin_hz = 31.25
        fft_size = 1
        while fft_size * target_bin_hz < sr:
            fft_size *= 2  # next power of 2

    hop    = max(1, sr // frame_rate)
    n_bins = fft_size // 2  # number of positive-frequency bins passed to decoder

    dec = StreamingMorseDecoder(
        frame_rate      = frame_rate,
        wpm_min         = wpm_min,
        wpm_max         = wpm_max,
        tone_bin        = tone_bin,
        n_fft_bins      = n_bins,
        sample_rate     = sr,
        nominal_tone_hz = tone_hz,
    )

    window = np.hanning(fft_size).astype(np.float32)
    all_events: List[Event] = []
    import time as _time
    t0 = _time.time()

    for i in range(0, len(data) - fft_size, hop):
        seg  = data[i:i + fft_size] * window
        spec = np.abs(np.fft.rfft(seg, n=fft_size))  # fft_size//2 + 1 bins
        events = dec.feed(spec[:n_bins])              # positive-freq bins only
        all_events.extend(events)
        if verbose:
            for ev in events:
                print(str(ev), end="", flush=True)

    # Flush any in-progress symbol at end of stream
    if dec._current_symbol:
        sym = dec._current_symbol
        ch  = MORSE_REVERSE.get(sym, "?")
        all_events.append(Event(EventKind.CHAR, ch))
        if verbose:
            print(ch, end="", flush=True)

    elapsed = _time.time() - t0
    if verbose:
        print()  # newline after decoded text

    return all_events, elapsed


# ---------------------------------------------------------------------------
# CLI demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse, sys

    parser = argparse.ArgumentParser(description="Stream-decode a Morse WAV file")
    parser.add_argument("wav", help="Input WAV file")
    parser.add_argument("--frame-rate", type=int,   default=62)
    parser.add_argument("--fft-size",   type=int,   default=None,
                        help="FFT size (default: auto-sized for ~31Hz/bin)")
    parser.add_argument("--tone-hz",    type=float, default=800.0)
    parser.add_argument("--wpm-min",    type=float, default=5.0)
    parser.add_argument("--wpm-max",    type=float, default=35.0)
    parser.add_argument("--tone-bin",   type=int,   default=None,
                        help="Fixed FFT bin (omit for auto-detect)")
    args = parser.parse_args()

    print(f"Streaming: {args.wav}")
    print(f"Frame rate: {args.frame_rate} fps  FFT: {args.fft_size}pt  Tone: {args.tone_hz:.0f}Hz")
    print("-" * 60)

    events, elapsed = stream_from_wav(
        wav_path   = args.wav,
        frame_rate = args.frame_rate,
        fft_size   = args.fft_size,
        tone_hz    = args.tone_hz,
        tone_bin   = args.tone_bin,
        wpm_min    = args.wpm_min,
        wpm_max    = args.wpm_max,
        verbose    = True,
    )

    chars   = [e.payload for e in events if e.kind == EventKind.CHAR]
    n_locks = sum(1 for e in events if e.kind == EventKind.LOCKED)
    n_lost  = sum(1 for e in events if e.kind == EventKind.LOST)

    print(f"\nDecoded {len(chars)} chars  |  {n_locks} lock(s)  {n_lost} loss(es)  |  {elapsed:.2f}s")
    