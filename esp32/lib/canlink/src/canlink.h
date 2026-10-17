// Thin wrapper around the ESP32 TWAI (CAN) driver, shared by both nodes.
// 500 kbit/s, standard IDs, accept-all filter (the bus carries < 300 frames/s).
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "driver/gpio.h"

namespace canlink {

struct Frame {
    uint32_t id;
    uint8_t len;
    uint8_t data[8];
};

// Installs and starts the driver. Returns false if the driver could not start.
bool begin(gpio_num_t tx, gpio_num_t rx);

// Stops and uninstalls the driver (before deep sleep, so the transceiver can be powered down).
void end();

// Non-blocking send; returns false if the TX queue is full or the bus is off.
bool send(uint32_t id, const uint8_t* data, uint8_t len);

// Non-blocking receive.
bool receive(Frame& out);

// Call regularly: recovers from bus-off (e.g. after a wiring fault) and restarts.
void service();

// Frames dropped because the TX queue was full or the bus was off.
uint32_t tx_errors();

}  // namespace canlink
