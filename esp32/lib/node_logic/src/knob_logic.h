// Haptic knob logic, hardware-free (angles in radians, time in ms).
//
// The motor runs in voltage-torque mode; the "feel" is a spring towards the
// nearest detent (or back inside the end stops), so each Pi page can ask for a
// different profile over CAN (PI_KNOB_PROFILE).
#pragma once

#include <math.h>
#include <stdint.h>

#include "node_logic.h"

namespace logic {

// PI_KNOB_PROFILE.Prof_Mode (can/miata.dbc)
enum class KnobMode : uint8_t { Free = 0, Detents = 1, DetentsEndstops = 2, FineTuning = 3, PowerOnClick = 4 };

struct KnobProfile {
    KnobMode mode = KnobMode::Detents;
    uint8_t detents_per_rev = 20;
    uint8_t strength_pct = 50;
    int16_t min_pos = 0;
    int16_t max_pos = 0;  // min == max: no end stops
};

// Profile the knob uses on its own while the Pi is off: two positions, a firm
// click between them, turning to position 1 = "power on" (like an old radio).
inline KnobProfile power_on_profile() {
    KnobProfile p;
    p.mode = KnobMode::PowerOnClick;
    p.detents_per_rev = 12;
    p.strength_pct = 100;
    p.min_pos = 0;
    p.max_pos = 1;
    return p;
}

class Haptics {
   public:
    // max_torque_v: motor voltage at 100 % strength (from the motor's resistance and heating).
    explicit Haptics(float max_torque_v) : max_v_(max_torque_v) {}

    void set_profile(const KnobProfile& p, float angle_now) {
        profile_ = p;
        if (p.detents_per_rev == 0) profile_.detents_per_rev = 1;
        origin_ = angle_now - position_ * width();  // keep the current position across a profile change
    }

    void set_position(int16_t pos, float angle_now) {
        position_ = pos;
        origin_ = angle_now - pos * width();
    }

    // Returns the motor voltage (torque) for this angle; updates the detent position.
    float update(float angle, float velocity) {
        const float w = width();
        const float rel = (angle - origin_) / w;
        int32_t nearest = static_cast<int32_t>(lroundf(rel));
        const bool stops = profile_.min_pos != profile_.max_pos;
        if (stops) {
            if (nearest < profile_.min_pos) nearest = profile_.min_pos;
            if (nearest > profile_.max_pos) nearest = profile_.max_pos;
        }
        position_ = static_cast<int16_t>(nearest);
        if (profile_.mode == KnobMode::Free) return -kDamping * velocity * strength();

        const float error = angle - (origin_ + nearest * w);  // rad from the detent centre
        // Spring scaled so the torque peaks half-way between detents; stronger outside end stops.
        float k = max_v_ * strength() / (w / 2.0f);
        const bool outside = stops && (rel < profile_.min_pos - 0.5f || rel > profile_.max_pos + 0.5f);
        if (outside) k *= kEndstopGain;
        float v = -k * error - kDamping * velocity;
        const float limit = outside ? max_v_ : max_v_ * strength();
        if (v > limit) v = limit;
        if (v < -limit) v = -limit;
        return v;
    }

    int16_t position() const { return position_; }
    const KnobProfile& profile() const { return profile_; }

   private:
    static constexpr float kDamping = 0.02f;      // V per rad/s
    static constexpr float kEndstopGain = 4.0f;
    float width() const { return 2.0f * static_cast<float>(M_PI) / profile_.detents_per_rev; }
    float strength() const { return profile_.strength_pct / 100.0f; }

    float max_v_;
    KnobProfile profile_;
    float origin_ = 0.0f;
    int16_t position_ = 0;
};

// Push button: debounced press plus a long-press flag after `long_ms`.
class Button {
   public:
    Button(uint32_t debounce_ms, uint32_t long_ms) : db_(debounce_ms), long_ms_(long_ms) {}
    void update(uint32_t now, bool raw_pressed) {
        const bool p = db_.update(now, raw_pressed);
        if (p && !pressed_) since_ = now;
        pressed_ = p;
        long_ = p && elapsed(now, since_) >= long_ms_;
    }
    bool pressed() const { return pressed_; }
    bool long_press() const { return long_; }

   private:
    Debounce db_;
    uint32_t long_ms_;
    bool pressed_ = false;
    bool long_ = false;
    uint32_t since_ = 0;
};

// Manual Pi power-on: with the Pi off, reaching position 1 or a press asserts
// PI_ON_REQ for `pulse_ms` (power design: >= 100 ms), then waits `cooldown_ms`.
class PowerOnRequest {
   public:
    PowerOnRequest(uint32_t pulse_ms, uint32_t cooldown_ms) : pulse_(pulse_ms), cooldown_ms_(cooldown_ms) {}
    bool update(uint32_t now, bool pi_present, int16_t position, bool pressed) {
        const bool wants = !pi_present && (position >= 1 || pressed);
        if (wants && !pulse_.active() && (!fired_ || elapsed(now, fired_at_) >= cooldown_ms_)) {
            pulse_.trigger(now);
            fired_ = true;
            fired_at_ = now;
        }
        return pulse_.update(now);
    }

   private:
    Pulse pulse_;
    uint32_t cooldown_ms_;
    bool fired_ = false;
    uint32_t fired_at_ = 0;
};

// Motor enable gate (design-knob-node §16): on after VM >= on_v for hold_ms with the
// driver's nFAULT high; off immediately when VM < off_v or nFAULT goes low.
class MotorGate {
   public:
    MotorGate(float on_v, float off_v, uint32_t hold_ms) : on_v_(on_v), off_v_(off_v), hold_ms_(hold_ms) {}
    bool update(uint32_t now, float vm, bool fault_ok) {
        if (enabled_) {
            if (vm < off_v_ || !fault_ok) enabled_ = false;
        } else if (vm >= on_v_ && fault_ok) {
            if (!pending_) {
                pending_ = true;
                since_ = now;
            } else if (elapsed(now, since_) >= hold_ms_) {
                enabled_ = true;
            }
        }
        if (!(vm >= on_v_ && fault_ok)) pending_ = false;
        return enabled_;
    }
    bool enabled() const { return enabled_; }

   private:
    float on_v_, off_v_;
    uint32_t hold_ms_;
    bool enabled_ = false, pending_ = false;
    uint32_t since_ = 0;
};

// Motor heating limit (design-knob-node §6.2): the running mean of U_q^2 over about
// `window_s` must stay <= limit_v^2. Short haptic bursts at full voltage pass; holding
// full torque against an end stop is throttled down to limit_v RMS.
class ThermalLimiter {
   public:
    ThermalLimiter(float limit_v, float window_s)
        : limit_v_(limit_v), limit_sq_(limit_v * limit_v), tau_s_(window_s) {}
    // Returns the voltage actually applied for the requested one. Once the running mean
    // reaches the limit, |U| is clamped to limit_v (the mean then settles exactly there);
    // the clamp releases below 90 % of the limit, so it never chatters (felt as buzzing).
    float apply(float requested_v, float dt_s) {
        if (mean_sq_ >= limit_sq_) clamped_ = true;
        else if (mean_sq_ < 0.9f * limit_sq_) clamped_ = false;
        float v = requested_v;
        if (clamped_) {
            if (v > limit_v_) v = limit_v_;
            if (v < -limit_v_) v = -limit_v_;
        }
        mean_sq_ += (v * v - mean_sq_) * (dt_s / tau_s_);
        return v;
    }
    float rms() const { return sqrtf(mean_sq_); }

   private:
    float limit_v_, limit_sq_, tau_s_;
    bool clamped_ = false;
    float mean_sq_ = 0.0f;
};

}  // namespace logic
