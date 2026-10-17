# ESP32 node firmware

Not started. Waits for the hardware design in
[miata-hw](https://github.com/RobbeVanHoppe/miata-hw).

Role: read analog/digital car inputs, drive relays, talk to the Pi over CAN
(TWAI) using messages defined in `../can/miata.dbc`. Relays must go to a
safe state on their own if no command arrives from the Pi for a set time.
