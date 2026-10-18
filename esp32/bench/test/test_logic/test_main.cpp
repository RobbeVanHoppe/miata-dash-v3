// Unit tests for the hardware-free node logic, run on the ESP32 with Unity.
#include <Arduino.h>
#include <unity.h>

#include "knob_logic.h"
#include "node_logic.h"

using namespace logic;

void setUp() {}
void tearDown() {}

// ---- main node: lights-off with a simulated latching relay -----------------------------
// The relay's feedback contact follows a coil energised (with supply) for >= 30 ms,
// and the hardware IG1 self-reset releases it 20 ms after key-on.
struct Car {
    LightsOff lights;
    uint32_t t = 0;
    bool ig1 = false, head = false, enabled = true;
    bool fb = false, stuck = false, selfreset = true;
    uint32_t set_ms = 0, reset_ms = 0, ig1_ms = 0;
    int set_pulses = 0, reset_pulses = 0;
    bool set_prev = false, reset_prev = false, both_on = false;

    void run(uint32_t ms) {
        for (uint32_t i = 0; i < ms; i++) {
            t++;
            const CoilSequencer::Drive d = lights.update(t, ig1, head, fb, enabled);
            set_ms = d.vrly && d.set ? set_ms + 1 : 0;
            reset_ms = d.vrly && d.reset ? reset_ms + 1 : 0;
            ig1_ms = ig1 ? ig1_ms + 1 : 0;
            if (d.set && !set_prev) set_pulses++;
            if (d.reset && !reset_prev) reset_pulses++;
            both_on |= d.set && d.reset;
            set_prev = d.set;
            reset_prev = d.reset;
            if (!stuck) {
                if (set_ms >= 30) fb = true;
                if (reset_ms >= 30) fb = false;
                if (selfreset && ig1_ms >= 20) fb = false;
            }
        }
    }
};

void test_lights_off_after_3s_with_verified_pulse() {
    Car c;
    c.head = true;
    c.ig1 = true;
    c.run(500);
    c.ig1 = false;
    c.run(2950);
    TEST_ASSERT_EQUAL(0, c.set_pulses);  // not yet
    c.run(300);
    TEST_ASSERT_EQUAL(1, c.set_pulses);
    TEST_ASSERT_TRUE(c.fb);
    TEST_ASSERT_TRUE(c.lights.latched());
    TEST_ASSERT_FALSE(c.both_on);
}

void test_set_retries_three_times_then_fault() {
    Car c;
    c.head = true;
    c.stuck = true;  // relay never moves
    c.ig1 = true;
    c.run(300);
    c.ig1 = false;
    c.run(3000 + 3 * (5 + 100 + 50) + 2 * 500 + 200);
    TEST_ASSERT_EQUAL(3, c.set_pulses);
    TEST_ASSERT_TRUE(c.lights.faults() & fault::kSetFailed);
    TEST_ASSERT_FALSE(c.lights.latched());
}

void test_key_back_on_within_3s_cancels() {
    Car c;
    c.head = true;
    c.ig1 = true;
    c.run(300);
    c.ig1 = false;
    c.run(1500);
    c.ig1 = true;
    c.run(5000);
    TEST_ASSERT_EQUAL(0, c.set_pulses);
}

void test_no_action_when_lights_off_or_disabled() {
    Car a;
    a.ig1 = true;
    a.run(300);
    a.ig1 = false;
    a.run(4000);
    TEST_ASSERT_EQUAL(0, a.set_pulses);
    Car b;
    b.head = true;
    b.enabled = false;
    b.ig1 = true;
    b.run(300);
    b.ig1 = false;
    b.run(4000);
    TEST_ASSERT_EQUAL(0, b.set_pulses);
}

void test_power_up_with_key_off_is_not_key_off_event() {
    Car c;
    c.head = true;
    c.run(5000);
    TEST_ASSERT_EQUAL(0, c.set_pulses);
}

void test_stalk_off_head_overrides() {
    Car c;
    c.head = true;
    c.ig1 = true;
    c.run(300);
    c.ig1 = false;
    c.run(3500);
    TEST_ASSERT_TRUE(c.lights.latched());
    c.head = false;  // driver moves the stalk
    c.run(300);
    TEST_ASSERT_EQUAL(1, c.reset_pulses);
    TEST_ASSERT_FALSE(c.fb);
    c.head = true;  // back to HEAD: factory behaviour, no new SET
    c.run(5000);
    TEST_ASSERT_EQUAL(1, c.set_pulses);
}

void test_key_on_hardware_self_reset_no_fault() {
    Car c;
    c.head = true;
    c.ig1 = true;
    c.run(300);
    c.ig1 = false;
    c.run(3500);
    c.ig1 = true;
    c.run(500);
    TEST_ASSERT_FALSE(c.fb);
    TEST_ASSERT_EQUAL(0, c.reset_pulses);  // hardware handled it within the grace time
    TEST_ASSERT_FALSE(c.lights.faults() & fault::kSetWithIg1);
}

void test_key_on_without_self_reset_firmware_resets_with_fault() {
    Car c;
    c.head = true;
    c.selfreset = false;  // hardware path broken
    c.ig1 = true;
    c.run(300);
    c.ig1 = false;
    c.run(3500);
    c.ig1 = true;
    c.run(600);
    TEST_ASSERT_EQUAL(1, c.reset_pulses);
    TEST_ASSERT_FALSE(c.fb);
    TEST_ASSERT_TRUE(c.lights.faults() & fault::kSetWithIg1);
}

void test_hold_check_resets_dropout() {
    Car c;
    c.head = true;
    c.ig1 = true;
    c.run(300);
    c.ig1 = false;
    c.run(3500);
    c.fb = false;  // relay dropped out on its own while parked
    c.lights.hold_check(c.t, c.head, c.fb);
    c.run(400);
    TEST_ASSERT_EQUAL(2, c.set_pulses);
    TEST_ASSERT_TRUE(c.fb);
    TEST_ASSERT_TRUE(c.lights.faults() & fault::kDroppedOut);
}

void test_sleep_only_when_settled() {
    Car c;
    c.head = true;
    c.ig1 = true;
    c.run(300);
    c.ig1 = false;
    c.run(3500);
    TEST_ASSERT_FALSE(c.lights.can_sleep(c.t, 10000));
    c.run(10000);
    TEST_ASSERT_TRUE(c.lights.can_sleep(c.t, 10000));
    LightsOff woken;
    woken.restore(c.lights.save(), 0);
    TEST_ASSERT_TRUE(woken.latched());
}

// ---- heartbeat ----------------------------------------------------------------------------
void test_heartbeat_acquire_timeout_and_stuck_counter() {
    Heartbeat h(1000);
    h.feed(0, 10);
    h.feed(100, 11);
    TEST_ASSERT_FALSE(h.alive(150));  // needs 3 consecutive frames
    h.feed(200, 12);
    TEST_ASSERT_TRUE(h.alive(250));
    for (uint32_t t = 300; t < 1300; t += 100) h.feed(t, 12);  // stuck application: same counter
    TEST_ASSERT_FALSE(h.alive(1250));
    h.feed(1300, 13);  // one good frame is not enough to come back
    TEST_ASSERT_FALSE(h.alive(1310));
    h.feed(1400, 14);
    h.feed(1500, 15);
    TEST_ASSERT_TRUE(h.alive(1510));
    h.feed(1600, 255);  // gap/jump while alive: still a valid frame
    h.feed(1700, 0);    // wraps 255 -> 0
    TEST_ASSERT_TRUE(h.alive(2600));
    TEST_ASSERT_FALSE(h.alive(2800));
}

void test_debounce_and_millis_wrap() {
    Debounce d(30);
    uint32_t t = 0xFFFFFFF0u;  // 16 ms before millis() wraps
    d.update(t, true);
    TEST_ASSERT_FALSE(d.update(t + 20, true));
    TEST_ASSERT_TRUE(d.update(t + 40, true));  // crosses the wrap
    TEST_ASSERT_TRUE(d.update(t + 50, false));  // glitch ignored
    TEST_ASSERT_TRUE(d.update(t + 60, true));
}

// ---- knob ---------------------------------------------------------------------------------
void test_haptics_detents_and_endstops() {
    Haptics h(3.0f);
    KnobProfile p;
    p.mode = KnobMode::DetentsEndstops;
    p.detents_per_rev = 12;
    p.strength_pct = 100;
    p.min_pos = 0;
    p.max_pos = 3;
    h.set_profile(p, 0.0f);
    const float w = 2.0f * PI / 12;
    TEST_ASSERT_FLOAT_WITHIN(1e-4, 0.0f, h.update(0.0f, 0.0f));  // on a detent: no torque
    TEST_ASSERT_TRUE(h.update(0.2f * w, 0.0f) < 0.0f);          // pulled back
    h.update(2.1f * w, 0.0f);
    TEST_ASSERT_EQUAL(2, h.position());
    h.update(6.0f * w, 0.0f);  // far past the end stop
    TEST_ASSERT_EQUAL(3, h.position());
    TEST_ASSERT_FLOAT_WITHIN(1e-3, -3.0f, h.update(6.0f * w, 0.0f));  // full torque back
}

void test_haptics_keeps_position_across_profile_change() {
    Haptics h(3.0f);
    KnobProfile a;
    a.detents_per_rev = 12;
    h.set_profile(a, 0.0f);
    h.update(3.0f * 2.0f * PI / 12, 0.0f);
    TEST_ASSERT_EQUAL(3, h.position());
    KnobProfile b;
    b.detents_per_rev = 36;
    h.set_profile(b, 3.0f * 2.0f * PI / 12);
    h.update(3.0f * 2.0f * PI / 12, 0.0f);
    TEST_ASSERT_EQUAL(3, h.position());
}

void test_power_on_request() {
    PowerOnRequest r(300, 10000);
    TEST_ASSERT_FALSE(r.update(0, true, 1, true));  // Pi present: never
    TEST_ASSERT_TRUE(r.update(10, false, 1, false));
    TEST_ASSERT_TRUE(r.update(300, false, 1, false));
    TEST_ASSERT_FALSE(r.update(320, false, 1, false));  // pulse over
    TEST_ASSERT_FALSE(r.update(5000, false, 0, true));  // cooldown
    TEST_ASSERT_TRUE(r.update(10400, false, 0, true));
}


void test_motor_gate_hysteresis_and_fault() {
    MotorGate g(8.3f, 7.9f, 200);
    TEST_ASSERT_FALSE(g.update(0, 9.0f, true));
    TEST_ASSERT_FALSE(g.update(150, 9.0f, true));  // must hold 200 ms
    TEST_ASSERT_TRUE(g.update(210, 9.0f, true));
    TEST_ASSERT_TRUE(g.update(300, 8.0f, true));   // inside hysteresis: stays on
    TEST_ASSERT_FALSE(g.update(310, 7.8f, true));  // below off threshold
    TEST_ASSERT_FALSE(g.update(320, 9.0f, true));
    TEST_ASSERT_FALSE(g.update(400, 8.1f, true));  // dip resets the hold timer
    TEST_ASSERT_FALSE(g.update(500, 9.0f, true));
    TEST_ASSERT_TRUE(g.update(710, 9.0f, true));
    TEST_ASSERT_FALSE(g.update(720, 9.0f, false));  // driver fault: off at once
}

void test_thermal_limiter() {
    ThermalLimiter t(1.3f, 60.0f);
    float v = 0;
    for (int i = 0; i < 500; i++) v = t.apply(3.0f, 0.01f);  // 5 s burst at full voltage
    TEST_ASSERT_FLOAT_WITHIN(0.01f, 3.0f, v);                // short bursts pass untouched
    for (int i = 0; i < 60000; i++) v = t.apply(3.0f, 0.01f);  // 10 min held against a stop
    TEST_ASSERT_FLOAT_WITHIN(0.1f, 1.3f, t.rms());
    TEST_ASSERT_TRUE(v < 1.5f);
    for (int i = 0; i < 30000; i++) t.apply(0.0f, 0.01f);  // 5 min off: cools down
    TEST_ASSERT_TRUE(t.rms() < 0.15f);  // 5 tau: sqrt(1.69 x e^-5) = 0.107 V
}

void setup() {
    delay(2000);  // let the serial monitor attach
    UNITY_BEGIN();
    RUN_TEST(test_lights_off_after_3s_with_verified_pulse);
    RUN_TEST(test_set_retries_three_times_then_fault);
    RUN_TEST(test_key_back_on_within_3s_cancels);
    RUN_TEST(test_no_action_when_lights_off_or_disabled);
    RUN_TEST(test_power_up_with_key_off_is_not_key_off_event);
    RUN_TEST(test_stalk_off_head_overrides);
    RUN_TEST(test_key_on_hardware_self_reset_no_fault);
    RUN_TEST(test_key_on_without_self_reset_firmware_resets_with_fault);
    RUN_TEST(test_hold_check_resets_dropout);
    RUN_TEST(test_sleep_only_when_settled);
    RUN_TEST(test_heartbeat_acquire_timeout_and_stuck_counter);
    RUN_TEST(test_debounce_and_millis_wrap);
    RUN_TEST(test_haptics_detents_and_endstops);
    RUN_TEST(test_haptics_keeps_position_across_profile_change);
    RUN_TEST(test_power_on_request);
    RUN_TEST(test_motor_gate_hysteresis_and_fault);
    RUN_TEST(test_thermal_limiter);
    UNITY_END();
}

void loop() {}
