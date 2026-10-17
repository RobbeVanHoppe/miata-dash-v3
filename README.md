# miata-dash-v3

Software for the 1989 Mazda MX-5 NA head unit. Hardware lives in
[miata-hw](https://github.com/RobbeVanHoppe/miata-hw).

```
ME221 ECU ─── CAN 500 kbit/s (shielded twisted pair) ───┬──────────────┬── main node (ESP32)
(end 1)                                                 │              │   relays, inputs, lights-off
                                                        │              │   (end 2, 120 Ω)
GY-GPS6MV2 ── UART ─┐                                   │              │
MPU6050 ──── I²C ───┼── Raspberry Pi 3B ── MCP2518FD ───┘   knob node (ESP32, haptic BLDC)
RTL-SDR ──── USB ───┘   Flask dashboard, 7" 1024x600 touch
                        WiFi = client of phone hotspot
Power supply board: supervisor starts the Pi after the engine runs, shuts it down cleanly.
```

| Folder | What |
|---|---|
| `can/` | `ME1_4.dbc` = ME221 broadcast; `miata.dbc` = our nodes (Pi, main node, knob). |
| `pi/` | Python package `miata_dash` + tests. |
| `esp32/` | ESP32 firmware for the main node and the knob node. |
| `deploy/` | Pi config files: boot overlays, CAN interface, service, kiosk, RTL-SDR driver blacklist. |

## Code layout (`pi/miata_dash`)

| Module | Role |
|---|---|
| `state.VehicleState` | Thread-safe latest value + timestamp of every signal (`ecu.RPM`, `gps.speed_kph`, `net.Main_IG1`, …). |
| `sources.DataSource` | Base class: background thread, reopens the device after any error. |
| `sources.CanSource` | Decodes CAN with both DBCs (`ecu.*`, `net.*`), per-message listeners, sending. |
| `sources.GpsSource` + `nmea` + `ubx` | NEO-6M: NMEA in, UBX setup out (RMC+GGA at 5 Hz). |
| `sources.imu` | MPU6050 at 100 Hz, mounting axes + automatic levelling. |
| `fusion.SpeedEstimator` | GPS/IMU Kalman filter: `ins.speed_kph`, bridges GPS dropouts ("DR"). |
| `radio.RadioController` | RTL-SDR: `rtl_fm` → `aplay`, bands, squelch, presets, FM scan (`rtl_power`). |
| `knob.KnobController` | Knob turns/presses → page, layout, radio tuning; haptic profile per page. |
| `node_link.PiNodeLink` | Pi frames on CAN: relay requests + heartbeat, status, knob profile. |
| `ui.UiState` | Which page is on screen; lets the knob navigate the browser. |
| `gauges` | What the dashboard shows (signals with fallbacks, ranges, dial ticks). |
| `settings.SettingsStore` | User settings as JSON, written atomically (safe on power loss). |
| `logger.DriveLogger` | One CSV per drive (10 Hz, ECU + GPS + INS). Starts at RPM > 0, ends 60 s after the engine stops. |
| `web.create_app` | Flask pages: Dash (digital / retro / motorsport), Radio, Logs, Settings + JSON APIs. |
| `simulator` | Fake ME221, main node, GPS (with tunnels), MPU6050 and radio. |
| `app.Application` | Wires it together; SIGTERM → clean stop (log flushed, shutdown announced on CAN). |

## Run on a PC (no hardware)

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"      # Linux/Pi: .venv/bin/pip
.venv/Scripts/python -m miata_dash --simulate
# open http://127.0.0.1:5000
.venv/Scripts/python -m pytest
```

## Raspberry Pi 3B setup

Raspberry Pi OS Bookworm (desktop, for the kiosk browser). Values marked
**TBD** depend on parts that are not chosen yet; do not guess them.

1. **Boot config.** Append `deploy/boot-config.txt` to `/boot/firmware/config.txt`.
   It moves Bluetooth off the good UART (GPS on `/dev/serial0`), enables I²C
   (MPU6050) and SPI + the MCP2518FD overlay (interrupt GPIO **TBD** per HAT),
   and sets up the power-supervisor handshake: GPIO17 = shutdown request
   (`gpio-shutdown`), GPIO26 = "halted" (`gpio-poweroff`).
   Then `sudo systemctl disable hciuart`, and `raspi-config` → Interface →
   Serial Port: login shell **No**, hardware **Yes**.
2. **CAN.** `sudo cp deploy/80-can.network /etc/systemd/network/` and
   `sudo systemctl enable --now systemd-networkd` (500 kbit/s).
3. **RTL-SDR.** `sudo apt install rtl-sdr alsa-utils` and
   `sudo cp deploy/blacklist-rtl.conf /etc/modprobe.d/`. The ALSA output device
   is a setting (`radio_audio_device`); the path to the car speakers is **TBD**.
4. **WiFi.** The Pi 3B only has 2.4 GHz WiFi: set the phone hotspot to
   2.4 GHz / "maximise compatibility".
5. **Writable data partition.** Make a small ext4 partition mounted at `/data`
   (settings + drive logs), then turn on the read-only overlay for the root
   filesystem (`raspi-config` → Performance → Overlay File System). Power cuts
   then cannot corrupt the system; only `/data` is written, and the app writes it
   safely (atomic settings, fsync'd logs).
6. **Install.**
   ```bash
   sudo mkdir -p /data/miata-dash /data/miata-logs && sudo chown -R $USER /data
   git clone https://github.com/RobbeVanHoppe/miata-dash-v3.git ~/miata-dash-v3
   cd ~/miata-dash-v3 && python -m venv .venv && .venv/bin/pip install -e .
   mkdir -p ~/.config/systemd/user && cp deploy/miata-dash.service ~/.config/systemd/user/
   systemctl --user enable --now miata-dash
   sudo loginctl enable-linger $USER
   ```
7. **Kiosk.** `mkdir -p ~/.config/autostart && cp deploy/kiosk.desktop ~/.config/autostart/`
   (waits for the dashboard, then opens Chromium full-screen).

## Not done yet

- ESP32 firmware for the main node and knob (pin maps follow the hardware designs).
- Uploading logs to a database; extra I²C sensors; warning thresholds per gauge.
- Car audio path for the radio (TBD).
