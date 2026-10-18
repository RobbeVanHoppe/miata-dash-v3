# Power supervisor firmware (ATtiny84A on the power supply board)

Decides when the Pi gets power, asks it to shut down, and cuts power only after
it has halted (miata-hw `docs/design-power.md` §6 and §10, rev 3).

- `lib/supervisor_logic/` — the state machine, integer-only C++ with no AVR
  dependencies. It is unit-tested on the ESP32 bench (`esp32/bench`, 8 tests).
- `src/main.cpp` — register-level avr-libc: 1 ms Timer0 tick, external 2.500 V
  ADC reference, outputs active-high, power-down sleep with PCINT wake on
  IGN/ACC, 2 s watchdog while running, state restored after WDT/BOD resets.

| State | Pi 5 V + hold-up | Shutdown request | Leaves when |
|---|---|---|---|
| Off | off | – | IG1 on → WaitRun; knob request (key on, ≥ 12.1 V) → OnManual |
| WaitRun | off | – | battery > 13.2 V [P] for 3 s (engine running) → On |
| On / OnManual | on | – | key off 10 s or battery < 11.8 V [P] for 30 s → Shutdown |
| Shutdown | on | yes | Pi reports halted for 2 s → PostWait; 5 min → forced cut |
| PostWait | on | yes | after 10 s → Off |

Build: `python tools/pio_build.py supervisor` (1.9 KB of 8 KB flash).
Program with a 3.3 V ISP (usbasp) and set the fuses (`pio run -t fuses`);
unplug the Pi and the knob first, because MOSI/MISO drive their lines.
