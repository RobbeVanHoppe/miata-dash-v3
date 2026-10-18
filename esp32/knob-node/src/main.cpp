// Knob node firmware (miata-hw docs/design-knob-node.md): haptic BLDC knob
// (SimpleFOC + AS5600), CAN to the Pi, and the manual "Pi on" request to the power
// board while the Pi is off.
//
// Without the motor rail (cranking, battery below ~9.7 V, driver fault) the knob still
// works as a plain encoder: the AS5600 keeps reporting positions, only the feel is gone.

#include <Arduino.h>
#include <Preferences.h>
#include <SimpleFOC.h>
#include <Wire.h>

#include "canlink.h"
#include "config.h"
#include "knob_logic.h"
#include "miata.h"

using logic::Button;
using logic::Haptics;
using logic::KnobMode;
using logic::KnobProfile;
using logic::MotorGate;
using logic::PowerOnRequest;
using logic::ThermalLimiter;

static BLDCMotor motor(cfg::kPolePairs);
static BLDCDriver3PWM driver(cfg::kPwmA, cfg::kPwmB, cfg::kPwmC, cfg::kMotorEnable);
static MagneticSensorI2C sensor(AS5600_I2C);
static Preferences nvs;

static Haptics haptics(cfg::kMaxTorqueV);
static Button button(20, cfg::kLongPressMs);
static PowerOnRequest pi_on(cfg::kPiOnPulseMs, cfg::kPiOnCooldownMs);
static MotorGate gate(cfg::kVmOnV, cfg::kVmOffV, cfg::kVmOnHoldMs);
static ThermalLimiter thermal(cfg::kThermalLimitV, cfg::kThermalWindowS);

static KnobProfile pi_profile;  // last profile received from the Pi
static bool pi_present = false, using_power_profile = false;
static bool foc_initialised = false, motor_on = false;
static uint32_t last_pi_status = 0, last_state_tx = 0, last_fault_reset = 0, last_loop_us = 0;
static int16_t last_sent_pos = 0;
static bool last_sent_pressed = false, last_sent_long = false;
static uint8_t counter = 0;

static float read_vm() { return analogReadMilliVolts(cfg::kVmSensePin) / 1000.0f / cfg::kVmDivider; }
static bool driver_fault_free() { return digitalRead(cfg::kDrvNFault) == HIGH; }

// First start: SimpleFOC aligns the rotor (a visible twitch) and we store the result.
// Afterwards zero angle and direction come from NVS, so initFOC() skips the alignment
// and a crank (motor rail drop) never makes the knob twitch.
static void init_foc_once() {
    if (nvs.isKey("zea")) {
        motor.zero_electric_angle = nvs.getFloat("zea");
        motor.sensor_direction = static_cast<Direction>(nvs.getChar("dir"));
    }
    motor.enable();
    if (motor.initFOC() == 1) {
        foc_initialised = true;
        if (!nvs.isKey("zea")) {
            nvs.putFloat("zea", motor.zero_electric_angle);
            nvs.putChar("dir", static_cast<int8_t>(motor.sensor_direction));
        }
    } else {
        motor.disable();
    }
}

static void handle_frame(const canlink::Frame& f, uint32_t now) {
    const float angle = sensor.getAngle();
    if (f.id == MIATA_PI_STATUS_FRAME_ID) {
        last_pi_status = now;
    } else if (f.id == MIATA_PI_KNOB_PROFILE_FRAME_ID) {
        miata_pi_knob_profile_t m;
        if (miata_pi_knob_profile_unpack(&m, f.data, f.len) != 0) return;
        KnobProfile p;
        p.mode = static_cast<KnobMode>(m.prof_mode);
        p.detents_per_rev = m.prof_detents_per_rev;
        p.strength_pct = m.prof_strength > 100 ? 100 : m.prof_strength;
        p.min_pos = m.prof_min_pos;
        p.max_pos = m.prof_max_pos;
        const KnobProfile& cur = pi_profile;
        const bool changed = p.mode != cur.mode || p.detents_per_rev != cur.detents_per_rev ||
                             p.strength_pct != cur.strength_pct || p.min_pos != cur.min_pos ||
                             p.max_pos != cur.max_pos;
        pi_profile = p;
        if (changed && pi_present) haptics.set_profile(p, angle);
    } else if (f.id == MIATA_PI_KNOB_SETPOS_FRAME_ID) {
        miata_pi_knob_setpos_t m;
        if (miata_pi_knob_setpos_unpack(&m, f.data, f.len) != 0) return;
        haptics.set_position(m.set_pos_position, angle);
        last_sent_pos = m.set_pos_position;  // a commanded jump is not a turn
    }
}

static void send_state(uint32_t now, float angle) {
    const int16_t pos = haptics.position();
    int32_t delta = pos - last_sent_pos;
    if (delta > 127) delta = 127;
    if (delta < -128) delta = -128;
    miata_knob_state_t s = {};
    s.knob_position = pos;
    s.knob_delta = static_cast<int8_t>(delta);
    s.knob_pressed = button.pressed();
    s.knob_long_press = button.long_press();
    s.knob_motor_off = !motor_on;
    float deg = fmodf(angle * 180.0f / PI, 360.0f);
    if (deg < 0) deg += 360.0f;
    s.knob_angle = miata_knob_state_knob_angle_encode(deg);
    s.knob_counter = counter++;
    uint8_t buf[MIATA_KNOB_STATE_LENGTH];
    if (miata_knob_state_pack(buf, &s, sizeof buf) > 0 && canlink::send(MIATA_KNOB_STATE_FRAME_ID, buf, sizeof buf)) {
        last_sent_pos = pos;
        last_sent_pressed = s.knob_pressed;
        last_sent_long = s.knob_long_press;
        last_state_tx = now;
    }
}

void setup() {
    pinMode(cfg::kPiOnReqGate, OUTPUT);
    digitalWrite(cfg::kPiOnReqGate, LOW);  // never request at boot
    pinMode(cfg::kMotorEnable, OUTPUT);
    digitalWrite(cfg::kMotorEnable, LOW);
    pinMode(cfg::kDrvNReset, OUTPUT);
    digitalWrite(cfg::kDrvNReset, HIGH);
    pinMode(cfg::kDrvNFault, INPUT);
    pinMode(cfg::kButton, INPUT);  // external pull-up + RC + clamp on the board
    analogSetPinAttenuation(cfg::kVmSensePin, ADC_0db);
    Serial.begin(115200);
    nvs.begin("knob", false);

    Wire.begin(cfg::kSda, cfg::kScl, 400000);
    sensor.init(&Wire);
    motor.linkSensor(&sensor);
    driver.voltage_power_supply = cfg::kSupplyV;
    driver.init();
    motor.linkDriver(&driver);
    motor.controller = MotionControlType::torque;
    motor.torque_controller = TorqueControlType::voltage;
    motor.voltage_limit = cfg::kMaxTorqueV;
    motor.init();
    motor.disable();  // the gate below decides when the motor may run

    canlink::begin(cfg::kCanTx, cfg::kCanRx);
    haptics.set_profile(logic::power_on_profile(), sensor.getAngle());
    using_power_profile = true;
    last_loop_us = micros();
}

void loop() {
    const uint32_t now = millis();
    const uint32_t now_us = micros();
    const float dt = (now_us - last_loop_us) * 1e-6f;
    last_loop_us = now_us;

    canlink::service();
    canlink::Frame f;
    while (canlink::receive(f)) handle_frame(f, now);

    // Pi presence decides who owns the feel: the Pi's page profile, or our power-on click.
    pi_present = last_pi_status != 0 && logic::elapsed(now, last_pi_status) < cfg::kPiAbsentMs;
    if (pi_present && using_power_profile) {
        haptics.set_profile(pi_profile, sensor.getAngle());
        using_power_profile = false;
    } else if (!pi_present && !using_power_profile) {
        haptics.set_position(0, sensor.getAngle());
        haptics.set_profile(logic::power_on_profile(), sensor.getAngle());
        using_power_profile = true;
    }

    // Motor gate: VM high enough (DRV8313 UVLO) and no driver fault.
    const bool fault_free = driver_fault_free();
    const bool allowed = gate.update(now, read_vm(), fault_free);
    if (!fault_free && logic::elapsed(now, last_fault_reset) >= 1000) {  // try to clear a latched fault
        last_fault_reset = now;
        digitalWrite(cfg::kDrvNReset, LOW);
        delayMicroseconds(2000);
        digitalWrite(cfg::kDrvNReset, HIGH);
    }
    if (allowed && !motor_on) {
        if (!foc_initialised) init_foc_once();
        else motor.enable();
        motor_on = foc_initialised;
    } else if (!allowed && motor_on) {
        motor.disable();
        motor_on = false;
    }

    sensor.update();
    const float angle = sensor.getAngle();
    const float torque = haptics.update(angle, sensor.getVelocity());
    if (motor_on) {
        motor.loopFOC();
        motor.move(thermal.apply(torque, dt));
    } else {
        thermal.apply(0.0f, dt);  // the winding cools while the motor is off
    }

    button.update(now, digitalRead(cfg::kButton) == LOW);
    digitalWrite(cfg::kPiOnReqGate, pi_on.update(now, pi_present, haptics.position(), button.pressed()) ? HIGH : LOW);

    const bool changed = haptics.position() != last_sent_pos || button.pressed() != last_sent_pressed ||
                         button.long_press() != last_sent_long;
    if (changed || logic::elapsed(now, last_state_tx) >= cfg::kStatePeriodMs) send_state(now, angle);
}
