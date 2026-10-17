// Hardware-free logic for the ESP32 nodes. No Arduino calls: everything takes
// the time in milliseconds and plain booleans, so it can be unit-tested on a PC.
#pragma once

#include <stdint.h>

namespace logic {

// Signed difference that survives millis() wrap-around (every ~49.7 days).
inline uint32_t elapsed(uint32_t now, uint32_t since) { return now - since; }

// A digital input must hold a new level for `stable_ms` before it is accepted.
class Debounce {
   public:
    explicit Debounce(uint32_t stable_ms, bool initial = false)
        : stable_ms_(stable_ms), state_(initial), candidate_(initial) {}

    bool update(uint32_t now, bool raw) {
        if (raw != candidate_) {
            candidate_ = raw;
            since_ = now;
        } else if (candidate_ != state_ && elapsed(now, since_) >= stable_ms_) {
            state_ = candidate_;
        }
        return state_;
    }
    bool state() const { return state_; }

   private:
    uint32_t stable_ms_;
    bool state_;
    bool candidate_;
    uint32_t since_ = 0;
};

// The Pi's PI_MAIN_CMD doubles as its heartbeat.
class Heartbeat {
   public:
    explicit Heartbeat(uint32_t timeout_ms) : timeout_ms_(timeout_ms) {}
    void feed(uint32_t now) {
        last_ = now;
        seen_ = true;
    }
    bool alive(uint32_t now) const { return seen_ && elapsed(now, last_) < timeout_ms_; }

   private:
    uint32_t timeout_ms_;
    uint32_t last_ = 0;
    bool seen_ = false;
};

// Auto lights-off (owner decision, miata-hw docs/requirements.md):
//  - 3 s after IG1 goes OFF, if the stalk is in HEAD: pulse the SET coil of the
//    latching relay RLY1, which opens the stalk's HEAD line -> lights off, pop-ups retract.
//  - An engine stall with the key on does nothing (IG1 stays on).
//  - Override: any movement of the stalk while latched pulses RESET -> lights follow the stalk.
//  - Key-on always returns to factory: the hardware resets RLY1 passively from IG1;
//    the firmware also pulses RESET (belt and braces).
class LightsOff {
   public:
    enum class State : uint8_t { Idle, Countdown, Latched, Done };
    struct Out {
        bool pulse_set = false;
        bool pulse_reset = false;
    };
    static constexpr uint32_t kDelayMs = 3000;

    Out update(uint32_t now, bool ig1, bool head_on, bool enabled) {
        Out out;
        if (ig1) {
            if (state_ == State::Latched) out.pulse_reset = true;
            state_ = State::Idle;
        } else {
            switch (state_) {
                case State::Idle:
                    if (prev_ig1_) {  // falling edge: key just switched off
                        state_ = State::Countdown;
                        start_ = now;
                    }
                    break;
                case State::Countdown:
                    if (elapsed(now, start_) >= kDelayMs) {
                        if (enabled && head_on) {
                            out.pulse_set = true;
                            head_at_latch_ = head_on;
                            state_ = State::Latched;
                        } else {
                            state_ = State::Done;  // nothing to switch off
                        }
                    }
                    break;
                case State::Latched:
                    if (head_on != head_at_latch_) {  // driver moved the stalk: override
                        out.pulse_reset = true;
                        state_ = State::Done;
                    }
                    break;
                case State::Done:
                    break;
            }
        }
        prev_ig1_ = ig1;
        return out;
    }

    State state() const { return state_; }
    bool latched() const { return state_ == State::Latched; }
    // True while the node must stay awake to finish something.
    bool busy() const { return state_ == State::Countdown; }

    // Deep sleep keeps these in RTC memory: restore after wake-up.
    struct Snapshot {
        State state;
        bool prev_ig1;
        bool head_at_latch;
    };
    Snapshot save() const { return {state_, prev_ig1_, head_at_latch_}; }
    void restore(const Snapshot& s) {
        state_ = s.state == State::Countdown ? State::Idle : s.state;  // never sleep mid-countdown
        prev_ig1_ = s.prev_ig1;
        head_at_latch_ = s.head_at_latch;
    }

   private:
    State state_ = State::Idle;
    bool prev_ig1_ = false;  // power-up with the key off is not a key-off event
    bool head_at_latch_ = false;
    uint32_t start_ = 0;
};

// One-shot output pulse for a latching-relay coil.
class Pulse {
   public:
    explicit Pulse(uint32_t length_ms) : length_ms_(length_ms) {}
    void trigger(uint32_t now) {
        active_ = true;
        start_ = now;
    }
    bool update(uint32_t now) {
        if (active_ && elapsed(now, start_) >= length_ms_) active_ = false;
        return active_;
    }
    bool active() const { return active_; }

   private:
    uint32_t length_ms_;
    bool active_ = false;
    uint32_t start_ = 0;
};

}  // namespace logic
