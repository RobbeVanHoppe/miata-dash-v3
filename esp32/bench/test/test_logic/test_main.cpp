// Unit tests for the hardware-free node logic, run on the ESP32 with Unity.
#include <Arduino.h>
#include <unity.h>

#include "knob_logic.h"
#include "node_logic.h"

using namespace logic;

void setUp() {}
void tearDown() {}

// Drive LightsOff in 10 ms steps.
static LightsOff::Out run(LightsOff& l, uint32_t& t, uint32_t ms, bool ig1, bool head, bool en = true) {
    LightsOff::Out acc;
    for (uint32_t i = 0; i < ms; i += 10) {
        t += 10;
        LightsOff::Out o = l.update(t, ig1, head, en);
        acc.pulse_set |= o.pulse_set;
        acc.pulse_reset |= o.pulse_reset;
    }
    return acc;
}

void test_lights_off_after_3s() {
    LightsOff l;
    uint32_t t = 0;
    run(l, t, 100, true, true);
    TEST_ASSERT_FALSE(run(l, t, 2900, false, true).pulse_set);  // not yet
    TEST_ASSERT_TRUE(run(l, t, 200, false, true).pulse_set);
    TEST_ASSERT_TRUE(l.latched());
}

void test_key_back_on_cancels_countdown() {
    LightsOff l;
    uint32_t t = 0;
    run(l, t, 100, true, true);
    run(l, t, 1000, false, true);
    LightsOff::Out o = run(l, t, 5000, true, true);
    TEST_ASSERT_FALSE(o.pulse_set);
    TEST_ASSERT_EQUAL(static_cast<int>(LightsOff::State::Idle), static_cast<int>(l.state()));
}

void test_no_action_when_lights_already_off_or_disabled() {
    LightsOff a, b;
    uint32_t t1 = 0, t2 = 0;
    run(a, t1, 100, true, false);
    TEST_ASSERT_FALSE(run(a, t1, 4000, false, false).pulse_set);
    run(b, t2, 100, true, true, false);
    TEST_ASSERT_FALSE(run(b, t2, 4000, false, true, false).pulse_set);
}

void test_power_up_with_key_off_is_not_key_off_event() {
    LightsOff l;
    uint32_t t = 0;
    TEST_ASSERT_FALSE(run(l, t, 5000, false, true).pulse_set);
}

void test_stalk_toggle_overrides_and_key_on_resets() {
    LightsOff l;
    uint32_t t = 0;
    run(l, t, 100, true, true);
    run(l, t, 3200, false, true);
    TEST_ASSERT_TRUE(l.latched());
    TEST_ASSERT_TRUE(run(l, t, 50, false, false).pulse_reset);  // stalk moved
    TEST_ASSERT_FALSE(l.latched());
    // Latch again on the next key-off, then key-on resets it.
    run(l, t, 100, true, true);
    run(l, t, 3200, false, true);
    TEST_ASSERT_TRUE(run(l, t, 20, true, true).pulse_reset);
}

void test_snapshot_survives_sleep() {
    LightsOff l;
    uint32_t t = 0;
    run(l, t, 100, true, true);
    run(l, t, 3200, false, true);
    LightsOff woken;
    woken.restore(l.save());
    TEST_ASSERT_TRUE(woken.latched());
    TEST_ASSERT_TRUE(woken.update(t + 10, false, false, true).pulse_reset);
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

void test_heartbeat() {
    Heartbeat h(1000);
    TEST_ASSERT_FALSE(h.alive(0));
    h.feed(100);
    TEST_ASSERT_TRUE(h.alive(1000));
    TEST_ASSERT_FALSE(h.alive(1100));
}

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

void setup() {
    delay(2000);  // let the serial monitor attach
    UNITY_BEGIN();
    RUN_TEST(test_lights_off_after_3s);
    RUN_TEST(test_key_back_on_cancels_countdown);
    RUN_TEST(test_no_action_when_lights_already_off_or_disabled);
    RUN_TEST(test_power_up_with_key_off_is_not_key_off_event);
    RUN_TEST(test_stalk_toggle_overrides_and_key_on_resets);
    RUN_TEST(test_snapshot_survives_sleep);
    RUN_TEST(test_debounce_and_millis_wrap);
    RUN_TEST(test_heartbeat);
    RUN_TEST(test_haptics_detents_and_endstops);
    RUN_TEST(test_haptics_keeps_position_across_profile_change);
    RUN_TEST(test_power_on_request);
    UNITY_END();
}

void loop() {}
