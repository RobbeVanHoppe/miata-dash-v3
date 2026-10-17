// Main node firmware (miata-hw docs/design-main-node.md §13): car inputs, relay
// drivers, auto lights-off with latching relay RLY1, CAN (can/miata.dbc), deep
// sleep with the key off.
//
// Safety model (owner rule): every output's de-energised state is the factory
// circuit. Outputs start OFF (board pull-downs, written low first thing),
// Pi-commanded relays need a live Pi heartbeat and IG1, and a dead node leaves the
// car stock (RLY1 is reset by IG1 in hardware).

#include <Arduino.h>
#include <Preferences.h>
#include <esp_sleep.h>
#include <esp_task_wdt.h>

#include "canlink.h"
#include "config.h"
#include "miata.h"
#include "node_logic.h"
#ifdef MAIN_BENCH
#include "bench_io.h"
#endif

using logic::CoilSequencer;
using logic::Debounce;
using logic::Heartbeat;
using logic::LightsOff;

// Kept across deep sleep (not across power loss).
RTC_DATA_ATTR static LightsOff::Snapshot rtc_lights = {LightsOff::State::KeyoffIdle, 0};
RTC_DATA_ATTR static bool rtc_auto_enabled = true;
RTC_DATA_ATTR static bool rtc_valid = false;

static Debounce ign(cfg::kIgnDebounceMs), head(cfg::kHeadDebounceMs), rev(cfg::kRevDebounceMs),
    fb(cfg::kFbDebounceMs), tns(cfg::kHeadDebounceMs);
static Heartbeat pi(cfg::kPiTimeoutMs);
static LightsOff lights;
static Preferences nvs;
static bool relay_req[3] = {false, false, false};
static bool auto_enabled = true, auto_from_pi = false, auto_ever_received = false;
static uint8_t counter = 0, last_status_code = 0xFF;
static uint32_t last_status = 0, vbat_low_since = 0;
static uint32_t bus_off_seen = 0;
static float vbat = 0.0f;

// ---- hardware access (replaced by the serial console in the bench build) ---------------
#ifdef MAIN_BENCH
static bool raw_ign() { return bench::ig1; }
static bool raw_head() { return bench::head; }
static bool raw_fb_set() { return bench::relay_set; }
static bool raw_rev() { return bench::rev; }
static bool raw_tns() { return bench::head; }
#else
static bool raw_ign() { return digitalRead(cfg::kIgn) == HIGH; }
static bool raw_head() { return digitalRead(cfg::kHeadN) == LOW; }
static bool raw_fb_set() { return digitalRead(cfg::kRly1FbN) == LOW; }
static bool raw_rev() { return (digitalRead(cfg::kRev) == HIGH) != cfg::kRevActiveLow; }
static bool raw_tns() { return digitalRead(cfg::kTnsN) == LOW; }
#endif

static float read_vbat() {
    // Atten 0 covers up to ~16 V (950 mV); switch to 11 dB above (§10).
    analogSetPinAttenuation(cfg::kVbatPin, ADC_0db);
    uint32_t mv = analogReadMilliVolts(cfg::kVbatPin);
    if (mv > 900) {
        analogSetPinAttenuation(cfg::kVbatPin, ADC_11db);
        mv = analogReadMilliVolts(cfg::kVbatPin);
    }
    return mv / 1000.0f / cfg::kVbatK + cfg::kVbatOffsetV;  // two-point NVS calibration: TODO
}

static void write_outputs(const CoilSequencer::Drive& d, const bool relays[3]) {
    for (int i = 0; i < 3; i++) digitalWrite(cfg::kRelayPins[i], relays[i] ? HIGH : LOW);
    digitalWrite(cfg::kVrlyEn, d.vrly ? HIGH : LOW);
    digitalWrite(cfg::kSetEn, d.set ? HIGH : LOW);
    digitalWrite(cfg::kRstEn, d.reset && !d.set ? HIGH : LOW);
#ifdef MAIN_BENCH
    bench::drive(millis(), d.vrly, d.set, d.reset, relays);
#endif
}

static void all_outputs_low() {
    const bool off[3] = {false, false, false};
    write_outputs(CoilSequencer::Drive{}, off);
}

// ---- CAN ------------------------------------------------------------------------------
static void handle_frame(const canlink::Frame& f, uint32_t now) {
    if (f.id != MIATA_PI_MAIN_CMD_FRAME_ID) return;
    miata_pi_main_cmd_t m;
    if (miata_pi_main_cmd_unpack(&m, f.data, f.len) != 0) return;
    pi.feed(now, m.pi_counter);
    relay_req[0] = m.pi_relay_req_o02;
    relay_req[1] = m.pi_relay_req_o03;
    relay_req[2] = m.pi_relay_req_o04;
    const bool en = m.pi_auto_lights_off_en;
    if (!auto_from_pi || en != auto_enabled) {
        if (en != auto_enabled) nvs.putBool("auto", en);  // flash write only on change
        auto_enabled = en;
        auto_from_pi = auto_ever_received = true;
    }
}

static void send_status(const bool relays[3], bool pi_ok) {
    miata_main_status_t s = {};
    s.main_ig1 = ign.state();
    s.main_head_switch = head.state();
    s.main_reverse = rev.state();
    s.main_lights_off_latched = fb.state();  // real relay position, not the command
    s.main_relay_o02 = relays[0];
    s.main_relay_o03 = relays[1];
    s.main_relay_o04 = relays[2];
    s.main_pi_timeout = !pi_ok;
    s.main_faults = lights.faults();
    s.main_v_bat = miata_main_status_main_v_bat_encode(vbat < 0 ? 0 : vbat);
    s.main_counter = counter++;
    uint8_t buf[MIATA_MAIN_STATUS_LENGTH];
    if (miata_main_status_pack(buf, &s, sizeof buf) > 0) canlink::send(MIATA_MAIN_STATUS_FRAME_ID, buf, sizeof buf);
}

// ---- sleep ----------------------------------------------------------------------------
static void go_to_sleep() {
    rtc_lights = lights.save();
    rtc_auto_enabled = auto_enabled;
    rtc_valid = true;
#ifdef MAIN_BENCH
    if (!bench::sleep_reported) bench::event(millis(), lights.latched() ? "sleep_latched" : "sleep");
    bench::sleep_reported = true;
    return;  // deep sleep would drop the USB serial link
#endif
    all_outputs_low();
    canlink::end();
    pinMode(cfg::kCanTx, INPUT);
    pinMode(cfg::kCanRx, INPUT);
    digitalWrite(cfg::kCanPowerN, HIGH);  // transceiver off
    gpio_hold_en(cfg::kCanPowerN);
    gpio_deep_sleep_hold_en();
    uint64_t mask = 1ULL << cfg::kIgn;
    if (lights.latched()) {
        mask |= 1ULL << cfg::kHeadN;  // stalk off HEAD -> pin goes high -> override
        esp_sleep_enable_timer_wakeup(cfg::kHoldCheckUs);
    }
    esp_sleep_enable_ext1_wakeup(mask, ESP_EXT1_WAKEUP_ANY_HIGH);
    esp_deep_sleep_start();
}

// ---- setup / loop ---------------------------------------------------------------------
void setup() {
    // Outputs low before they become outputs: no glitch on the coil drivers.
    for (gpio_num_t p : cfg::kRelayPins) {
        digitalWrite(p, LOW);
        pinMode(p, OUTPUT);
    }
    for (gpio_num_t p : {cfg::kVrlyEn, cfg::kSetEn, cfg::kRstEn}) {
        digitalWrite(p, LOW);
        pinMode(p, OUTPUT);
    }
    gpio_hold_dis(cfg::kCanPowerN);
    gpio_deep_sleep_hold_dis();
    digitalWrite(cfg::kCanPowerN, HIGH);
    pinMode(cfg::kCanPowerN, OUTPUT);
    for (gpio_num_t p : {cfg::kIgn, cfg::kHeadN, cfg::kRly1FbN, cfg::kRev, cfg::kTnsN}) pinMode(p, INPUT);

#ifdef MAIN_BENCH
    Serial.begin(115200);
    bench::event(millis(), "boot");
#endif
    const uint32_t now = millis();
    ign = Debounce(cfg::kIgnDebounceMs, raw_ign());  // seed: a wake-up is not an edge
    head = Debounce(cfg::kHeadDebounceMs, raw_head());
    rev = Debounce(cfg::kRevDebounceMs, raw_rev());
    fb = Debounce(cfg::kFbDebounceMs, raw_fb_set());
    tns = Debounce(cfg::kHeadDebounceMs, raw_tns());

    nvs.begin("main", false);
    auto_ever_received = nvs.isKey("auto");
    const esp_sleep_wakeup_cause_t wake = esp_sleep_get_wakeup_cause();
    if (rtc_valid && wake != ESP_SLEEP_WAKEUP_UNDEFINED) {
        lights.restore(rtc_lights, now);
        auto_enabled = rtc_auto_enabled;
        if (wake == ESP_SLEEP_WAKEUP_TIMER) lights.hold_check(now, head.state(), fb.state());
    } else {
        auto_enabled = nvs.getBool("auto", true);  // first boot: on (Proposal, Q5)
        lights.add_fault(logic::fault::kUnexpectedReset);
        if (fb.state()) lights.restore({LightsOff::State::Latched, lights.faults()}, now);
    }

    esp_task_wdt_init(cfg::kWatchdogS, true);
    esp_task_wdt_add(nullptr);
}

void loop() {
    const uint32_t now = millis();
    esp_task_wdt_reset();

    const bool ig1_on = ign.update(now, raw_ign());
    const bool head_on = head.update(now, raw_head());
    rev.update(now, raw_rev());
    tns.update(now, raw_tns());
    const bool fb_set = fb.update(now, raw_fb_set());

    // CAN only while the key is on or something is pending (§13.3: powered at IG1).
    static bool can_up = false;
    if (ig1_on && !can_up) {
        digitalWrite(cfg::kCanPowerN, LOW);
        delay(2);
#ifndef MAIN_BENCH  // bench: no transceiver; Pi frames are injected by bench_io.h
        canlink::begin(cfg::kCanTx, cfg::kCanRx);
#endif
        can_up = true;
        pi.reset();  // HB_OK false until re-acquired at every IG1-on
    }
    canlink::service();
    if (canlink::bus_off_count() != bus_off_seen) {
        bus_off_seen = canlink::bus_off_count();
        lights.add_fault(logic::fault::kBusOff);
    }
    canlink::Frame f;
    while (canlink::receive(f)) handle_frame(f, now);
#ifdef MAIN_BENCH
    bench::poll_serial();
    if (bench::next_pi_frame(now, f)) handle_frame(f, now);
    if (bench::take_timer_wake()) lights.hold_check(now, head_on, fb_set);
    if (ig1_on) bench::sleep_reported = false;
#endif

    const CoilSequencer::Drive drive = lights.update(now, ig1_on, head_on, fb_set, auto_enabled);

    const bool pi_ok = pi.alive(now);
    bool relays[3];
    for (int i = 0; i < 3; i++) relays[i] = pi_ok && relay_req[i] && (ig1_on || !cfg::kReleaseRelaysAtKeyOff);
    write_outputs(drive, relays);

    // Battery: 16-sample mean every 100 ms is approximated by an IIR (§13.2).
    static uint32_t last_adc = 0;
    if (logic::elapsed(now, last_adc) >= 100) {
        last_adc = now;
#ifdef MAIN_BENCH
        vbat = bench::vbat;
#else
        const float v = read_vbat();
        vbat = vbat <= 0.0f ? v : vbat + (v - vbat) / 4.0f;
#endif
        if (vbat > cfg::kVbatHighV) lights.add_fault(logic::fault::kVbatHigh);
        if (vbat >= cfg::kVbatLowV) vbat_low_since = now;
        else if (logic::elapsed(now, vbat_low_since) >= cfg::kVbatLowMs) lights.add_fault(logic::fault::kVbatLow);
    }
    if (!auto_ever_received) lights.add_fault(logic::fault::kAutoDefault);

    // MAIN_STATUS at 10 Hz plus immediately on any input or latch change.
    const uint8_t code = ig1_on | head_on << 1 | rev.state() << 2 | fb_set << 3 | relays[0] << 4 |
                         relays[1] << 5 | relays[2] << 6 | (!pi_ok) << 7;
    if (code != last_status_code || logic::elapsed(now, last_status) >= cfg::kStatusPeriodMs) {
        last_status_code = code;
        last_status = now;
        send_status(relays, pi_ok);
    }

    if (!ig1_on && lights.can_sleep(now, cfg::kAwakeKeyoffMs)) go_to_sleep();
    delay(1);
}
