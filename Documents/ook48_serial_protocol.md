# OOK48 GUI ↔ Firmware Serial Protocol (as implemented in GUI)

This document defines the serial protocol behavior used by `ook48_gui.py`.
It is written from the current GUI implementation and intended for firmware alignment.

## 1) Transport

- Physical/link: USB CDC serial port
- Baud: `115200`
- Framing: ASCII text lines
- Line terminator (host → firmware): `\n` (LF)
- Line terminator (firmware → host): `\n` expected
- GUI read behavior:
  - Reads bytes, decodes ASCII with replacement (`errors="replace"`)
  - Splits on `\n`
  - Applies `strip()` to each line before parsing

### Important implications

- Any leading/trailing spaces in firmware lines are removed by GUI before parse.
- Empty lines are ignored.
- Unknown prefixes are ignored silently.

---

## 2) Host → Firmware Commands

All commands are sent as one ASCII line plus trailing LF.

## 2.1 `SET:` commands

### `SET:gpsbaud:<value>`
- Values sent by GUI: `9600` or `38400`

### `SET:loclen:<value>`
- Values sent by GUI: `6`, `8`, or `10`

### `SET:decmode:<value>`
- Values sent by GUI:
  - `0` = Normal (per-symbol max across tone search bins)
  - `1` = Alt (single best bin tracked across symbol cache)
  - `2` = Rainscatter (per-symbol wideband power across full OOK band)

### `SET:txadv:<value>`
- Integer milliseconds, GUI range `0..999`

### `SET:rxret:<value>`
- Integer milliseconds, GUI range `0..999`

### `SET:halfrate:<value>`
- Values sent by GUI: `0` (normal), `1` (half-rate)

### `SET:app:<value>`
- Integer app index
- GUI app indices currently:
  - `0` = OOK48
  - `1` = JT4G Decoder
  - `2` = PI4 Decoder

### `SET:msg:<slot>:<text>`
- `slot`: currently `0..9`
- `text`: message body (ASCII expected)
- GUI sends this in several paths:
  - Config push after connect (all slots)
  - Autogenerate/update slot templates
  - Immediate pre-TX overwrite for selected slot
  - Free-text TX path uses slot `9`

Notes:
- During config push, GUI removes `\r` and `\n` from stored text before sending.
- In some live update paths, GUI sends rendered text without explicit CR suffix.

## 2.2 `CMD:` commands

### `CMD:txmsg:<slot>`
- Select active TX message slot

### `CMD:tx`
- Enter TX mode/start transmit

### `CMD:rx`
- Return to RX mode/stop transmit

### `CMD:dashes`
- Start continuous plain CW dash keying for antenna/dish alignment

### `CMD:clear`
- Clear firmware-side decode/log state (if supported)

### `CMD:reboot`
- Reboot firmware/device

---

## 3) Firmware → Host Telemetry/Events

Firmware should emit one line per event with one of these prefixes.

### `RDY:<text>`
- Device-ready indication
- GUI logs as system message
- Typical use: handshake point before GUI config push

Recommended version payload (for firmware team):
- Include semantic firmware version in `RDY:` as key/value text.
- Preferred format:
  - `RDY:fw=1.4.2;proto=1.0;board=RP2040`
- Minimum recommended key:
  - `fw=<semver>`
- Backward compatibility:
  - GUI currently treats `RDY:` as opaque text, so adding these keys is safe.

### `STA:<csv>`
- Status CSV parsed as:
  - `parts[0]` = GPS time string (displayed)
  - `parts[3]` = locator string (displayed + used in message templates)
  - `parts[4]` = TX flag (`"1"` means TX active, anything else RX)
- `parts[1]` and `parts[2]` are currently ignored by GUI
- Minimum useful length: at least 4 CSV fields

### `MSG:<char_or_token>`
- RX decoded character stream element
- Special token recognized: `<CR>` means end-of-message boundary in GUI

### `TX:<char_or_token>`
- TX character stream element
- Special token recognized: `<CR>` means end-of-message boundary in GUI

### `ERR:<payload>`
- Two GUI behaviors:
  1. If full line length `< 10` chars: treated like single decode character (legacy char error path)
  2. Otherwise: treated as error/status line and logged

### `ACK:<text>`
- Acknowledgement/status line shown in GUI bottom status

### `JT:<text>`
- JT4 decoder log line

### `PI:<text>`
- PI4 decoder log line

### `WF:<csv_ints>`
- Waterfall row values
- CSV integers expected (typical scale 0..255)
- One line = one row

### `SFT:<csv_floats>`
- Soft-magnitude vector for accumulator panel
- CSV floats parsed by GUI and fed to accumulator
- Invalid float rows are dropped silently

---

## 4) Expected Startup / Runtime Sequence

## 4.1 On connect

1. GUI opens serial at `115200`.
2. GUI waits ~1.5 s.
3. GUI sends configuration burst:
   - `SET:gpsbaud`
   - `SET:loclen`
   - `SET:decmode`
   - `SET:txadv`
   - `SET:rxret`
   - `SET:halfrate`
   - `SET:msg:0..9`
4. GUI inserts ~50 ms between each command.

## 4.2 Typical TX start

1. Optional slot text refresh: `SET:msg:<slot>:<rendered>`
2. `CMD:txmsg:<slot>`
3. `CMD:tx`

## 4.3 Typical TX stop

1. `CMD:rx`

---

## 5) Field/Data Constraints for Firmware

- Keep all outbound telemetry ASCII-safe.
- Terminate every telemetry/event line with `\n`.
- Avoid depending on preserved leading/trailing spaces in host parser.
- For `STA:`, always include at least fields `0`, `3`, and `4`:
  - Example: `STA:12:34:56,0,0,IO91,0`
- For `MSG:` / `TX:`, emit `<CR>` token to indicate message boundary.
- For `WF:` / `SFT:`, comma-separated numeric lists only.

---

## 6) Current Known Ambiguities (for firmware-team agreement)

1. `ERR:` dual use (short char-like vs long error line) is legacy and length-based.
2. `SET:msg` CR policy is not fully uniform across all GUI send paths.
3. `STA:` fields 1 and 2 are currently undefined by GUI and can be versioned by firmware.

Suggested resolution for protocol vNext:
- Make `ERR:` strictly line-level and add a dedicated char-error prefix if needed.
- Define fixed message-slot semantics and explicit EOM policy.
- Version `STA:` field map in a companion `RDY:` capability string.

---

## 7) Firmware Version in Spec (new)

To support support/debug workflows, firmware should expose its build/version on connect.

Normative recommendation:
- Firmware MUST emit version metadata in the first `RDY:` after boot.
- Firmware SHOULD include:
  - `fw` (semantic version, e.g. `1.4.2`)
  - `proto` (protocol version implemented, e.g. `1.0`)
  - `git` (short commit hash, optional)
  - `build` (UTC build stamp, optional)

Example:
- `RDY:fw=1.4.2;proto=1.0;git=a1b2c3d;build=2026-02-26T18:20:00Z`

Compatibility note:
- Existing GUI requires no parser change because it logs all `RDY:` payload text verbatim.
