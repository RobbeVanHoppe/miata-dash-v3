# ESP32 firmware

PlatformIO projects (Arduino framework on ESP32, platform `espressif32@6.9.0`).

| Folder | What |
|---|---|
| `main-node/` | Car-state master under the driver-side dash: inputs (IG1, headlight stalk, reverse, battery), relay coil drivers, auto lights-off with the latching relay, Pi heartbeat watch, deep sleep with the key off. |
| `knob-node/` | Haptic knob behind the dash: SimpleFOC BLDC + AS5600, haptic profile from the Pi, knob events to the Pi, manual Pi power-on (PI_ON_REQ) while the Pi is off. |
| `bench/` | Tests on a bare ESP32: unit tests of the logic (`pio test`) and a CAN self-test without transceiver. |
| `lib/miata_can/` | **Generated** from `../can/miata.dbc` — do not edit; regenerate (below). |
| `lib/canlink/` | TWAI (CAN) driver wrapper: 500 kbit/s, bus-off recovery. |
| `lib/node_logic/` | Hardware-free logic (lights-off state machine, debounce, heartbeat, haptics, power-on request). |

**Pin maps:** the main node follows miata-hw `docs/design-main-node.md` §13
(final). The knob node is still PROVISIONAL until `docs/design-knob-node.md` exists.

## Safety model (main node)

- Before the firmware runs, and when it is dead, every relay is released: the
  factory circuit (board pull-downs; outputs written low before becoming outputs).
- Pi-commanded relays (O02–O04) need a live Pi heartbeat (3 consecutive
  `PI_MAIN_CMD` frames with an advancing counter), drop 1 s after it stops,
  and drop at key-off.
- Lights-off uses latching relay RLY1: no current while parked. Every pulse is
  verified on the relay's feedback contact (3 attempts). Key-on resets RLY1 in
  hardware; the firmware checks after 200 ms and resets it if needed (fault bit 2).
  Moving the stalk off HEAD while latched resets it (owner override).

## Tests on a bare ESP32 (nothing wired to it)

| What | Result (ESP32-WROOM-32D, 2026-10-18) |
|---|---|
| `bench` Unity tests of `node_logic` / `knob_logic` | 15/15 pass |
| `bench` CAN self-test (TWAI loopback on one pin, generated pack/unpack) | 20/20 frames, 0 bus errors |
| `tools/bench_main_node.py` on the `main-node-bench` build | 17/17 checks pass |

## Commands (Windows dev machine)

PlatformIO data lives on `D:\platformio` (C: is nearly full). PlatformIO's own
upload and error output fail silently here, so use the helpers:

```bash
python -m venv .pio-venv && .pio-venv/Scripts/pip install platformio        # once
python tools/pio_build.py esp32/main-node                                    # build (shows real compiler errors)
.pio-venv/Scripts/python tools/flash_esp32.py D:/platformio/build/main-node/main-node-bench --port COM10 --core D:/platformio
python tools/bench_main_node.py --port COM10                                 # hardware-in-the-loop scenarios
# after changing can/miata.dbc:
python -m cantools generate_c_source can/miata.dbc --database-name miata -o esp32/lib/miata_can/src
```
