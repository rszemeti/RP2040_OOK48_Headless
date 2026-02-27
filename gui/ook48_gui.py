#!/usr/bin/env python3
"""
OOK48 Serial Control GUI
Connects to RP2040 OOK48 firmware over USB serial.
Handles config push on connect, decode display, TX control.

Requirements: pip install pyserial
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
import serial
import serial.tools.list_ports
import threading
import json
import os
import time
import queue
import webbrowser
from datetime import datetime
import numpy as np
from PIL import Image, ImageTk
from ook48_accpanel import AccumulatorPanel

CONFIG_FILE = "ook48_config.json"
DEFAULT_CONFIG = {
    "port": "",
    "callsign": "",
    "serial": 1,
    "loclen": 8,
    "decmode": 0,
    "txadv": 0,
    "rxret": 0,
    "halfrate": 0,
    "confidence": 0.180,
    "app": 0,
    "messages": [
        "CQ {myCall}",
        "{theirCall} DE {myCall}",
        "{theirCall} 59{serial} {loc}",
        "{theirCall} 59{serial}",
        "{loc}",
        "ALL AGN",
        "LOC AGN",
        "RPT AGN",
        "RGR 73",
        "{myCall}",
    ]
}

APP_NAMES = ["OOK48", "JT4G Decoder", "PI4 Decoder"]
REPO_URL = "https://github.com/rszemeti/RP2040_OOK48_Headless"

class OOK48GUI:
    SLOT_LABELS = [
        "CQ",
        "Their call",
        "Full exch",
        "Exch no loc",
        "Loc only",
        "ALL AGN",
        "LOC AGN",
        "RPT AGN",
        "RGR 73",
        "My call",
    ]

    def __init__(self, root):
        self.root = root
        self.root.title("OOK48 Serial Control + Waterfall")
        self.root.geometry("1100x750")
        self.root.minsize(800, 600)

        self.serial_port = None
        self.connected = False
        self.read_thread = None
        self.config = self.load_config()
        self.qso_templates = self._templates_from_config()
        self.log_file = self._open_log_file()
        self.tx_mode = False
        self.current_loc = ""
        self.last_decode_tag = None
        self.wf_bins = 0
        self.wf_height = 300    # number of history rows to keep
        self.wf_queue = queue.Queue()  # serial thread -> GUI thread
        self.wf_dirty = False  # tracks when a new message line starts
        self.supports_rainscatter = True
        self.config_push_in_progress = False

        self.build_ui()
        self.refresh_ports()
        self._wf_poll()  # start queue drain loop

    # ------------------------------------------------------------------
    # Config persistence
    # ------------------------------------------------------------------
    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE) as f:
                    cfg = json.load(f)
                changed = False
                # Fill in any missing keys from defaults
                for k, v in DEFAULT_CONFIG.items():
                    if k not in cfg:
                        cfg[k] = v
                        changed = True
                normalized_messages = self._normalize_message_templates(cfg.get("messages"))
                if cfg.get("messages") != normalized_messages:
                    cfg["messages"] = normalized_messages
                    changed = True
                if changed:
                    with open(CONFIG_FILE, "w") as f:
                        json.dump(cfg, f, indent=2)
                return cfg
            except Exception:
                pass
        return dict(DEFAULT_CONFIG)

    def save_config(self):
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            self.log(f"[WARN] Could not save config: {e}")

    def _default_message_templates(self):
        return list(DEFAULT_CONFIG["messages"])

    def _templates_from_config(self):
        messages = self._normalize_message_templates(self.config.get("messages"))
        self.config["messages"] = messages
        return [
            (slot, self.SLOT_LABELS[slot], messages[slot])
            for slot in range(len(self.SLOT_LABELS))
        ]

    def _normalize_message_templates(self, messages):
        defaults = self._default_message_templates()
        if not isinstance(messages, list) or len(messages) != len(defaults):
            return defaults

        cleaned = [str(m).replace("\n", "").replace("\r", "") for m in messages]
        if any((not text) or text.upper() == "EMPTY" for text in cleaned):
            return defaults

        template_tokens = ("{myCall}", "{theirCall}", "{serial}", "{loc}")
        has_templates = any(any(tok in text for tok in template_tokens) for text in cleaned)
        if not has_templates:
            return defaults

        return cleaned

    # ------------------------------------------------------------------
    # File logging
    # ------------------------------------------------------------------
    def _open_log_file(self):
        filename = datetime.utcnow().strftime("ook48_%Y%m%d.log")
        try:
            f = open(filename, "a", buffering=1)  # line-buffered
            f.write(f"\n--- Session started {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%Sz')} ---\n")
            return f
        except Exception as e:
            print(f"Could not open log file: {e}")
            return None

    def _write_log(self, text):
        if self.log_file:
            try:
                self.log_file.write(text)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def build_ui(self):
        self.build_menu()

        # Top bar: connection controls
        conn_frame = ttk.LabelFrame(self.root, text="Connection", padding=5)
        conn_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(conn_frame, text="Port:").pack(side=tk.LEFT)
        self.port_var = tk.StringVar(value=self.config.get("port", ""))
        self.port_combo = ttk.Combobox(conn_frame, textvariable=self.port_var, width=18)
        self.port_combo.pack(side=tk.LEFT, padx=3)

        ttk.Button(conn_frame, text="Refresh", command=self.refresh_ports).pack(side=tk.LEFT, padx=2)
        self.connect_btn = ttk.Button(conn_frame, text="Connect", command=self.toggle_connect)
        self.connect_btn.pack(side=tk.LEFT, padx=2)

        self.status_label = ttk.Label(conn_frame, text="Disconnected", foreground="red")
        self.status_label.pack(side=tk.LEFT, padx=10)

        self.gps_label = ttk.Label(conn_frame, text="GPS: --:--:--", foreground="grey")
        self.gps_label.pack(side=tk.RIGHT, padx=10)
        self.loc_label = ttk.Label(conn_frame, text="", foreground="grey")
        self.loc_label.pack(side=tk.RIGHT, padx=5)
        self.remote_fw_label = ttk.Label(conn_frame, text="Remote FW: --", foreground="grey")
        self.remote_fw_label.pack(side=tk.RIGHT, padx=10)

        # RX audio level meter
        ttk.Label(conn_frame, text="RX Level:").pack(side=tk.RIGHT, padx=(10, 2))
        self.level_label = ttk.Label(conn_frame, text="--", width=4, foreground="grey")
        self.level_label.pack(side=tk.RIGHT)
        self.level_bar = ttk.Progressbar(conn_frame, orient=tk.HORIZONTAL, length=80,
                                         maximum=100, mode="determinate")
        self.level_bar.pack(side=tk.RIGHT, padx=2)

        # Notebook for main areas
        nb = ttk.Notebook(self.root)
        nb.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Tab 1: Decode output + TX control
        main_tab = ttk.Frame(nb)
        nb.add(main_tab, text="Decode / TX")
        self.build_main_tab(main_tab)

        # Tab 2: Settings
        cfg_tab = ttk.Frame(nb)
        nb.add(cfg_tab, text="Settings")
        self.build_settings_tab(cfg_tab)

        # Bottom status bar
        status_bar = ttk.Frame(self.root)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)
        self.bottom_status = ttk.Label(status_bar, text="Ready", anchor=tk.W)
        self.bottom_status.pack(fill=tk.X, padx=5)

    def build_menu(self):
        menubar = tk.Menu(self.root)
        about_menu = tk.Menu(menubar, tearoff=0)
        about_menu.add_command(label="About OOK48…", command=self.show_about_dialog)
        menubar.add_cascade(label="About", menu=about_menu)
        self.root.config(menu=menubar)

    def build_main_tab(self, parent):
        # Horizontal paned window — left: RX decode, right: TX controls
        pane = tk.PanedWindow(parent, orient=tk.HORIZONTAL, sashrelief=tk.RAISED, sashwidth=5)
        pane.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # ---- Left pane: waterfall + decoded messages ----
        rx_frame = ttk.LabelFrame(pane, text="RX", padding=5)
        pane.add(rx_frame, stretch="always", minsize=250)

        # Waterfall
        wf_frame = ttk.LabelFrame(rx_frame, text="Waterfall", padding=2)
        wf_frame.pack(fill=tk.X)

        wf_ctrl = ttk.Frame(wf_frame)
        wf_ctrl.pack(fill=tk.X)
        ttk.Label(wf_ctrl, text="Min:").pack(side=tk.LEFT)
        self.wf_min_var = tk.IntVar(value=0)
        ttk.Spinbox(wf_ctrl, from_=0, to=254, textvariable=self.wf_min_var,
                    width=4, command=self.wf_rescale).pack(side=tk.LEFT, padx=(2,6))
        ttk.Label(wf_ctrl, text="Max:").pack(side=tk.LEFT)
        self.wf_max_var = tk.IntVar(value=255)
        ttk.Spinbox(wf_ctrl, from_=1, to=255, textvariable=self.wf_max_var,
                    width=4, command=self.wf_rescale).pack(side=tk.LEFT, padx=(2,6))
        ttk.Button(wf_ctrl, text="Auto", command=self.wf_auto_scale).pack(side=tk.LEFT, padx=2)
        ttk.Button(wf_ctrl, text="Clear WF", command=self.wf_clear).pack(side=tk.LEFT, padx=2)
        self.decmode_main_btn = ttk.Button(
            wf_ctrl,
            text="",
            command=self.on_main_decode_toggle,
        )
        self.decmode_main_btn.pack(side=tk.LEFT, padx=(8, 2))
        self.wf_info = ttk.Label(wf_ctrl, text="", foreground="grey")
        self.wf_info.pack(side=tk.RIGHT, padx=4)
        self._sync_decode_mode_controls()

        self.wf_canvas = tk.Canvas(wf_frame, background="black", height=150)
        self.wf_canvas.pack(fill=tk.X)
        self.wf_canvas.bind("<Configure>", self._wf_on_resize)
        self.wf_canvas_image_id = None
        self.wf_rows = []
        self.wf_tk_image = None

        # Accumulator panel (between waterfall and message log)
        self.acc_panel = AccumulatorPanel(rx_frame, on_state_change=self.on_acc_state_change)
        self.acc_panel.pack(fill=tk.X, pady=(4, 0))
        self.acc_panel.set_confidence_threshold(self.config.get("confidence", 0.180))

        # Decoded messages
        decode_frame = ttk.LabelFrame(rx_frame, text="Decoded Messages", padding=2)
        decode_frame.pack(fill=tk.BOTH, expand=True, pady=(4,0))

        btn_row = ttk.Frame(decode_frame)
        btn_row.pack(fill=tk.X)
        ttk.Button(btn_row, text="Clear", command=self.clear_decode).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Save Log…", command=self.save_log).pack(side=tk.LEFT, padx=5)

        self.decode_text = scrolledtext.ScrolledText(decode_frame, font=("Courier", 11))
        self.decode_text.pack(fill=tk.BOTH, expand=True, pady=3)
        self.decode_text.tag_config("rx", foreground="green")
        self.decode_text.tag_config("tx", foreground="red")
        self.decode_text.tag_config("err", foreground="orange")
        self.decode_text.tag_config("jt", foreground="darkgreen")
        self.decode_text.tag_config("pi", foreground="purple")
        self.decode_text.tag_config("sys", foreground="grey")
        self.decode_text.bind("<Double-Button-1>", self.on_decode_double_click)

        # ---- Right pane: TX controls ----
        tx_outer = ttk.Frame(pane)
        pane.add(tx_outer, stretch="never", minsize=260)

        tx_frame = ttk.LabelFrame(tx_outer, text="TX — Contest QSO", padding=8)
        tx_frame.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)

        # -- QSO fields --
        fields_frame = ttk.Frame(tx_frame)
        fields_frame.pack(fill=tk.X, pady=(0, 6))
        fields_frame.columnconfigure(1, weight=1)

        ttk.Label(fields_frame, text="My call:").grid(row=0, column=0, sticky=tk.W, padx=(0,4), pady=2)
        self.callsign_var = tk.StringVar(value=self.config.get("callsign", ""))
        my_call_entry = ttk.Entry(fields_frame, textvariable=self.callsign_var, width=10)
        my_call_entry.grid(row=0, column=1, sticky=tk.EW, pady=2, columnspan=2)
        self.callsign_var.trace_add("write", self.on_callsign_change)

        ttk.Label(fields_frame, text="Their call:").grid(row=1, column=0, sticky=tk.W, padx=(0,4), pady=2)
        self.theircall_var = tk.StringVar()
        their_entry = ttk.Entry(fields_frame, textvariable=self.theircall_var, width=10)
        their_entry.grid(row=1, column=1, sticky=tk.EW, pady=2)
        self.theircall_var.trace_add("write", lambda *_: self._upcase_var(self.theircall_var) or self.refresh_qso_buttons())

        ttk.Label(fields_frame, text="Serial #:").grid(row=2, column=0, sticky=tk.W, padx=(0,4), pady=2)
        self.serial_var = tk.IntVar(value=self.config.get("serial", 1))
        serial_spin = ttk.Spinbox(fields_frame, from_=1, to=9999, textvariable=self.serial_var,
                                  width=6, command=self.refresh_qso_buttons)
        serial_spin.grid(row=2, column=1, sticky=tk.W, pady=2)
        serial_spin.bind("<Return>", lambda e: self.refresh_qso_buttons())
        ttk.Button(fields_frame, text="+1", width=4,
                   command=self.increment_serial).grid(row=2, column=2, padx=(4,0), pady=2)

        ttk.Separator(tx_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(2,6))

        # -- QSO slot buttons --
        self.qso_btn_frame = ttk.Frame(tx_frame)
        self.qso_btn_frame.pack(fill=tk.X)
        self.qso_buttons = []
        self._build_qso_buttons()

        ttk.Separator(tx_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=6)

        # -- Free text entry --
        ft_frame = ttk.LabelFrame(tx_frame, text="Free text", padding=4)
        ft_frame.pack(fill=tk.X, pady=(0,6))
        self.freetext_var = tk.StringVar()
        ft_entry = ttk.Entry(ft_frame, textvariable=self.freetext_var, font=("Courier", 10))
        ft_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0,4))
        ft_entry.bind("<KeyRelease>", lambda e: self._upcase_entry(self.freetext_var))
        ft_entry.bind("<Return>", lambda e: self.send_freetext())
        ttk.Button(ft_frame, text="Send", command=self.send_freetext).pack(side=tk.LEFT)

        self.dashes_btn = ttk.Button(tx_frame, text="Send Dashes", command=self.send_dashes)
        self.dashes_btn.pack(fill=tk.X, pady=(0, 4))

        self.stop_btn = ttk.Button(tx_frame, text="■  STOP TX", command=self.stop_tx)
        self.stop_btn.pack(fill=tk.X)

        self.active_slot_label = ttk.Label(tx_frame, text="", font=("Courier", 9),
                                           foreground="grey", wraplength=240)
        self.active_slot_label.pack(anchor=tk.W, pady=(4,0))

        self.tx_slot_var = tk.IntVar(value=0)
        self.tx_btn = self.stop_btn


    def build_settings_tab(self, parent):
        sf = ttk.Frame(parent)
        sf.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        def row(label, widget_fn, col=0):
            r = len(sf.grid_slaves()) // 2
            ttk.Label(sf, text=label).grid(row=r, column=col*2, sticky=tk.W, pady=3, padx=5)
            w = widget_fn(sf)
            w.grid(row=r, column=col*2+1, sticky=tk.W, pady=3, padx=5)
            return w

        # App selection
        ttk.Label(sf, text="Application:").grid(row=0, column=0, sticky=tk.W, pady=3, padx=5)
        self.app_var = tk.IntVar(value=self.config["app"])
        app_combo = ttk.Combobox(sf, textvariable=self.app_var, values=APP_NAMES, state="readonly", width=18)
        app_combo.current(self.config["app"])
        app_combo.grid(row=0, column=1, sticky=tk.W, pady=3, padx=5)
        self.app_combo = app_combo

        # Locator length
        ttk.Label(sf, text="Locator length:").grid(row=1, column=0, sticky=tk.W, pady=3, padx=5)
        self.loclen_var = tk.IntVar(value=self.config["loclen"])
        loc_combo = ttk.Combobox(sf, textvariable=self.loclen_var, values=[6, 8, 10], state="readonly", width=10)
        loc_combo.grid(row=1, column=1, sticky=tk.W, pady=3, padx=5)

        # Decode mode
        ttk.Label(sf, text="Decode mode:").grid(row=2, column=0, sticky=tk.W, pady=3, padx=5)
        self.decmode_var = tk.IntVar(value=self.config["decmode"])
        dm_combo = ttk.Combobox(
            sf,
            textvariable=self.decmode_var,
            values=["Normal (0)", "Rainscatter (2)"],
            state="readonly",
            width=18,
        )
        decmode = int(self.config.get("decmode", 0))
        if decmode == 2:
            dm_combo.current(1)
        else:
            dm_combo.current(0)
            self.config["decmode"] = 0
        dm_combo.grid(row=2, column=1, sticky=tk.W, pady=3, padx=5)
        self.dm_combo = dm_combo

        # Half rate
        ttk.Label(sf, text="Character period:").grid(row=3, column=0, sticky=tk.W, pady=3, padx=5)
        self.halfrate_var = tk.IntVar(value=self.config["halfrate"])
        hr_combo = ttk.Combobox(sf, textvariable=self.halfrate_var, values=["1s (normal)", "2s (half rate)"], state="readonly", width=16)
        hr_combo.current(self.config["halfrate"])
        hr_combo.grid(row=3, column=1, sticky=tk.W, pady=3, padx=5)
        self.hr_combo = hr_combo

        # TX advance
        ttk.Label(sf, text="TX timing advance (ms):").grid(row=4, column=0, sticky=tk.W, pady=3, padx=5)
        self.txadv_var = tk.IntVar(value=self.config["txadv"])
        ttk.Spinbox(sf, from_=0, to=999, textvariable=self.txadv_var, width=8).grid(row=4, column=1, sticky=tk.W, pady=3, padx=5)

        # RX retard
        ttk.Label(sf, text="RX timing retard (ms):").grid(row=5, column=0, sticky=tk.W, pady=3, padx=5)
        self.rxret_var = tk.IntVar(value=self.config["rxret"])
        ttk.Spinbox(sf, from_=0, to=999, textvariable=self.rxret_var, width=8).grid(row=5, column=1, sticky=tk.W, pady=3, padx=5)

        # Confidence threshold
        ttk.Label(sf, text="Confidence threshold:").grid(row=6, column=0, sticky=tk.W, pady=3, padx=5)
        self.confidence_var = tk.DoubleVar(value=self.config.get("confidence", 0.180))
        conf_spin = ttk.Spinbox(sf, from_=0.01, to=0.99, increment=0.01,
                                textvariable=self.confidence_var, width=8, format="%.3f")
        conf_spin.grid(row=6, column=1, sticky=tk.W, pady=3, padx=5)
        ttk.Label(sf, text="(OOK48 UNK gate, default 0.180)",
                  foreground="grey").grid(row=6, column=2, sticky=tk.W, pady=3, padx=5)

        # Buttons
        btn_frame = ttk.Frame(sf)
        btn_frame.grid(row=7, column=0, columnspan=3, pady=15)
        ttk.Button(btn_frame, text="Apply Settings", command=self.apply_settings).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Save to File", command=self.save_config_ui).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Reboot Device", command=self.reboot_device).pack(side=tk.LEFT, padx=5)

    # ------------------------------------------------------------------
    # Waterfall tab
    # ------------------------------------------------------------------
    def build_waterfall_tab(self, parent):
        ctrl = ttk.Frame(parent)
        ctrl.pack(fill=tk.X, padx=5, pady=3)

        ttk.Label(ctrl, text="Min:").pack(side=tk.LEFT)
        self.wf_min_var = tk.IntVar(value=0)
        ttk.Spinbox(ctrl, from_=0, to=254, textvariable=self.wf_min_var,
                    width=5, command=self.wf_rescale).pack(side=tk.LEFT, padx=(2,8))

        ttk.Label(ctrl, text="Max:").pack(side=tk.LEFT)
        self.wf_max_var = tk.IntVar(value=255)
        ttk.Spinbox(ctrl, from_=1, to=255, textvariable=self.wf_max_var,
                    width=5, command=self.wf_rescale).pack(side=tk.LEFT, padx=(2,8))

        ttk.Button(ctrl, text="Auto", command=self.wf_auto_scale).pack(side=tk.LEFT, padx=4)
        ttk.Button(ctrl, text="Clear", command=self.wf_clear).pack(side=tk.LEFT, padx=4)

        self.wf_info = ttk.Label(ctrl, text="No data", foreground="grey")
        self.wf_info.pack(side=tk.RIGHT, padx=8)

        # Canvas fills the rest of the tab
        self.wf_canvas = tk.Canvas(parent, background="black", cursor="crosshair")
        self.wf_canvas.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0,5))
        self.wf_canvas.bind("<Configure>", self._wf_on_resize)
        self.wf_canvas_image_id = None
        self.wf_rows = []       # list of raw bin lists (newest first)
        self.wf_tk_image = None

    # ── Pre-built lookup table: index 0-255 -> (R, G, B) thermal colourmap
    _CMAP = None

    @classmethod
    def _build_cmap(cls):
        """Build a 256-entry uint8 RGB lookup table once."""
        if cls._CMAP is not None:
            return
        lut = np.zeros((256, 3), dtype=np.uint8)
        for v in range(256):
            if v < 64:
                t = v / 64.0;  lut[v] = (0, 0, int(255*t))
            elif v < 128:
                t = (v-64)/64.0;  lut[v] = (0, int(255*t), 255)
            elif v < 160:
                t = (v-128)/32.0;  lut[v] = (0, 255, int(255*(1-t)))
            elif v < 200:
                t = (v-160)/40.0;  lut[v] = (int(255*t), 255, 0)
            else:
                t = (v-200)/55.0;  lut[v] = (255, int(255*(1-t)), 0)
        cls._CMAP = lut

    def _wf_poll(self):
        """Drain the WF queue; redraw once if new rows arrived. Runs on GUI thread."""
        got_data = False
        while not self.wf_queue.empty():
            try:
                data = self.wf_queue.get_nowait()
            except queue.Empty:
                break
            try:
                bins = np.array([int(x) for x in data.split(",") if x.strip()], dtype=np.uint8)
            except ValueError:
                continue
            if bins.size == 0:
                continue
            self.wf_bins = bins.size
            self.wf_rows.insert(0, bins)
            if len(self.wf_rows) > self.wf_height:
                self.wf_rows.pop()
            got_data = True

        if got_data or self.wf_dirty:
            self._wf_redraw()
            self.wf_dirty = False
            if self.wf_rows:
                self.wf_info.config(text=f"{self.wf_bins} bins  |  {len(self.wf_rows)} rows")

        self.root.after(33, self._wf_poll)   # ~30 fps cap

    def handle_wf(self, data):
        """Legacy direct call path (fake data generator uses this)."""
        self.wf_queue.put(data)

    def _wf_redraw(self):
        """Render wf_rows to a PIL image and push to canvas. All numpy — no Python pixel loops."""
        if not self.wf_rows:
            return
        cw = self.wf_canvas.winfo_width()
        ch = self.wf_canvas.winfo_height()
        if cw < 2 or ch < 2:
            return

        self._build_cmap()

        lo = self.wf_min_var.get()
        hi = self.wf_max_var.get()
        if hi <= lo:
            hi = lo + 1

        rows_to_show = min(len(self.wf_rows), ch)
        bins = self.wf_bins or 1

        # Stack rows into a 2-D numpy array (rows x bins), uint8
        arr = np.array(self.wf_rows[:rows_to_show], dtype=np.float32)

        # Clip and scale to 0-255
        arr = np.clip((arr - lo) * (255.0 / (hi - lo)), 0, 255).astype(np.uint8)

        # Resize horizontally from bins -> cw using nearest-neighbour index mapping
        x_idx = (np.arange(cw) * bins / cw).astype(np.int32)
        x_idx = np.clip(x_idx, 0, bins - 1)
        arr = arr[:, x_idx]           # shape: (rows_to_show, cw)

        # Apply colourmap: arr indexes into _CMAP -> (rows_to_show, cw, 3)
        rgb = self._CMAP[arr]

        # Convert to PIL and then to ImageTk in one shot
        pil_img = Image.fromarray(rgb, mode="RGB")
        self.wf_tk_image = ImageTk.PhotoImage(pil_img)

        if self.wf_canvas_image_id is None:
            self.wf_canvas_image_id = self.wf_canvas.create_image(0, 0, anchor=tk.NW,
                                                                    image=self.wf_tk_image)
        else:
            self.wf_canvas.itemconfig(self.wf_canvas_image_id, image=self.wf_tk_image)

    def _wf_on_resize(self, event):
        self.wf_dirty = True   # redraw on next poll cycle

    def wf_rescale(self):
        self.wf_dirty = True

    def wf_auto_scale(self):
        """Set min/max from the actual data range."""
        if not self.wf_rows:
            return
        all_vals = [v for row in self.wf_rows for v in row]
        self.wf_min_var.set(min(all_vals))
        self.wf_max_var.set(max(all_vals))
        self._wf_redraw()

    def wf_clear(self):
        self.wf_rows.clear()
        if self.wf_canvas_image_id:
            self.wf_canvas.delete(self.wf_canvas_image_id)
            self.wf_canvas_image_id = None
        self.wf_info.config(text="No data")

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------
    def refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo["values"] = ports
        if self.port_var.get() not in ports and ports:
            self.port_var.set(ports[0])

    def toggle_connect(self):
        if self.connected:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        port = self.port_var.get()
        if not port:
            messagebox.showerror("Error", "Select a serial port first")
            return
        try:
            self.supports_rainscatter = True
            self._sync_decode_mode_controls()
            self.serial_port = serial.Serial(port, 115200, timeout=0.1)
            self.connected = True
            self.connect_btn.config(text="Disconnect")
            self.status_label.config(text="Connected", foreground="green")
            self.config["port"] = port
            # Start read thread
            self.read_thread = threading.Thread(target=self.read_loop, daemon=True)
            self.read_thread.start()
            self.log("[SYS] Connected to " + port, "sys")
            # Send ident immediately to get RDY: version response, then push config
            self.root.after(200, lambda: self.send("CMD:ident"))
            self.root.after(1500, self.push_config)
        except Exception as e:
            messagebox.showerror("Connection Error", str(e))

    def disconnect(self):
        self.connected = False
        if hasattr(self, "acc_panel") and self.acc_panel:
            self.acc_panel.reset()
        if self.serial_port:
            try:
                self.serial_port.close()
            except Exception:
                pass
            self.serial_port = None
        self.connect_btn.config(text="Connect")
        self.status_label.config(text="Disconnected", foreground="red")
        self.tx_mode = False
        self.current_loc = ""
        self.last_decode_tag = None
        self.wf_bins = 0
        self.wf_height = 300    # number of history rows to keep
        self.wf_queue = queue.Queue()  # serial thread -> GUI thread
        self.wf_dirty = False  # tracks when a new message line starts
        if hasattr(self, "remote_fw_label"):
            self.remote_fw_label.config(text="Remote FW: --", foreground="grey")
        self.level_bar["value"] = 0
        self.level_label.config(text="--", foreground="grey")
        self.update_tx_button()
        self.log("[SYS] Disconnected", "sys")

    # ------------------------------------------------------------------
    # Serial read loop (runs in thread)
    # ------------------------------------------------------------------
    def read_loop(self):
        buf = ""
        while self.connected and self.serial_port:
            try:
                data = self.serial_port.read(256).decode("ascii", errors="replace")
                if data:
                    buf += data
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        line = line.strip()
                        if line:
                            self.root.after(0, self.handle_line, line)
            except Exception:
                break

    def handle_line(self, line):
        if line.startswith("RDY:"):
            self.last_decode_tag = None
            self.update_remote_fw(line[4:])
            self.log(f"[SYS] Device ready: {line[4:]}", "sys")
        elif line.startswith("STA:"):
            self.update_status(line[4:])
        elif line.startswith("MSG:"):
            char = line[4:]
            self.append_decode(char, "rx")
        elif line.startswith("TX:"):
            char = line[3:]
            self.append_decode(char, "tx")
        elif line.startswith("ERR:") and len(line) < 10:
            char = line[4:]
            self.last_decode_tag = None
            self.append_decode(char, "err")
        elif line.startswith("JT:"):
            self.last_decode_tag = None
            self.log(f"JT4  {line[3:]}", "jt")
        elif line.startswith("PI:"):
            self.last_decode_tag = None
            self.log(f"PI4  {line[3:]}", "pi")
        elif line.startswith("WF:"):
            self.wf_queue.put(line[3:])
        elif line.startswith("SFT:"):
            if hasattr(self, "acc_panel") and self.acc_panel:
                try:
                    mags = [float(x) for x in line[4:].split(",") if x]
                    if mags:
                        self.acc_panel.push(mags)
                except ValueError:
                    pass
        elif line.startswith("ACK:"):

            self.last_decode_tag = None
            self.bottom_status.config(text=f"✓ {line}")
            ack_cmd = line[4:].strip()
            if self._should_log_ack(ack_cmd):
                self.log(f"[ACK] {ack_cmd}", "sys")
        elif line.startswith("ERR:"):
            self.last_decode_tag = None
            reason = line[4:].strip().lower()
            if "invalid decode mode" in reason:
                self._handle_legacy_decode_mode()
            self.bottom_status.config(text=f"! {line}")
            self.log(f"[ERR] {line[4:]}", "err")

    def _handle_legacy_decode_mode(self):
        """Fallback for older firmware that does not support decode mode 2."""
        self.supports_rainscatter = False
        if int(self.config.get("decmode", 0)) != 0:
            self.config["decmode"] = 0
            self.save_config()
        self._sync_decode_mode_controls()

    def update_remote_fw(self, payload):
        """Display firmware version from RDY payload."""
        text = (payload or "").strip()
        if text:
            self.remote_fw_label.config(text=f"Remote FW: {text[:28]}", foreground="darkgreen")
        else:
            self.remote_fw_label.config(text="Remote FW: --", foreground="grey")

    def update_status(self, payload):
        parts = payload.split(",")
        if len(parts) >= 4:
            time_str = parts[0]
            locator = parts[3] if len(parts) > 3 else ""
            tx_flag = parts[4] if len(parts) > 4 else "0"
            self.gps_label.config(text=f"GPS: {time_str}",
                                  foreground="darkgreen" if time_str != "--:--:--" else "grey")
            self.current_loc = locator
            self.loc_label.config(text=locator)
            self.refresh_qso_buttons()
            new_tx = (tx_flag == "1")
            if new_tx != self.tx_mode:
                self.tx_mode = new_tx
                self.update_tx_button()
            if len(parts) > 5:
                try:
                    level = int(parts[5])
                    self.level_bar["value"] = level
                    if level < 5:
                        color, text = "grey", f"{level}%"
                    elif level > 90:
                        color, text = "red", f"{level}%"
                    elif level > 70:
                        color, text = "orange", f"{level}%"
                    else:
                        color, text = "darkgreen", f"{level}%"
                    self.level_label.config(text=text, foreground=color)
                except ValueError:
                    pass

    # ------------------------------------------------------------------
    # Config push to firmware
    # ------------------------------------------------------------------
    def push_config(self):
        """Send all settings to firmware after connect."""
        if not self.connected:
            return
        self.config_push_in_progress = True
        self.log("[SYS] Pushing config to device…", "sys")
        try:
            self.send(f"SET:loclen:{self.config['loclen']}")
            time.sleep(0.05)
            decmode_to_send = 0 if (not self.supports_rainscatter and int(self.config.get("decmode", 0)) == 2) else int(self.config.get("decmode", 0))
            if decmode_to_send != int(self.config.get("decmode", 0)):
                self.config["decmode"] = 0
                self._sync_decode_mode_controls()
                self.save_config()
            self.send(f"SET:decmode:{decmode_to_send}")
            time.sleep(0.05)
            self.send(f"SET:txadv:{self.config['txadv']}")
            time.sleep(0.05)
            self.send(f"SET:rxret:{self.config['rxret']}")
            time.sleep(0.05)
            self.send(f"SET:halfrate:{self.config['halfrate']}")
            time.sleep(0.05)
            self.send(f"SET:confidence:{self.config['confidence']:.3f}")
            time.sleep(0.05)
            for i, msg in enumerate(self.config["messages"]):
                text = self._render_template(msg)
                self.send(f"SET:msg:{i}:{text}")
                time.sleep(0.05)
        finally:
            self.config_push_in_progress = False
        self.log("[SYS] Config push complete", "sys")

    def _should_log_ack(self, ack_cmd):
        if not ack_cmd:
            return False
        lower = ack_cmd.lower()
        if self.config_push_in_progress:
            return False
        if lower.startswith("set:"):
            return False
        return True

    def send(self, cmd):
        if self.connected and self.serial_port:
            try:
                self.serial_port.write((cmd + "\n").encode("ascii"))
            except Exception as e:
                self.log(f"[ERR] Send failed: {e}", "err")

    # ------------------------------------------------------------------
    # UI actions
    # ------------------------------------------------------------------
    def apply_settings(self):
        # Update config dict from UI
        self.config["loclen"] = int(self.loclen_var.get())
        self.config["decmode"] = 2 if self.dm_combo.current() == 1 else 0
        self._sync_decode_mode_controls()
        self.config["txadv"] = int(self.txadv_var.get())
        self.config["rxret"] = int(self.rxret_var.get())
        self.config["halfrate"] = self.hr_combo.current()
        self.config["confidence"] = round(float(self.confidence_var.get()), 3)
        if hasattr(self, "acc_panel") and self.acc_panel:
            self.acc_panel.set_confidence_threshold(self.config["confidence"])
        new_app = self.app_combo.current()

        if self.connected:
            self.send(f"SET:loclen:{self.config['loclen']}")
            self.send(f"SET:decmode:{self.config['decmode']}")
            self.send(f"SET:txadv:{self.config['txadv']}")
            self.send(f"SET:rxret:{self.config['rxret']}")
            self.send(f"SET:halfrate:{self.config['halfrate']}")
            self.send(f"SET:confidence:{self.config['confidence']:.3f}")
            if new_app != self.config["app"]:
                if messagebox.askyesno("Change App", "Changing app requires a reboot. Continue?"):
                    self.config["app"] = new_app
                    self.send(f"SET:app:{new_app}")
        else:
            self.config["app"] = new_app
        self.save_config()
        self.bottom_status.config(text="Settings applied")

    def _sync_decode_mode_controls(self):
        decmode = int(self.config.get("decmode", 0))
        if hasattr(self, "decmode_main_btn"):
            if not self.supports_rainscatter:
                self.decmode_main_btn.config(text="Decode: Normal", state=tk.DISABLED)
            elif decmode == 2:
                self.decmode_main_btn.config(state=tk.NORMAL)
                self.decmode_main_btn.config(text="Decode: Rainscatter")
            else:
                self.decmode_main_btn.config(state=tk.NORMAL)
                self.decmode_main_btn.config(text="Decode: Normal")
        if hasattr(self, "dm_combo"):
            if not self.supports_rainscatter and decmode == 2:
                self.config["decmode"] = 0
                decmode = 0
            self.dm_combo.current(1 if decmode == 2 else 0)

    def on_main_decode_toggle(self):
        """Main-page quick toggle: Normal (0) <-> Rainscatter (2)."""
        if not self.supports_rainscatter:
            self.bottom_status.config(text="Decode mode: Normal (firmware does not support Rainscatter)")
            return
        self.config["decmode"] = 0 if int(self.config.get("decmode", 0)) == 2 else 2
        self._sync_decode_mode_controls()
        if self.connected:
            self.send(f"SET:decmode:{self.config['decmode']}")
            mode_name = "Rainscatter" if self.config["decmode"] == 2 else "Normal"
            self.bottom_status.config(text=f"Decode mode: {mode_name}")
        self.save_config()

    def save_config_ui(self):
        self.apply_settings()
        self.save_config()
        messagebox.showinfo("Saved", f"Config saved to {CONFIG_FILE}")

    def show_about_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("About OOK48")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()

        frame = ttk.Frame(win, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="OOK48 Serial Control GUI", font=("Segoe UI", 11, "bold")).pack(anchor=tk.W)
        ttk.Label(frame, text="RP2040_OOK48_Headless", foreground="grey").pack(anchor=tk.W, pady=(0, 8))

        ttk.Label(frame, text="Repository:").pack(anchor=tk.W)
        repo_label = ttk.Label(frame, text=REPO_URL, foreground="blue", cursor="hand2")
        repo_label.pack(anchor=tk.W, pady=(0, 8))
        repo_label.bind("<Button-1>", lambda _e: webbrowser.open(REPO_URL))

        ttk.Label(
            frame,
            text="Credits:\n"
                 "• Colin Durbridge (G4EML) — original OOK48 work\n"
                 "• Robin Szemeti (G1YFG) — RP2040_OOK48_Headless serial + GUI adaptation",
            justify=tk.LEFT,
        ).pack(anchor=tk.W)

        ttk.Button(frame, text="Close", command=win.destroy).pack(anchor=tk.E, pady=(10, 0))

    def reboot_device(self):
        if self.connected:
            self.send("CMD:reboot")

    def _upcase_var(self, var):
        """Force a StringVar to uppercase without triggering a recursive trace."""
        val = var.get()
        upper = val.upper()
        if val != upper:
            var.set(upper)

    def _upcase_entry(self, var):
        val = var.get()
        upper = val.upper()
        if val != upper:
            var.set(upper)

    def on_callsign_change(self, *_):
        """Persist callsign edits immediately and keep dependent UI in sync."""
        self._upcase_var(self.callsign_var)
        new_call = self.callsign_var.get().strip().upper()
        self.refresh_qso_buttons()
        if self.config.get("callsign", "") != new_call:
            self.config["callsign"] = new_call
            self.save_config()

    def send_freetext(self):
        """Push free-text entry as a one-shot message in slot 9 and transmit."""
        text = self.freetext_var.get().strip().upper()
        if not text:
            return
        if not self.connected:
            self.bottom_status.config(text="Not connected")
            return
        self.send(f"SET:msg:9:{text}")
        time.sleep(0.05)
        self.send("CMD:txmsg:9")
        time.sleep(0.05)
        self.send("CMD:tx")
        self.active_slot_label.config(text=f"▶ [FT] {text}", foreground="red")
        self.bottom_status.config(text=f"TX free text: {text}")

    def send_dashes(self):
        """Start continuous dashes transmit mode for dish alignment."""
        if not self.connected:
            self.bottom_status.config(text="Not connected")
            return
        self.send("CMD:dashes")
        self.active_slot_label.config(text="▶ [DASHES] ----------------", foreground="red")
        self.bottom_status.config(text="TX dashes: alignment mode")
        self.log("[SYS] Dashes requested (CMD:dashes)", "sys")

    def select_tx_slot(self):
        if self.connected:
            self.send(f"CMD:txmsg:{self.tx_slot_var.get()}")

    def start_tx(self):
        """Start TX on the currently selected slot."""
        if not self.connected:
            return
        slot = self.tx_slot_var.get()
        self.send(f"CMD:txmsg:{slot}")
        time.sleep(0.05)
        self.send("CMD:tx")

    def stop_tx(self):
        if self.connected:
            was_dashes = hasattr(self, "active_slot_label") and "[DASHES]" in self.active_slot_label.cget("text")
            self.send("CMD:rx")
            if was_dashes:
                self.log("[SYS] Stop dashes requested (CMD:rx)", "sys")
            else:
                self.log("[SYS] TX stop requested (CMD:rx)", "sys")

    def toggle_tx(self):
        if self.tx_mode:
            self.stop_tx()
        else:
            self.start_tx()

    def on_slot_double_click(self, slot):
        """Transmit the given slot — push rendered text to firmware first."""
        self.tx_slot_var.set(slot)
        # Find the rendered text for this slot
        rendered = ""
        for s, label, tmpl in self.qso_templates:
            if s == slot:
                rendered = self._render_template(tmpl)
                break
        if not self.connected:
            self.bottom_status.config(text=f"Slot {slot}: {rendered}  (not connected)")
            return
        # Push the rendered message to firmware slot, then start TX
        if rendered:
            self.send(f"SET:msg:{slot}:{rendered}")
            time.sleep(0.05)
        self.send(f"CMD:txmsg:{slot}")
        time.sleep(0.05)
        self.send("CMD:tx")
        self.active_slot_label.config(text=f"▶ [{slot}] {rendered}", foreground="red")
        self.bottom_status.config(text=f"TX: {rendered}")

    def _highlight_active_slot(self, slot):
        if hasattr(self, "active_slot_label"):
            if slot is not None and self.tx_mode:
                rendered = ""
                for s, label, tmpl in self.qso_templates:
                    if s == slot:
                        rendered = self._render_template(tmpl)
                        break
                self.active_slot_label.config(text=f"▶ [{slot}] {rendered}", foreground="red")
            else:
                self.active_slot_label.config(text="", foreground="grey")

    # ------------------------------------------------------------------
    # Contest QSO pad
    # ------------------------------------------------------------------

    def _render_template(self, tmpl):
        """Substitute live QSO fields into a template string."""
        my   = self.callsign_var.get().strip().upper() or "MYCALL"
        them = self.theircall_var.get().strip().upper() or "???"
        ser  = str(self.serial_var.get()).zfill(3)
        loc  = self.current_loc if self.current_loc else "{LOC}"
        return (tmpl
                .replace("{myCall}",    my)
                .replace("{theirCall}", them)
                .replace("{serial}",    ser)
                .replace("{loc}",       loc))

    def _build_qso_buttons(self):
        """Create one button per QSO slot inside qso_btn_frame."""
        for widget in self.qso_btn_frame.winfo_children():
            widget.destroy()
        self.qso_buttons = []
        for slot, label, tmpl in self.qso_templates:
            rendered = self._render_template(tmpl)
            btn = ttk.Button(
                self.qso_btn_frame,
                text=rendered,
                command=lambda s=slot: self.on_slot_double_click(s)
            )
            btn.pack(fill=tk.X, pady=1)
            self.qso_buttons.append((btn, slot, tmpl))

    def refresh_qso_buttons(self):
        """Update button labels whenever QSO fields change."""
        if not hasattr(self, "qso_buttons"):
            return
        for btn, slot, tmpl in self.qso_buttons:
            btn.config(text=self._render_template(tmpl))
        # Keep config messages in sync as templates (not rendered text)
        for slot, label, tmpl in self.qso_templates:
            self.config["messages"][slot] = tmpl

    def increment_serial(self):
        self.serial_var.set(self.serial_var.get() + 1)
        self.config["serial"] = self.serial_var.get()
        self.refresh_qso_buttons()

    def autogenerate_messages(self):
        """Regenerate all slot messages from current QSO fields and push to firmware."""
        call = self.callsign_var.get().strip().upper()
        if not call:
            messagebox.showwarning("Generate", "Enter your callsign first.")
            return
        self.config["callsign"] = call
        self.config["serial"] = self.serial_var.get()
        self.refresh_qso_buttons()
        if self.connected:
            for slot, label, tmpl in self.qso_templates:
                rendered = self._render_template(tmpl)
                self.send(f"SET:msg:{slot}:{rendered}")
                time.sleep(0.05)
        self.save_config()
        self.bottom_status.config(text=f"Messages generated for {call}")

    def update_tx_button(self):
        if self.tx_mode:
            self.stop_btn.config(text="■  STOP TX  (TX active)", state=tk.NORMAL)
        else:
            self.stop_btn.config(text="■  STOP TX", state=tk.NORMAL)
            self._highlight_active_slot(None)

    def on_decode_double_click(self, event):
        """If the double-clicked word is from received (rx) text, treat it as their callsign."""
        idx = self.decode_text.index(f"@{event.x},{event.y}")
        # Check the character under the click carries the rx tag
        tags = self.decode_text.tag_names(idx)
        if "rx" not in tags:
            return "break"
        # Grab the word boundaries around the click position
        word_start = self.decode_text.index(f"{idx} wordstart")
        word_end   = self.decode_text.index(f"{idx} wordend")
        word = self.decode_text.get(word_start, word_end).strip()
        if word:
            self.theircall_var.set(word.upper())
            self.bottom_status.config(text=f"Their call set: {word.upper()}")
        return "break"   # prevent default word-selection behaviour

    def clear_decode(self):
        self.decode_text.delete("1.0", tk.END)
        if hasattr(self, "acc_panel") and self.acc_panel:
            self.acc_panel.reset()
        if self.connected:
            self.send("CMD:clear")

    def on_acc_state_change(self, state_label, state):
        """Mirror accumulator state changes to bottom status bar."""
        if not hasattr(self, "bottom_status"):
            return
        msg_len = state.get("msg_len")
        repeats = state.get("repeats", 0)
        if msg_len:
            self.bottom_status.config(text=f"ACC: {state_label}  L={msg_len}  x{repeats}")
        else:
            self.bottom_status.config(text=f"ACC: {state_label}")

    def save_log(self):
        content = self.decode_text.get("1.0", tk.END)
        path = filedialog.asksaveasfilename(defaultextension=".txt",
                                            filetypes=[("Text files", "*.txt"), ("All", "*.*")])
        if path:
            with open(path, "w") as f:
                f.write(content)

    # ------------------------------------------------------------------
    # Decode display
    # ------------------------------------------------------------------
    def append_decode(self, char, tag):
        """Append a single character to the decode window.
        Inserts a GMT timestamp at the start of each new message.
        <CR> (firmware escape for \\r) is treated as end-of-message."""
        if char == "<CR>":
            self.last_decode_tag = None   # next char gets a fresh timestamp
            return
        display_char = char
        display_tag = tag
        if char in ("<UNK>", "UNK", "~"):
            display_char = "?"
            display_tag = "err"
        if tag != self.last_decode_tag:
            ts = datetime.utcnow().strftime("%H:%M:%S")
            prefix = "RX" if tag == "rx" else "TX" if tag == "tx" else "  "
            self.decode_text.insert(tk.END, f"\n[{ts}z {prefix}] ", "sys")
            self._write_log(f"\n[{ts}z {prefix}] ")
            self.last_decode_tag = tag
        self.decode_text.insert(tk.END, display_char, display_tag)
        self._write_log(display_char)
        self.decode_text.see(tk.END)

    def log(self, msg, tag="sys"):
        """Append a full line to the decode window."""
        ts = datetime.utcnow().strftime("%H:%M:%S")
        self.decode_text.insert(tk.END, f"\n[{ts}z] {msg}", tag)
        self._write_log(f"\n[{ts}z] {msg}")
        self.last_decode_tag = None
        self.decode_text.see(tk.END)


def main():
    root = tk.Tk()
    app = OOK48GUI(root)

    def on_close():
        if app.connected:
            app.disconnect()
        app.save_config()
        if app.log_file:
            app.log_file.close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()