#include "canlink.h"

#include <string.h>

#include "driver/twai.h"

namespace canlink {

static bool g_running = false;
static uint32_t g_tx_errors = 0;
static uint32_t g_bus_off = 0;

bool begin(gpio_num_t tx, gpio_num_t rx) {
    twai_general_config_t g = TWAI_GENERAL_CONFIG_DEFAULT(tx, rx, TWAI_MODE_NORMAL);
    g.tx_queue_len = 16;
    g.rx_queue_len = 32;
    twai_timing_config_t t = TWAI_TIMING_CONFIG_500KBITS();  // ME221 bus rate
    twai_filter_config_t f = TWAI_FILTER_CONFIG_ACCEPT_ALL();
    if (twai_driver_install(&g, &t, &f) != ESP_OK) return false;
    g_running = twai_start() == ESP_OK;
    return g_running;
}

void end() {
    if (g_running) twai_stop();
    twai_driver_uninstall();
    g_running = false;
}

bool send(uint32_t id, const uint8_t* data, uint8_t len) {
    if (!g_running) return false;
    twai_message_t msg = {};
    msg.identifier = id;
    msg.data_length_code = len > 8 ? 8 : len;
    memcpy(msg.data, data, msg.data_length_code);
    if (twai_transmit(&msg, 0) != ESP_OK) {
        g_tx_errors++;
        return false;
    }
    return true;
}

bool receive(Frame& out) {
    if (!g_running) return false;
    twai_message_t msg;
    if (twai_receive(&msg, 0) != ESP_OK) return false;
    if (msg.extd || msg.rtr) return false;  // we only use standard data frames
    out.id = msg.identifier;
    out.len = msg.data_length_code;
    memcpy(out.data, msg.data, out.len);
    return true;
}

void service() {
    twai_status_info_t s;
    if (twai_get_status_info(&s) != ESP_OK) return;
    if (s.state == TWAI_STATE_BUS_OFF) {
        g_bus_off++;
        twai_initiate_recovery();  // waits for 128 x 11 recessive bits, then STOPPED
    } else if (s.state == TWAI_STATE_STOPPED && g_running) {
        twai_start();
    }
}

uint32_t tx_errors() { return g_tx_errors; }

uint32_t bus_off_count() { return g_bus_off; }

}  // namespace canlink
