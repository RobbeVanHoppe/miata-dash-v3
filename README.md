# miata-dash-v3

Software for the 1989 Mazda MX-5 NA head unit. Hardware lives in
[miata-hw](https://github.com/RobbeVanHoppe/miata-hw).

```
ME221 ECU ──┐
            ├── CAN (shielded twisted pair) ──┐
ESP32 node ─┘   relays, analog/digital inputs  │
                                               │
GY-GPS6MV2 ── UART ── Raspberry Pi 3B ─────────┘
                      Flask dashboard on 7" 1024x600 touchscreen
                      WiFi = client of phone hotspot (internet)
```

| Folder | What |
|---|---|
| `can/` | CAN message definitions (DBC). `ME1_4.dbc` = ME221 broadcast. |
| `pi/` | Python package `miata_dash` + tests. |
| `esp32/` | ESP32 node firmware (not started). |
| `deploy/` | systemd unit for the Pi. |

## Code layout (`pi/miata_dash`)

| Module | Role |
|---|---|
| `state.VehicleState` | Thread-safe latest value + timestamp of every signal (`ecu.RPM`, `gps.speed_kph`, …). |
| `sources.DataSource` | Base class: background thread, reopens the device after any error. |
| `sources.CanSource` | Decodes CAN with the DBC; every DBC signal becomes `ecu.<name>`. |
| `sources.GpsSource` + `sources.nmea` | Reads NMEA (RMC/VTG/GGA) from the GPS into `gps.*`. |
| `gauges` | What the dashboard shows (signal, unit, display range). |
| `settings.SettingsStore` | User settings as JSON, written atomically (safe on power loss). |
| `web.create_app` | Flask: `/` dashboard, `/settings`, `/api/state` (JSON, polled at 10 Hz). |
| `logger.DriveLogger` | One CSV per drive in `~/miata-logs` (10 Hz, all ECU + GPS signals). Starts when RPM > 0, ends 60 s after the engine stops. |
| `simulator` | Fake ECU (real DBC encoding on a virtual CAN bus) and fake GPS. |
| `app.Application` | Wires it all together. |

Adding a sensor = a new `DataSource` subclass writing into `VehicleState`,
plus a `Gauge` entry. Hiding or showing a gauge (e.g. AFR before the
wideband is fitted) is done on the settings page.

## Run on a PC (no hardware)

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"      # Linux/Pi: .venv/bin/pip
.venv/Scripts/python -m miata_dash --simulate
# open http://127.0.0.1:5000
.venv/Scripts/python -m pytest
```

## Raspberry Pi 3B setup

Raspberry Pi OS Bookworm assumed. Values marked **TBD** depend on parts or
ECU settings that are not decided yet; do not guess them.

1. **GPS UART.** The Pi 3B's good UART (PL011) is used by Bluetooth by default.
   In `/boot/firmware/config.txt`:
   ```
   enable_uart=1
   dtoverlay=disable-bt
   ```
   Then `sudo systemctl disable hciuart`, and in `raspi-config` → Interface →
   Serial Port: login shell **No**, hardware **Yes**. The GPS is then `/dev/serial0`.
   Power the GY-GPS6MV2 from 5 V; check that its TX idles at ~3.3 V first.
2. **CAN.** CAN interface for the Pi: **TBD** (hardware design). For an
   MCP2515-based board:
   ```
   dtparam=spi=on
   dtoverlay=mcp2515-can0,oscillator=<crystal Hz, TBD>,interrupt=<GPIO, TBD>
   ```
   Bring `can0` up at boot, e.g. `/etc/systemd/network/80-can.network`:
   ```
   [Match]
   Name=can0
   [CAN]
   BitRate=500000
   ```
   and `sudo systemctl enable --now systemd-networkd`.
3. **WiFi.** The Pi 3B only has 2.4 GHz WiFi: set the phone hotspot to
   2.4 GHz / "maximise compatibility".
4. **Install.**
   ```bash
   git clone https://github.com/RobbeVanHoppe/miata-dash-v3.git ~/miata-dash-v3
   cd ~/miata-dash-v3 && python -m venv .venv && .venv/bin/pip install -e .
   mkdir -p ~/.config/systemd/user && cp deploy/miata-dash.service ~/.config/systemd/user/
   systemctl --user enable --now miata-dash
   sudo loginctl enable-linger $USER
   ```
5. **Kiosk.** Open `http://127.0.0.1:5000` full-screen in Chromium (`--kiosk`).

## Not done yet

- Safe shutdown after ignition off (needs the power board). Logs need a
  writable partition even if the root filesystem is made read-only.
- Haptic knob next to the display (hardware not designed yet).
- ESP32 ↔ Pi CAN messages (`can/miata.dbc`) and ESP32 firmware.
- Uploading logs to a database, NEO-6M 5 Hz configuration, IMU dead reckoning, extra I²C sensors.
- Warning thresholds per gauge.
