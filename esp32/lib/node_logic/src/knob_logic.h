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

}  // namespace logic
