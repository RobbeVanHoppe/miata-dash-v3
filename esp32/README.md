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

**Pin maps are PROVISIONAL** (`*/src/config.h`) until the hardware designs in
miata-hw (`docs/design-main-node.md`, `docs/design-knob-node.md`) are final.

## Safety model (main node)

- With no power, or before the firmware runs, every relay is released, which
  is the factory circuit (board pull-downs, outputs written low first thing).
- Pi-commanded relays (O02–O04) release when `PI_MAIN_CMD` is missing for 1 s.
- Lights-off uses a latching relay: no current while parked. Key-on resets it
  in hardware; the firmware also pulses RESET. Moving the stalk while latched
  gives the lights back.

## Commands

```bash
python -m venv .pio-venv && .pio-venv/Scripts/pip install platformio   # once, from the repo root
cd esp32/main-node && ../../.pio-venv/Scripts/pio run                   # build
cd esp32/bench && ../../.pio-venv/Scripts/pio test                       # unit tests on a connected ESP32
cd esp32/bench && ../../.pio-venv/Scripts/pio run -t upload -t monitor   # CAN self-test
# after changing can/miata.dbc:
.venv/Scripts/python -m cantools generate_c_source can/miata.dbc --database-name miata -o esp32/lib/miata_can/src
```
