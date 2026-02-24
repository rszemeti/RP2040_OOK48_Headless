# OOK48 Serial Control Version

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
| `MSG:<char>` | OOK48 decoded character |
| `ERR:<char>` | OOK48 decode error character |
| `TX:<char>` | Transmitted character echo |
| `JT:<hh>:<mm>,<snr>,<message>` | JT4 decoded message |
| `PI:<hh>:<mm>,<snr>,<message>` | PI4 decoded message |
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
| `SET:msg:<0-9>:<text>` | Set TX message slot (use `{LOC}` for locator token) |
| `CMD:tx` | Switch to transmit |
| `CMD:rx` | Switch to receive |
| `CMD:txmsg:<0-9>` | Select active TX message slot |
| `CMD:clear` | No-op, returns ACK |
| `CMD:reboot` | Reboot the device |

---

## Python GUI

### Requirements
```
pip install pyserial
```

### Running
```
python ook48_gui.py
```

### Features
- Auto-detects available serial ports
- Pushes full config to firmware on connect
- Settings saved to `ook48_config.json` between sessions — edit directly if preferred
- **Decode tab** — colour-coded output: blue=RX, red=TX echo, orange=error,
  green=JT4, purple=PI4; Save Log button writes session text to a local file
- **Settings tab** — all firmware parameters with Apply and Save buttons
- **TX Messages tab** — 10 message slots, supports `{LOC}` token for auto locator
- TX start/stop and message slot selection

### Config file (`ook48_config.json`)
Created automatically on first run. Example:
```json
{
  "port": "COM3",
  "gpsbaud": 9600,
  "loclen": 8,
  "decmode": 0,
  "txadv": 0,
  "rxret": 0,
  "halfrate": 0,
  "app": 0,
  "messages": [
    "G4EML IO91",
    "G4EML {LOC}",
    "EMPTY",
    ...
  ]
}
```

---

## Notes

**GPS baud rate** — `autoBaud()` detection is removed. The firmware starts at 9600
by default. If your GPS module runs at 38400, change `gpsbaud` in `ook48_config.json`
before first connect, or set it in the Settings tab and click Apply.

**First connect** — there is a 1.5 second delay after connecting before the config
push is sent, to allow the firmware time to finish booting and send its `RDY:` message.

**App switching** — changing the app via `SET:app:` causes an immediate reboot. The
Python GUI will show a disconnect; simply reconnect after a few seconds.

**Touch calibration** — the touchscreen calibration data fields are still present in
the settings struct for potential future use but the touch hardware is not used in
this version.
