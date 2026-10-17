// CAN self-test on a bare ESP32: no transceiver needed.
// TX and RX are routed to the same GPIO, the controller runs in NO_ACK mode
// with self-reception, so every frame we send comes straight back. Each frame
// is packed with the generated miata.c code and unpacked again.

#include <Arduino.h>

#include "driver/twai.h"
#include "miata.h"
#include "soc/gpio_periph.h"
#include "soc/gpio_struct.h"

static constexpr gpio_num_t kLoopPin = GPIO_NUM_17;  // nothing may be connected to it

static bool roundtrip(uint32_t id, const uint8_t* data, uint8_t len, twai_message_t& rx) {
    twai_message_t tx = {};
    tx.identifier = id;
    tx.self = 1;  // self-reception request
    tx.data_length_code = len;
    memcpy(tx.data, data, len);
    if (twai_transmit(&tx, pdMS_TO_TICKS(50)) != ESP_OK) return false;
    return twai_receive(&rx, pdMS_TO_TICKS(50)) == ESP_OK && rx.identifier == id &&
           memcmp(rx.data, data, len) == 0;
}

void setup() {
    Serial.begin(115200);
    delay(1500);
    Serial.println("\n== miata CAN self-test ==");

    twai_general_config_t g = TWAI_GENERAL_CONFIG_DEFAULT(kLoopPin, kLoopPin, TWAI_MODE_NO_ACK);
    twai_timing_config_t t = TWAI_TIMING_CONFIG_500KBITS();
    twai_filter_config_t f = TWAI_FILTER_CONFIG_ACCEPT_ALL();
    bool ok = twai_driver_install(&g, &t, &f) == ESP_OK && twai_start() == ESP_OK;
    Serial.printf("driver start: %s\n", ok ? "OK" : "FAIL");
    // Same pin for TX and RX. The driver leaves the pin input-only (it configures RX last),
    // so turn the output driver back on at register level: gpio_set_direction() would also
    // re-route the pin to plain GPIO and disconnect the TWAI TX signal.
    GPIO.enable_w1ts = 1UL << kLoopPin;
    PIN_INPUT_ENABLE(GPIO_PIN_MUX_REG[kLoopPin]);

    int pass = 0, fail = 0;
    for (int i = 0; i < 20 && ok; i++) {
        miata_main_status_t s = {};
        s.main_ig1 = i & 1;
        s.main_lights_off_latched = (i >> 1) & 1;
        s.main_v_bat = miata_main_status_main_v_bat_encode(12.0 + i * 0.1);
        s.main_counter = i;
        uint8_t buf[MIATA_MAIN_STATUS_LENGTH];
        miata_main_status_pack(buf, &s, sizeof buf);
        twai_message_t rx;
        miata_main_status_t back = {};
        const bool good = roundtrip(MIATA_MAIN_STATUS_FRAME_ID, buf, sizeof buf, rx) &&
                          miata_main_status_unpack(&back, rx.data, rx.data_length_code) == 0 &&
                          back.main_counter == i && back.main_ig1 == (i & 1) &&
                          fabs(miata_main_status_main_v_bat_decode(back.main_v_bat) - (12.0 + i * 0.1)) < 0.006;
        good ? pass++ : fail++;
    }
    twai_status_info_t st;
    twai_get_status_info(&st);
    Serial.printf("MAIN_STATUS round trips: %d pass, %d fail\n", pass, fail);
    Serial.printf("tx_error_counter=%lu rx_error_counter=%lu bus_error_count=%lu\n",
                  (unsigned long)st.tx_error_counter, (unsigned long)st.rx_error_counter,
                  (unsigned long)st.bus_error_count);
    Serial.println(fail == 0 && pass == 20 ? "RESULT: PASS" : "RESULT: FAIL");
}

void loop() { delay(1000); }
