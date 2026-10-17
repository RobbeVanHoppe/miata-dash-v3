// Main node firmware: reads car inputs, drives the relays, runs auto lights-off,
// talks CAN (can/miata.dbc), deep-sleeps with the key off.
//
// Safety model (owner rule): every output's de-energised state is the factory
// circuit. Outputs start OFF at boot, Pi-commanded relays release when the Pi
// heartbeat (PI_MAIN_CMD) stops, and a dead node leaves the car stock.

#include <Arduino.h>
#include <esp_sleep.h>

#include "canlink.h"
#include "config.h"
#include "miata.h"
#include "node_logic.h"
#ifdef MAIN_BENCH
#include "bench_io.h"
#endif

using logic::Debounce;
using logic::Heartbeat;
using logic::LightsOff;
using logic::Pulse;

// Survive deep sleep (not a power loss): auto lights-off state and the Pi's last enable.
RTC_DATA_ATTR static LightsOff::Snapshot rtc_lights = {LightsOff::State::Idle, false, false};
RTC_DATA_ATTR static bool rtc_auto_enabled = true;  // default on until the Pi says otherwise
RTC_DATA_ATTR static bool rtc_valid = false;

static Debounce ig1(cfg::kDebounceMs), head(cfg::kDebounceMs), reverse_in(cfg::kDebounceMs);
static Heartbeat pi(cfg::kPiTimeoutMs);
static LightsOff lights;
static Pulse coil_set(cfg::kCoilPulseMs), coil_reset(cfg::kCoilPulseMs);
static bool relay_req[3] = {false, false, false};
static bool auto_enabled = true;
static uint8_t counter = 0;
static uint32_t last_status = 0, last_activity = 0;

static const gpio_num_t kRelayPins[3] = {cfg::kRelayO02, cfg::kRelayO03, cfg::kRelayO04};

static bool read_input(gpio_num_t pin, bool active_high) {
#ifdef MAIN_BENCH
    if (pin == cfg::kIg1) return bench::ig1;
    if (pin == cfg::kHead) return bench::head;
    if (pin == cfg::kReverse) return bench::rev;
#endif
    return digitalRead(pin) == (active_high ? HIGH : LOW);
}

static float read_vbat() {
    if (cfg::kVbatDivider <= 0.0f) return 0.0f;
    return analogReadMilliVolts(cfg::kVbatPin) / 1000.0f / cfg::kVbatDivider;
}

static void outputs_off() {
    for (gpio_num_t p : kRelayPins) digitalWrite(p, LOW);
    digitalWrite(cfg::kRly1Set, LOW);
    digitalWrite(cfg::kRly1Reset, LOW);
}

static void handle_frame(const canlink::Frame& f, uint32_t now) {
    if (f.id == MIATA_PI_MAIN_CMD_FRAME_ID) {
        miata_pi_main_cmd_t m;
        if (miata_pi_main_cmd_unpack(&m, f.data, f.len) != 0) return;
        relay_req[0] = m.pi_relay_req_o02;
        relay_req[1] = m.pi_relay_req_o03;
        relay_req[2] = m.pi_relay_req_o04;
        auto_enabled = m.pi_auto_lights_off_en;
        pi.feed(now);
    }
}

static void send_status(uint32_t now, float vbat) {
    const bool pi_ok = pi.alive(now);
    miata_main_status_t s = {};
    s.main_ig1 = ig1.state();
    s.main_head_switch = head.state();
    s.main_reverse = reverse_in.state();
    s.main_lights_off_latched = lights.latched();
    s.main_relay_o02 = pi_ok && relay_req[0];
    s.main_relay_o03 = pi_ok && relay_req[1];
    s.main_relay_o04 = pi_ok && relay_req[2];
    s.main_pi_timeout = !pi_ok;
    s.main_faults = 0;
    s.main_v_bat = miata_main_status_main_v_bat_encode(vbat);
    s.main_counter = counter++;
    uint8_t buf[MIATA_MAIN_STATUS_LENGTH];
    if (miata_main_status_pack(buf, &s, sizeof buf) > 0) canlink::send(MIATA_MAIN_STATUS_FRAME_ID, buf, sizeof buf);
}

static void go_to_sleep() {
#ifdef MAIN_BENCH
    // Bench: report instead of sleeping (deep sleep would drop the USB serial link).
    static bool reported = false;
    if (!reported) Serial.printf("T=%lu EVENT sleep\n", (unsigned long)millis());
    reported = true;
    return;
#endif
    rtc_lights = lights.save();
    rtc_auto_enabled = auto_enabled;
    rtc_valid = true;
    outputs_off();
    canlink::end();
    digitalWrite(cfg::kCanPower, LOW);
    for (gpio_num_t p : kRelayPins) gpio_hold_en(p);  // keep coil drivers off while asleep
    gpio_hold_en(cfg::kRly1Set);
    gpio_hold_en(cfg::kRly1Reset);
    gpio_hold_en(cfg::kCanPower);
    gpio_deep_sleep_hold_en();
    // Wake on key-on, or on any stalk movement (needed for the lights-off override).
    esp_sleep_enable_ext1_wakeup(1ULL << cfg::kIg1,
                                 cfg::kIg1ActiveHigh ? ESP_EXT1_WAKEUP_ANY_HIGH : ESP_EXT1_WAKEUP_ALL_LOW);
    esp_sleep_enable_ext0_wakeup(cfg::kHead, digitalRead(cfg::kHead) == HIGH ? 0 : 1);
    esp_deep_sleep_start();
}

void setup() {
    for (gpio_num_t p : kRelayPins) {
        gpio_hold_dis(p);
        pinMode(p, OUTPUT);
    }
    for (gpio_num_t p : {cfg::kRly1Set, cfg::kRly1Reset, cfg::kCanPower}) {
        gpio_hold_dis(p);
        pinMode(p, OUTPUT);
    }
    gpio_deep_sleep_hold_dis();
    outputs_off();
#ifdef MAIN_BENCH
    Serial.begin(115200);
    Serial.printf("T=%lu BOOT main-node bench\n", (unsigned long)millis());
#endif
    pinMode(cfg::kIg1, INPUT);  // GPIO34-39: input only, pulls are on the board
    pinMode(cfg::kHead, INPUT);
    pinMode(cfg::kReverse, INPUT);
    analogSetPinAttenuation(cfg::kVbatPin, ADC_11db);

    const uint32_t now = millis();
    // Seed the debouncers with the current levels so wake-up does not look like an edge.
    ig1 = Debounce(cfg::kDebounceMs, read_input(cfg::kIg1, cfg::kIg1ActiveHigh));
    head = Debounce(cfg::kDebounceMs, read_input(cfg::kHead, cfg::kHeadActiveHigh));
    reverse_in = Debounce(cfg::kDebounceMs, read_input(cfg::kReverse, cfg::kReverseActiveHigh));
    if (rtc_valid) {
        lights.restore(rtc_lights);
        auto_enabled = rtc_auto_enabled;
    }

    digitalWrite(cfg::kCanPower, HIGH);
    delay(2);  // transceiver start-up
#ifndef MAIN_BENCH  // bench: no transceiver; Pi frames are injected below
    canlink::begin(cfg::kCanTx, cfg::kCanRx);
#endif
    last_activity = now;
}

void loop() {
    const uint32_t now = millis();
    canlink::service();

    canlink::Frame f;
    while (canlink::receive(f)) handle_frame(f, now);
#ifdef MAIN_BENCH
    bench::poll_serial();
    static uint32_t last_pi = 0;
    static uint8_t pi_counter = 0;
    if (bench::pi_on && logic::elapsed(now, last_pi) >= 100) {  // the Pi sends PI_MAIN_CMD at 10 Hz
        last_pi = now;
        miata_pi_main_cmd_t m = {};
        m.pi_relay_req_o02 = bench::req[0];
        m.pi_relay_req_o03 = bench::req[1];
        m.pi_relay_req_o04 = bench::req[2];
        m.pi_auto_lights_off_en = bench::auto_en;
        m.pi_counter = pi_counter++;
        f.id = MIATA_PI_MAIN_CMD_FRAME_ID;
        f.len = MIATA_PI_MAIN_CMD_LENGTH;
        miata_pi_main_cmd_pack(f.data, &m, sizeof f.data);
        handle_frame(f, now);
    }
#endif

    const bool ig1_on = ig1.update(now, read_input(cfg::kIg1, cfg::kIg1ActiveHigh));
    const bool head_on = head.update(now, read_input(cfg::kHead, cfg::kHeadActiveHigh));
    reverse_in.update(now, read_input(cfg::kReverse, cfg::kReverseActiveHigh));

    const LightsOff::Out act = lights.update(now, ig1_on, head_on, auto_enabled);
    if (act.pulse_set) coil_set.trigger(now);
    if (act.pulse_reset) coil_reset.trigger(now);
    // Never energise both coils of the latching relay at once.
    const bool set_on = coil_set.update(now);
    const bool reset_on = coil_reset.update(now) && !set_on;
    digitalWrite(cfg::kRly1Set, set_on ? HIGH : LOW);
    digitalWrite(cfg::kRly1Reset, reset_on ? HIGH : LOW);

    const bool pi_ok = pi.alive(now);
    bool relay_on[3];
    for (int i = 0; i < 3; i++) {
        relay_on[i] = pi_ok && relay_req[i];
        digitalWrite(kRelayPins[i], relay_on[i] ? HIGH : LOW);
    }
#ifdef MAIN_BENCH
    bench::log_outputs(now, set_on, reset_on, relay_on);
#endif

    if (logic::elapsed(now, last_status) >= cfg::kStatusPeriodMs) {
        last_status = now;
        send_status(now, read_vbat());
    }

    if (ig1_on || lights.busy() || set_on || reset_on || pi_ok) last_activity = now;
    if (!ig1_on && logic::elapsed(now, last_activity) >= cfg::kSleepAfterMs) go_to_sleep();

    delay(1);
}
