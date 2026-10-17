# CAN definitions

| File | Sender | Contents |
|---|---|---|
| `ME1_4.dbc` | ME221 | Broadcast, IDs 0x300–0x307 (768–775). Also `ME1_In_1` (0x340 / 832): vehicle speed *into* the ECU. Requires ME firmware ≥ 3.7.3. |
| `miata.dbc` | ESP32 / Pi | Not written yet. Must not reuse any ME221 ID. |

Bus: 500 kbit/s (set in MEITE). Also set in MEITE: which frames are sent and
how often. The ME221 has internal 120 Ω termination (121 Ω measured), so it
must sit at one physical end of the bus.

The Pi can send GPS speed to the ECU on 0x340 (the car has no VSS):
`EcuSpeedSender`, off by default, switched on the settings page. It sends at
10 Hz only while the GPS has a fresh fix and goes silent during a dropout.
What the ME221 does when that input goes quiet is **TBD** — check in MEITE
before relying on it for anything the engine depends on.
