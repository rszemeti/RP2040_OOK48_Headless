#!/usr/bin/env python3
# ook48_accpanel.py  v1.0
"""
AccumulatorPanel — tkinter widget for the OOK48 GUI.

Displays 4 rows of soft-accumulated decode at x1/x2/x4/x8 depths.
Each character cell is coloured red→amber→yellow→green by confidence.
Sits above the existing RX decode text area.

Drop-in usage in OOK48GUI.build_main_tab():

    from ook48_accpanel import AccumulatorPanel
    self.acc_panel = AccumulatorPanel(rx_frame)
    self.acc_panel.pack(fill=tk.X, pady=(0, 4))

Then in handle_line(), when a SFT: arrives:
    elif line.startswith("SFT:"):
        mags = [float(x) for x in line[4:].split(",")]
        self.acc_panel.push(mags)
"""

import tkinter as tk
from tkinter import ttk
import numpy as np
import time
from ook48_accumulator import OOK48Accumulator, CONFIDENCE_THRESHOLD, COPY_THRESHOLD

# ---------------------------------------------------------------------------
# Colour scheme — dark terminal aesthetic to match radio software
# ---------------------------------------------------------------------------
BG_PANEL   = '#1a1a1a'
BG_CELL    = '#2a2a2a'
BG_CELL_HL = '#3a3a3a'   # flipped/just-changed
FG_LABEL   = '#888888'
FG_COPY    = '#00ff88'
FG_STATUS  = '#666666'

# Confidence → background colour gradient
def conf_colour(conf):
    if conf < CONFIDENCE_THRESHOLD:
        return '#6b1a1a', '#ff6666'   # bg, fg  — red
    elif conf < 0.35:
        return '#6b4a00', '#ffaa33'   # amber
    elif conf < 0.55:
        return '#5a5a00', '#eeee44'   # yellow
    elif conf < 0.70:
        return '#1a5a1a', '#66ee66'   # light green
    else:
        return '#0a3a0a', '#00ff88'   # bright green


ROWS = [1, 2, 4, 8]
MAX_DISPLAY_LEN = 30   # max chars to display (message could be up to 30)
CHAR_FONT  = ('Courier', 13, 'bold')
LABEL_FONT = ('Courier', 9)
STATUS_FONT= ('Courier', 9)


class CharCell(tk.Frame):
    """Single character cell with coloured background."""
    def __init__(self, parent):
        super().__init__(parent, bg=BG_CELL, width=26, height=30,
                         highlightthickness=1, highlightbackground='#333333')
        self.pack_propagate(False)
        self._label = tk.Label(self, text=' ', font=CHAR_FONT,
                               bg=BG_CELL, fg='#444444', width=1)
        self._label.pack(expand=True)

    def update(self, char, confidence, flipped=False):
        if char == '\r':
            display = '↵'
        elif char == '~':
            display = '·'
        else:
            display = char

        bg, fg = conf_colour(confidence)
        if flipped:
            bg = BG_CELL_HL

        self.configure(bg=bg, highlightbackground='#444444' if confidence > CONFIDENCE_THRESHOLD else '#2a2a2a')
        self._label.configure(text=display, bg=bg, fg=fg)

    def clear(self):
        self.configure(bg=BG_CELL, highlightbackground='#333333')
        self._label.configure(text=' ', bg=BG_CELL, fg='#444444')


class AccumulatorRow(tk.Frame):
    """One row: label + character cells + status."""
    def __init__(self, parent, depth):
        super().__init__(parent, bg=BG_PANEL)
        self.depth = depth

        # Row label
        lbl = tk.Label(self, text=f'x{depth}', font=LABEL_FONT,
                       bg=BG_PANEL, fg=FG_LABEL, width=3, anchor='e')
        lbl.pack(side=tk.LEFT, padx=(4, 6))

        # Character cells container
        self._cell_frame = tk.Frame(self, bg=BG_PANEL)
        self._cell_frame.pack(side=tk.LEFT)
        self._cells = []

        # Status label (right side)
        self._status = tk.Label(self, text='', font=STATUS_FONT,
                                bg=BG_PANEL, fg=FG_STATUS, anchor='w')
        self._status.pack(side=tk.LEFT, padx=(8, 4))

        # Copy flash label
        self._copy_lbl = tk.Label(self, text='', font=STATUS_FONT,
                                  bg=BG_PANEL, fg=FG_COPY, anchor='e')
        self._copy_lbl.pack(side=tk.RIGHT, padx=4)

    def set_length(self, n):
        """Resize to n character cells."""
        if len(self._cells) == n:
            return
        for c in self._cells:
            c.destroy()
        self._cells = []
        for _ in range(n):
            cell = CharCell(self._cell_frame)
            cell.pack(side=tk.LEFT, padx=1, pady=2)
            self._cells.append(cell)

    def update_chars(self, chars, repeats):
        """
        chars: list of {char, confidence, flipped} dicts from accumulator state.
        Only updates cells where count >= depth.
        """
        n = len(chars)
        self.set_length(n)
        active = 0
        for i, (cell, c) in enumerate(zip(self._cells, chars)):
            if c['count'] >= self.depth:
                cell.update(c['char'], c['confidence'], c['flipped'])
                active += 1
            else:
                cell.clear()
        if active > 0:
            self._status.configure(text=f'x{repeats:02d}' if self.depth == 1 else '')
        else:
            self._status.configure(text='…')

    def flash_copy(self):
        """Brief green flash on confirmed copy."""
        self._copy_lbl.configure(text='✓ COPY')
        self._cell_frame.after(3000, lambda: self._copy_lbl.configure(text=''))

    def clear(self):
        for c in self._cells:
            c.clear()
        self._status.configure(text='')
        self._copy_lbl.configure(text='')


class AccumulatorPanel(tk.Frame):
    """
    Four-row accumulator display panel.
    Instantiate and pack above the RX decode area.
    Call push(mags) for each SFT: line received.
    """

    def __init__(self, parent, on_copy=None):
        super().__init__(parent, bg=BG_PANEL,
                         highlightthickness=1, highlightbackground='#333333')

        self._on_copy_cb = on_copy

        # Header bar
        header = tk.Frame(self, bg=BG_PANEL)
        header.pack(fill=tk.X, padx=4, pady=(3, 0))
        tk.Label(header, text='ACCUMULATOR', font=('Courier', 8),
                 bg=BG_PANEL, fg='#555555').pack(side=tk.LEFT)
        self._length_lbl = tk.Label(header, text='', font=('Courier', 8),
                                    bg=BG_PANEL, fg='#555555')
        self._length_lbl.pack(side=tk.RIGHT, padx=4)

        # Separator
        sep = tk.Frame(self, bg='#333333', height=1)
        sep.pack(fill=tk.X, padx=4, pady=(2, 0))

        # Four accumulator rows
        self._rows = {}
        for depth in ROWS:
            row = AccumulatorRow(self, depth)
            row.pack(fill=tk.X, padx=2, pady=1)
            self._rows[depth] = row

        # Separator
        sep2 = tk.Frame(self, bg='#333333', height=1)
        sep2.pack(fill=tk.X, padx=4, pady=(2, 2))

        # Internal accumulator — 4 independent ones at different depths
        # All share the same magnitude history via a master accumulator
        self._master = OOK48Accumulator(
            on_update=self._on_update,
            on_copy=self._on_copy,
        )
        # Shadow accumulators at fixed depths share msg_len once known
        self._depth_accs = {d: OOK48Accumulator() for d in ROWS}

    def push(self, mags):
        """Feed one character's magnitudes. Call from GUI serial handler."""
        mags = np.asarray(mags, dtype=np.float64)
        self._master.push(mags)
        for acc in self._depth_accs.values():
            acc.push(mags)
            # Propagate detected length from master
            if self._master.msg_len and acc.msg_len != self._master.msg_len:
                acc.msg_len = self._master.msg_len
                acc._length_locked = self._master._length_locked

    def _on_update(self, state):
        """Called by master accumulator after every push."""
        if not state['chars']:
            self._length_lbl.configure(text='detecting…')
            return

        L       = state['msg_len']
        locked  = state['locked']
        repeats = state['repeats']

        lock_sym = '✓' if locked else '?'
        self._length_lbl.configure(text=f'{lock_sym} L={L}  x{repeats}')

        # Build per-depth views from the depth accumulators
        for depth, row in self._rows.items():
            ds = self._depth_accs[depth].get_display_state()
            if ds['chars']:
                # Clamp: only show chars where count >= depth
                row.update_chars(ds['chars'], ds['repeats'])
            else:
                row.clear()

    def _on_copy(self, entry):
        """Called when master accumulator confirms a copy."""
        # Flash the x8 row
        self._rows[8].flash_copy()
        if self._on_copy_cb:
            self._on_copy_cb(entry)

    def reset(self):
        self._master.reset()
        for acc in self._depth_accs.values():
            acc.reset()
        for row in self._rows.values():
            row.clear()
        self._length_lbl.configure(text='')


# ---------------------------------------------------------------------------
# Standalone demo — simulates SFT: stream from a WAV file
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    import sys, os, threading
    from scipy.io import wavfile
    from scipy.signal import find_peaks

    SAMPLE_RATE     = 44100
    SAMPLES_PER_SYM = 4900
    FFT_LEN         = SAMPLES_PER_SYM
    TONE_BIN        = round(800 * FFT_LEN / SAMPLE_RATE)
    TONE_TOLERANCE  = 3

    def get_mags(tone_ch, char_start):
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

    def find_clicks(click_ch):
        click_abs = np.abs(click_ch)
        peaks, _  = find_peaks(click_abs, height=np.max(click_abs)*0.3,
                               distance=int(SAMPLE_RATE*0.8))
        return peaks

    filename = sys.argv[1] if len(sys.argv) > 1 else 'OOK48Test_180s_SNR-20dB_3500Hz.wav'
    if not os.path.exists(filename):
        print(f"File not found: {filename}")
        sys.exit(1)

    print(f"Loading {filename}...")
    _, data  = wavfile.read(filename)
    click_ch = data[:,0].astype(np.float64)
    tone_ch  = data[:,1].astype(np.float64)
    clicks   = find_clicks(click_ch)
    print(f"Found {len(clicks)} characters")

    # Build GUI
    root = tk.Tk()
    root.title(f"OOK48 Accumulator Demo — {os.path.basename(filename)}")
    root.configure(bg='#1a1a1a')
    root.geometry('700x320')

    # Copy log
    copy_log = tk.Text(root, height=4, font=('Courier', 10),
                       bg='#0a0a0a', fg='#00ff88',
                       insertbackground='white', relief=tk.FLAT)
    copy_log.pack(fill=tk.X, padx=5, pady=(0, 3), side=tk.BOTTOM)

    tk.Label(root, text='CONFIRMED COPIES', font=('Courier', 8),
             bg='#1a1a1a', fg='#555555').pack(side=tk.BOTTOM, anchor='w', padx=5)

    def on_copy(entry):
        msg = entry['message'].replace('\r', '↵')
        copy_log.insert(tk.END,
            f"[{entry['time']}] {msg}  conf={entry['confidence']:.2f}  x{entry['repeats']}\n")
        copy_log.see(tk.END)

    panel = AccumulatorPanel(root, on_copy=on_copy)
    panel.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    # Feed magnitudes in real time (1 per second to simulate live decode)
    idx = [0]
    def feed_next():
        if idx[0] < len(clicks):
            mags = get_mags(tone_ch, int(clicks[idx[0]]))
            panel.push(mags)
            idx[0] += 1
            root.after(200, feed_next)   # 200ms per char for demo (5x faster than real)

    root.after(500, feed_next)
    root.mainloop()