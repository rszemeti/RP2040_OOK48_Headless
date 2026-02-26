#!/usr/bin/env python3
# ook48_accumulator.py  v2.0
"""
OOK48Accumulator — soft magnitude accumulator for the OOK48 GUI.

State machine:
  SEARCHING  — not enough data for length detection yet
  DETECTING  — length found but not confirmed (showing provisional decode)
  LOCKED     — length confirmed, accumulation building
  CONFIRMED  — mean confidence crossed COPY_THRESHOLD, copy written to log

Collapse detection:
  After LOCKED, tracks a rolling window of per-cycle mean confidence.
  If COLLAPSE_WINDOW consecutive cycles all fall below COLLAPSE_THRESHOLD
  the accumulator resets to SEARCHING and fires on_reset(reason).
  This handles: new station, QRM, operator changed message.

Phase:
  The phase (which position = message char 0) is unknown without a reference.
  The display shows the message in whatever rotation was locked in.
  set_phase(n) lets the operator or a future auto-phase function pin it.
"""

import numpy as np
import time
from enum import Enum, auto

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

CONFIDENCE_THRESHOLD = 0.180   # per-char: below this → UNK
COPY_THRESHOLD       = 0.65    # mean confidence to write confirmed copy
COLLAPSE_THRESHOLD   = 0.25    # mean confidence below this = bad cycle
COLLAPSE_WINDOW      = 3       # consecutive bad cycles triggers reset
UNK_CHAR             = '~'
MIN_MSG_LEN          = 3
MAX_MSG_LEN          = 30


class AccState(Enum):
    SEARCHING  = auto()
    DETECTING  = auto()
    LOCKED     = auto()
    CONFIRMED  = auto()


# ---------------------------------------------------------------------------
def _decode_mags(mags, use_confidence=True):
    ranked     = np.sort(mags)[::-1]
    rng        = ranked[0] - ranked[7]
    gap        = ranked[3] - ranked[4]
    confidence = gap / rng if rng > 0 else 0.0
    if use_confidence and confidence < CONFIDENCE_THRESHOLD:
        return UNK_CHAR, confidence
    bits, temp = [0]*8, mags.copy()
    for _ in range(4):
        idx = int(np.argmax(temp)); bits[idx] = 1; temp[idx] = 0
    byte_val = 0
    for b in bits: byte_val = (byte_val << 1) | b
    code = DECODE_4FROM8[byte_val] if byte_val < len(DECODE_4FROM8) else 0
    return (chr(code) if code > 0 else '?'), confidence


def _detect_length(history, min_l=MIN_MSG_LEN, max_l=MAX_MSG_LEN):
    n = len(history)
    if n < min_l * 2:
        return None
    scores = {}
    for L in range(min_l, min(max_l + 1, n // 2 + 1)):
        corr, count = 0.0, 0
        for i in range(n - L):
            a, b = history[i], history[i + L]
            na, nb = np.linalg.norm(a), np.linalg.norm(b)
            if na > 0 and nb > 0:
                corr += np.dot(a, b) / (na * nb); count += 1
        scores[L] = corr / count if count > 0 else 0.0
    mean_s = np.mean(list(scores.values()))
    norm_s = {L: s - mean_s for L, s in scores.items()}
    best_L = max(norm_s, key=norm_s.get)
    for div in range(2, best_L):
        if best_L % div == 0:
            cand = best_L // div
            if cand >= min_l and norm_s.get(cand, 0) >= 0.5 * norm_s[best_L]:
                best_L = cand; break
    return best_L


# ---------------------------------------------------------------------------
class CharacterState:
    __slots__ = ('accumulated', 'count', 'char', 'confidence', 'flipped')

    def __init__(self):
        self.accumulated = np.zeros(8)
        self.count       = 0
        self.char        = '?'
        self.confidence  = 0.0
        self.flipped     = False

    def update(self, mags):
        prev             = self.char
        self.accumulated += mags
        self.count       += 1
        avg              = self.accumulated / self.count
        self.char, self.confidence = _decode_mags(avg)
        self.flipped     = (self.char != prev and prev not in ('?', UNK_CHAR))

    def reset(self):
        self.accumulated[:] = 0
        self.count = 0; self.char = '?'; self.confidence = 0.0; self.flipped = False


# ---------------------------------------------------------------------------
class OOK48Accumulator:
    """
    Rolling soft accumulator with state machine, phase tracking,
    and confidence-collapse detection.

    Callbacks:
      on_update(state)  — every push()
      on_copy(entry)    — confirmed copy
      on_reset(reason)  — self-reset after collapse
    """

    def __init__(self, on_update=None, on_copy=None, on_reset=None, msg_len=None):
        self.on_update = on_update
        self.on_copy   = on_copy
        self.on_reset  = on_reset
        self.msg_len   = msg_len

        self._do_reset(initial=True)

    def _do_reset(self, initial=False):
        self._state             = AccState.SEARCHING
        self._history           = []
        self._chars             = []
        self._seq               = 0
        self._copies            = []
        self._detect_attempts   = 0
        self._length_locked     = (self.msg_len is not None)
        self._phase             = None
        self._cycle_confidences = []
        self._low_conf_streak   = 0
        if self.msg_len:
            self._chars = [CharacterState() for _ in range(self.msg_len)]
            self._state = AccState.DETECTING

    def reset(self, reason='manual'):
        self._do_reset()
        if not reason == 'initial' and self.on_reset:
            self.on_reset(reason)

    def set_phase(self, phase):
        """Pin phase externally (0-based index of message start character)."""
        if self.msg_len and 0 <= phase < self.msg_len:
            self._phase = phase

    # ------------------------------------------------------------------
    def push(self, mags):
        mags = np.asarray(mags, dtype=np.float64)
        self._history.append(mags)
        self._seq += 1

        if not self._length_locked:
            self._try_detect_length()

        if self.msg_len is None:
            if self.on_update:
                self.on_update(self.get_display_state())
            return

        if len(self._chars) != self.msg_len:
            self._chars = [CharacterState() for _ in range(self.msg_len)]

        pos = (self._seq - 1) % self.msg_len
        self._chars[pos].update(mags)

        if self._seq % self.msg_len == 0:
            self._end_of_cycle()
            # _end_of_cycle may have reset us — check before firing update
            if self.msg_len is None:
                if self.on_update:
                    self.on_update(self.get_display_state())
                return

        if self.on_update:
            self.on_update(self.get_display_state())

    # ------------------------------------------------------------------
    def _try_detect_length(self):
        n = len(self._history)
        if n < MAX_MSG_LEN * 2 or n % MAX_MSG_LEN != 0:
            return
        L = _detect_length(self._history)
        if L is None:
            return
        self._detect_attempts += 1
        if L == self.msg_len:
            if self._detect_attempts >= 2:
                self._length_locked = True
                self._state = AccState.LOCKED
        else:
            self.msg_len = L
            self._chars  = [CharacterState() for _ in range(L)]
            for i, m in enumerate(self._history):
                self._chars[i % L].update(m)
            self._detect_attempts = 1
            self._state = AccState.DETECTING

    # ------------------------------------------------------------------
    def _end_of_cycle(self):
        confs     = [cs.confidence for cs in self._chars if cs.count > 0]
        mean_conf = float(np.mean(confs)) if confs else 0.0
        self._cycle_confidences.append(mean_conf)
        # Trim history
        if len(self._cycle_confidences) > COLLAPSE_WINDOW * 3:
            self._cycle_confidences = self._cycle_confidences[-(COLLAPSE_WINDOW * 3):]

        # Collapse detection — only once we've been locked for COLLAPSE_WINDOW cycles
        if self._state in (AccState.LOCKED, AccState.CONFIRMED):
            repeats = min(cs.count for cs in self._chars)
            if repeats >= COLLAPSE_WINDOW:
                recent = self._cycle_confidences[-COLLAPSE_WINDOW:]
                if all(c < COLLAPSE_THRESHOLD for c in recent):
                    self._low_conf_streak += 1
                else:
                    self._low_conf_streak = 0
                if self._low_conf_streak >= COLLAPSE_WINDOW:
                    reason = (f"confidence collapsed "
                              f"({', '.join(f'{c:.2f}' for c in recent)}) "
                              f"— signal changed or lost")
                    self.reset(reason)
                    return

        # Confirmed copy check
        if self._state in (AccState.LOCKED, AccState.DETECTING):
            self._check_copy(mean_conf)

    # ------------------------------------------------------------------
    def _check_copy(self, mean_conf):
        repeats = min(cs.count for cs in self._chars) if self._chars else 0
        if mean_conf < COPY_THRESHOLD or repeats < 2:
            return
        message = ''.join(cs.char for cs in self._chars)
        if self._copies and self._copies[-1]['message'] == message:
            return
        self._state = AccState.CONFIRMED
        entry = {
            'time'      : time.strftime('%H:%M:%Sz'),
            'message'   : message,
            'confidence': mean_conf,
            'repeats'   : repeats,
            'phase'     : self._phase,
        }
        self._copies.append(entry)
        if self.on_copy:
            self.on_copy(entry)

    # ------------------------------------------------------------------
    def get_display_state(self):
        """
        Returns dict for GUI:
        {
          'state':           AccState
          'state_label':     str  ('SEARCHING' / 'DETECTING' / 'LOCKED' / 'CONFIRMED')
          'msg_len':         int or None
          'repeats':         int
          'phase':           int or None
          'chars': [{
            'char', 'confidence', 'count', 'flipped'
          }, ...]
          'mean_confidence': float
          'conf_trend':      float  (+ve = improving)
          'copies':          list of copy dicts
        }
        """
        if not self._chars:
            return {
                'state': self._state, 'state_label': self._state.name,
                'msg_len': self.msg_len, 'repeats': 0, 'phase': self._phase,
                'chars': [], 'mean_confidence': 0.0, 'conf_trend': 0.0,
                'copies': self._copies,
            }

        char_states = [
            {'char': cs.char, 'confidence': cs.confidence,
             'count': cs.count, 'flipped': cs.flipped}
            for cs in self._chars
        ]
        confs     = [cs.confidence for cs in self._chars if cs.count > 0]
        mean_conf = float(np.mean(confs)) if confs else 0.0
        repeats   = min(cs.count for cs in self._chars)
        trend     = 0.0
        if len(self._cycle_confidences) >= 2:
            trend = self._cycle_confidences[-1] - self._cycle_confidences[-2]

        return {
            'state': self._state, 'state_label': self._state.name,
            'msg_len': self.msg_len, 'repeats': repeats, 'phase': self._phase,
            'chars': char_states, 'mean_confidence': mean_conf,
            'conf_trend': trend, 'copies': self._copies,
        }

    @staticmethod
    def confidence_colour(conf):
        """(bg_hex, fg_hex) for GUI character cells."""
        if conf < CONFIDENCE_THRESHOLD: return '#6b1a1a', '#ff6666'
        elif conf < 0.35:               return '#6b4a00', '#ffaa33'
        elif conf < 0.55:               return '#5a5a00', '#eeee44'
        elif conf < COPY_THRESHOLD:     return '#1a5a1a', '#66ee66'
        else:                           return '#0a3a0a', '#00ff88'


# ---------------------------------------------------------------------------
if __name__ == '__main__':
    import sys, os
    from scipy.io import wavfile
    from scipy.signal import find_peaks

    SAMPLE_RATE = 44100; SAMPLES_PER_SYM = 4900; FFT_LEN = SAMPLES_PER_SYM
    TONE_BIN = round(800 * FFT_LEN / SAMPLE_RATE); TONE_TOLERANCE = 3

    def get_mags(tone_ch, char_start):
        n_bins = TONE_TOLERANCE * 2 + 1
        cache  = np.zeros((8, n_bins))
        lo, hi = TONE_BIN - TONE_TOLERANCE, TONE_BIN + TONE_TOLERANCE + 1
        for sym in range(8):
            s = char_start + sym * SAMPLES_PER_SYM
            if s + FFT_LEN > len(tone_ch): break
            seg = tone_ch[s:s+FFT_LEN] * np.hanning(FFT_LEN)
            cache[sym] = np.abs(np.fft.rfft(seg, n=FFT_LEN))[lo:hi]
        return cache.max(axis=1)

    def find_clicks(ch):
        abs_ch = np.abs(ch)
        p, _ = find_peaks(abs_ch, height=np.max(abs_ch)*0.3, distance=int(44100*0.8))
        return p

    def render(state):
        if not state['chars']:
            return f"  [{state['state_label']:10s}] waiting..."
        msg  = ''.join('↵' if c['char']=='\r' else c['char'] for c in state['chars'])
        bars = ''.join(
            '█' if c['confidence']>0.65 else '▓' if c['confidence']>0.40
            else '░' if c['confidence']>CONFIDENCE_THRESHOLD else '·'
            for c in state['chars'])
        t = '+' if state['conf_trend']>0.01 else ('-' if state['conf_trend']<-0.01 else '=')
        return (f"  [{state['state_label']:10s} L={state['msg_len']} "
                f"x{state['repeats']:02d} {t}] {msg}  "
                f"conf={state['mean_confidence']:.2f}\n  {'':28}{bars}")

    # Test with confidence collapse: concatenate a clean file then a noisy one
    files = sys.argv[1:] if len(sys.argv) > 1 else [
        'OOK48Test_180s_SNR-18dB_3500Hz.wav',
        'OOK48Test_180s_SNR-20dB_3500Hz.wav',
        'OOK48Test_180s_SNR-23dB_3500Hz.wav',
    ]
    files = [f for f in files if os.path.exists(f)]
    if not files: print("No files"); sys.exit(1)

    for filename in files:
        print(f"\n{'='*65}\nFile: {os.path.basename(filename)}\n{'='*65}")
        _, data = wavfile.read(filename)
        clicks  = find_clicks(data[:,0].astype(np.float64))
        tone_ch = data[:,1].astype(np.float64)
        copies  = []

        acc = OOK48Accumulator(
            on_copy  = lambda e: print(f"\n  ★ COPY [{e['time']}] "
                                       f"{''.join(c if c!=chr(13) else '↵' for c in e['message'])}  "
                                       f"conf={e['confidence']:.2f}  x{e['repeats']}"),
            on_reset = lambda r: print(f"\n  ⚠ RESET: {r}")
        )

        for i, click in enumerate(clicks):
            acc.push(get_mags(tone_ch, int(click)))
            if (i + 1) % 11 == 0:
                print(render(acc.get_display_state()))

        s = acc.get_display_state()
        print(f"\nFinal [{s['state_label']}]: copies={len(s['copies'])}")