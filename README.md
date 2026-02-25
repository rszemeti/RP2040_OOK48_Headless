# OOK48 Serial Control

A modified version of the RP2040 OOK48 LCD firmware that replaces the touchscreen
GUI with a USB serial interface. The LCD is retained but used exclusively for the
spectrum and waterfall display. All control, configuration, and decode output is
handled via a Python GUI on a connected laptop.

---

## What Changed From the Original

### Removed Entirely
- `config.ino` — replaced by serial `SET:` commands
- `GetApp.ino` — app selection now via `SET:app:`
- `MemPad.ino` — message slot selection now via `CMD:txmsg:`
- `TextPad.ino` — message text entry now via Python GUI
- `USBDrive.ino` — SD card logging is handled by the Python GUI instead
- All SD card hardware support (`SdFat`, `Adafruit_TinyUSB`, SPI1 setup)
- EEPROM read/write — settings are held in RAM and pushed from Python on connect
- Battery voltage measurement and calibration
- All touchscreen UI — buttons, config pages, keyboard, memory pad

### Modified
- `RP2040_OOK48_Serial.ino` — main file rewritten: serial protocol added,
  `defaultSettings()` replaces EEPROM load/save, LCD init simplified
- `globals.h` — SD, battery, and EEPROM-specific variables removed;
  `core0Ready` flag added for core synchronisation
- `GUI.ino` — stripped to spectrum/waterfall/legend drawing only
- `DEFINES.h` — SD card pin definitions removed

### Unchanged
- `DMA.ino`, `FFT.ino`, `Rx.ino`, `Tx.ino`, `JT4Decode.ino`,
  `PI4Decode.ino`, `fano.ino` — all radio processing untouched

---

## How It Works

The RP2040 enumerates as a USB CDC serial device (virtual COM port) when connected
to a laptop. The Python GUI connects to this port at 115200 baud.

On boot the firmware applies safe defaults and sends `RDY:<version>`. The Python
GUI responds by pushing all configuration settings. From that point on, decoded
messages stream out as simple prefixed ASCII lines and the GUI sends commands back
as needed.

The LCD shows the spectrum and waterfall continuously during RX, and a red TX
indicator during transmit — exactly as before, just without anything else on screen.

---

## Serial Protocol

All messages are newline-terminated ASCII at 115200 baud.

### Firmware → PC

| Message | Description |
|---------|-------------|
| `RDY:<version>` | Boot complete, ready for config push |
| `STA:<hh>:<mm>:<ss>,<lat>,<lon>,<locator>,<tx>` | Status line, once per second |
| `MSG:<char>` | OOK48 decoded character (one per message) |
| `ERR:<char>` | OOK48 decode error character |
| `TX:<char>` | Transmitted character echo (one per transmitted character) |
| `JT:<hh>:<mm>,<snr>,<message>` | JT4 decoded message |
| `PI:<hh>:<mm>,<snr>,<message>` | PI4 decoded message |
| `WF:<v0>,<v1>,...,<vN>` | Waterfall line — comma-separated 8-bit magnitudes, one per FFT bin |
| `ACK:<command>` | Command acknowledged |
| `ERR:<reason>` | Command rejected with reason |

### PC → Firmware

| Command | Description |
|---------|-------------|
| `SET:gpsbaud:<9600\|38400>` | Set GPS baud rate |
| `SET:loclen:<6\|8\|10>` | Set Maidenhead locator length |
| `SET:decmode:<0\|1>` | Decode mode: 0=Normal, 1=Alt |
| `SET:txadv:<0-999>` | TX timing advance in ms |
| `SET:rxret:<0-999>` | RX timing retard in ms |
| `SET:halfrate:<0\|1>` | 0=1s character period, 1=2s half-rate |
| `SET:app:<0\|1\|2>` | Select app: 0=OOK48, 1=JT4, 2=PI4 (triggers reboot) |
| `SET:msg:<0-9>:<text>` | Set TX message slot |
| `CMD:tx` | Switch to transmit |
| `CMD:rx` | Switch to receive |
| `CMD:txmsg:<0-9>` | Select active TX message slot |
| `CMD:clear` | No-op, returns ACK |
| `CMD:reboot` | Reboot the device |

---

## Python GUI

Two files are provided:

- **`ook48_gui.py`** — standard GUI without waterfall
- **`ook48_waterfall.py`** — adds a live waterfall display above the decode window

### Requirements
```
pip install pyserial
```

### Running
```
python ook48_gui.py
```
or
```
python ook48_waterfall.py
```

### Features

**Connection bar** — port selector, connect/disconnect, GPS time and locator
displayed live from `STA:` updates.

**Decode / TX tab** — split vertically into RX (left) and TX (right) panes.

- **RX pane** — colour-coded decode output: green=RX, red=TX echo, orange=error,
  dark green=JT4, purple=PI4, grey=system. Each message is prefixed with a UTC
  timestamp when its first character arrives. Double-clicking any received (green)
  word sets it as "Their call" in the TX pane.
  Clear button clears the screen only — the log file is unaffected.
  Save Log writes the current screen content to a file.

- **TX pane — Contest QSO pad** — designed for rapid contest operation:
  - **My call** — your callsign, persisted between sessions
  - **Their call** — type or double-click a received callsign to populate
  - **Serial #** — contest serial number with +1 button, persisted between sessions
  - All fields update the 10 slot buttons live as you type
  - **Single click** on any slot button immediately transmits that slot
  - **■ STOP TX** halts transmission and returns to RX

**TX message slots** — 10 slots are pre-filled by entering your callsign.
The slot layout is designed for a complete contest QSO:

| Slot | Content |
|------|---------|
| 0 | `CQ {myCall}` |
| 1 | `{theirCall} DE {myCall}` |
| 2 | `{theirCall} 59{serial} {loc}` |
| 3 | `{theirCall} 59{serial}` |
| 4 | `{loc}` |
| 5 | `ALL AGN` |
| 6 | `LOC AGN` |
| 7 | `RPT AGN` |
| 8 | `RGR 73` |
| 9 | `{myCall}` |

`{loc}` is substituted with the live GPS locator received from the firmware.
All other substitutions happen in the GUI before the message is sent to the firmware.

**Settings tab** — all firmware parameters with Apply, Save, and Reboot buttons.

**File logging** — everything shown in the decode window is also written to
`ook48_YYYYMMDD.log` in the same directory, opened automatically on startup.
Multiple sessions on the same day append to the same file with a session marker.

### Waterfall (ook48_waterfall.py only)

A waterfall display sits above the decode text in the RX pane. It renders `WF:`
lines from the firmware as a scrolling thermal colour map (black → blue → cyan →
green → yellow → red).

- **Min / Max** spinboxes clip the colour range to the interesting signal level
- **Auto** sets Min/Max from the actual data range
- **Clear WF** clears the waterfall history
- **▶ Fake WF** generates synthetic test data at 9 rows/second — useful for
  testing the display without hardware connected

The waterfall adapts automatically to however many bins the firmware sends and
scales horizontally to fill the available window width.

### Config file (`ook48_config.json`)

Created automatically on first run. Persists port, callsign, serial number, and
all firmware settings between sessions. Example:

```json
{
  "port": "COM3",
  "callsign": "G4EML",
  "serial": 1,
  "gpsbaud": 9600,
  "loclen": 8,
  "decmode": 0,
  "txadv": 0,
  "rxret": 0,
  "halfrate": 0,
  "app": 0,
  "messages": [
    "CQ G4EML",
    "??? DE G4EML",
    "??? 59001 {LOC}",
    ...
  ]
}
```

---

## Notes

**GPS locator** — the locator shown in the connection bar is received from the
firmware via `STA:` lines and is used live in the TX slot buttons. If GPS has not
yet locked, `{LOC}` is passed through to the firmware for its own substitution.

**GPS baud rate** — `autoBaud()` detection is removed. The firmware starts at 9600
by default. If your GPS module runs at 38400, change `gpsbaud` in `ook48_config.json`
before first connect, or set it in the Settings tab and click Apply.

**First connect** — there is a 1.5 second delay after connecting before the config
push is sent, to allow the firmware time to finish booting and send its `RDY:` message.

**App switching** — changing the app via `SET:app:` causes an immediate reboot. The
Python GUI will show a disconnect; simply reconnect after a few seconds.

**TX echo** — transmitted characters are echoed back by the firmware as `TX:` lines
and appear in red in the decode window as they are actually sent, one character at
a time.