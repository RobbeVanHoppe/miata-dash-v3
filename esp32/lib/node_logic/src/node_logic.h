// Hardware-free logic for the ESP32 nodes. No Arduino calls: everything takes
// the time in milliseconds and plain booleans, so it can be unit-tested.
//
// Main-node behaviour follows miata-hw docs/design-main-node.md §13.
#pragma once

#include <stdint.h>

namespace logic {

// Difference that survives millis() wrap-around (every ~49.7 days).
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

// One-shot output pulse.
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

// Main_Faults bits (design-main-node §13.6). Sticky until the next IG1-on.
namespace fault {
constexpr uint8_t kSetFailed = 1 << 0;
constexpr uint8_t kDroppedOut = 1 << 1;       // relay released on its own while latched
constexpr uint8_t kSetWithIg1 = 1 << 2;       // RLY1 found SET with IG1 on
constexpr uint8_t kVbatLow = 1 << 3;
constexpr uint8_t kVbatHigh = 1 << 4;
constexpr uint8_t kBusOff = 1 << 5;
constexpr uint8_t kUnexpectedReset = 1 << 6;
constexpr uint8_t kAutoDefault = 1 << 7;      // auto lights-off still the first-boot default
}  // namespace fault

// PI_MAIN_CMD doubles as the Pi heartbeat (§13.6):
//  - Pi_Counter must advance by 1 per frame; a repeated counter counts as missing
//    (a stuck Pi application keeps sending the same frame).
//  - Alive only after 3 consecutive good frames (0.3 s), so one stray frame cannot
//    re-energise a relay; lost again after `timeout_ms` without a good frame.
class Heartbeat {
   public:
    explicit Heartbeat(uint32_t timeout_ms, uint8_t acquire_frames = 3)
        : timeout_ms_(timeout_ms), acquire_(acquire_frames) {}

    void feed(uint32_t now, uint8_t counter) {
        if (has_counter_ && counter == last_counter_) return;  // stale frame: ignore
        const bool consecutive = has_counter_ && counter == static_cast<uint8_t>(last_counter_ + 1);
        streak_ = consecutive ? streak_ + 1 : 1;
        last_counter_ = counter;
        has_counter_ = true;
        if (alive_ && !consecutive) {  // gap or jump while alive: still a valid frame
            last_good_ = now;
            return;
        }
        if (streak_ >= acquire_) alive_ = true;
        if (alive_) last_good_ = now;
    }

    bool alive(uint32_t now) {
        if (alive_ && elapsed(now, last_good_) >= timeout_ms_) {
            alive_ = false;
            streak_ = 0;
        }
        return alive_;
    }

    void reset() {
        alive_ = false;
        streak_ = 0;
        has_counter_ = false;
    }

   private:
    uint32_t timeout_ms_;
    uint8_t acquire_;
    bool alive_ = false;
    bool has_counter_ = false;
    uint8_t last_counter_ = 0;
    uint8_t streak_ = 0;
    uint32_t last_good_ = 0;
};

// Drives one latching-relay coil pulse with supply settle, verification against
// the feedback contact, and retries (§13.4):
//   VRLY on -> 5 ms -> EN for 100 ms -> wait up to 50 ms for feedback -> VRLY off;
//   failed: retry after 500 ms, at most 3 attempts.
class CoilSequencer {
   public:
    enum class Result : uint8_t { Busy, Done, Failed };
    struct Drive {
        bool vrly = false;
        bool set = false;
        bool reset = false;
    };
    static constexpr uint32_t kSettleMs = 5, kPulseMs = 100, kVerifyMs = 50, kRetryGapMs = 500;
    static constexpr uint8_t kAttempts = 3;

    void start(uint32_t now, bool set_coil) {
        set_coil_ = set_coil;
        attempt_ = 1;
        enter(Phase::Settle, now);
    }
    void abort() { phase_ = Phase::Idle; }
    bool busy() const { return phase_ != Phase::Idle; }

    // `fb_set` = feedback contact says RLY1 is in the SET (lights-off) position.
    Result update(uint32_t now, bool fb_set, Drive& out) {
        out = Drive{};
        const bool reached = set_coil_ ? fb_set : !fb_set;
        switch (phase_) {
            case Phase::Idle:
                return Result::Done;
            case Phase::Settle:
                out.vrly = true;
                if (elapsed(now, since_) >= kSettleMs) enter(Phase::Pulse, now);
                return Result::Busy;
            case Phase::Pulse:
                out.vrly = true;
                if (elapsed(now, since_) >= kPulseMs) {
                    enter(Phase::Verify, now);
                } else {
                    (set_coil_ ? out.set : out.reset) = true;
                }
                return Result::Busy;
            case Phase::Verify:
                out.vrly = true;
                if (reached) {
                    phase_ = Phase::Idle;
                    out.vrly = false;
                    return Result::Done;
                }
                if (elapsed(now, since_) >= kVerifyMs) {
                    out.vrly = false;
                    if (attempt_ >= kAttempts) {
                        phase_ = Phase::Idle;
                        return Result::Failed;
                    }
                    attempt_++;
                    enter(Phase::Gap, now);
                }
                return Result::Busy;
            case Phase::Gap:
                if (elapsed(now, since_) >= kRetryGapMs) enter(Phase::Settle, now);
                return Result::Busy;
        }
        return Result::Busy;
    }

   private:
    enum class Phase : uint8_t { Idle, Settle, Pulse, Verify, Gap };
    void enter(Phase p, uint32_t now) {
        phase_ = p;
        since_ = now;
    }
    Phase phase_ = Phase::Idle;
    bool set_coil_ = true;
    uint8_t attempt_ = 0;
    uint32_t since_ = 0;
};

// Auto lights-off (owner decision; design-main-node §13.4):
//  - 3 s after IG1 goes OFF, if enabled and the stalk is in HEAD: SET RLY1, which opens
//    the stalk's HEAD line -> headlights off and pop-ups retract.
//  - An engine stall with the key on does nothing (IG1 stays on).
//  - Override: the driver moving the stalk off HEAD while latched -> RESET; turning it
//    back to HEAD is then pure factory behaviour.
//  - Key-on returns to factory: the hardware self-resets RLY1 from IG1; if the feedback
//    still says SET, the firmware pulses RESET too and records a fault.
class LightsOff {
   public:
    enum class State : uint8_t { Run, KeyoffTiming, SetPulse, Latched, OverrideReset, KeyoffIdle };
    static constexpr uint32_t kDelayMs = 3000;
    static constexpr uint32_t kKeyOnGraceMs = 200;  // let the hardware IG1 self-reset act first

    LightsOff::State state() const { return state_; }
    bool latched() const { return state_ == State::Latched; }
    uint8_t faults() const { return faults_; }
    void add_fault(uint8_t bits) { faults_ |= bits; }

    // Called every loop. Returns the coil drive for this instant.
    CoilSequencer::Drive update(uint32_t now, bool ig1, bool head, bool fb_set, bool enabled) {
        CoilSequencer::Drive drive;
        if (ig1 && state_ != State::Run) {
            seq_.abort();  // key-on cancels any pending action (hardware also inhibits SET)
            enter(State::Run, now);
            faults_ = 0;  // sticky until the next IG1-on
            key_on_reset_checked_ = false;
        }
        switch (state_) {
            case State::Run:
                if (!ig1) {
                    seq_.abort();
                    enter(State::KeyoffTiming, now);
                    break;
                }
                if (!key_on_reset_checked_ && !seq_.busy() && elapsed(now, since_) >= kKeyOnGraceMs) {
                    key_on_reset_checked_ = true;
                    if (fb_set) {
                        faults_ |= fault::kSetWithIg1;
                        seq_.start(now, false);
                    }
                }
                if (seq_.busy()) seq_.update(now, fb_set, drive);
                break;
            case State::KeyoffTiming:
                if (elapsed(now, since_) >= kDelayMs) {
                    if (enabled && head) {
                        seq_.start(now, true);
                        enter(State::SetPulse, now);
                    } else {
                        enter(State::KeyoffIdle, now);
                    }
                }
                break;
            case State::SetPulse: {
                const auto r = seq_.update(now, fb_set, drive);
                if (r == CoilSequencer::Result::Done) enter(State::Latched, now);
                if (r == CoilSequencer::Result::Failed) {
                    faults_ |= fault::kSetFailed;
                    enter(State::KeyoffIdle, now);
                }
                break;
            }
            case State::Latched:
                if (!head) {
                    seq_.start(now, false);
                    enter(State::OverrideReset, now);
                }
                break;
            case State::OverrideReset: {
                const auto r = seq_.update(now, fb_set, drive);
                if (r != CoilSequencer::Result::Busy) enter(State::KeyoffIdle, now);
                break;
            }
            case State::KeyoffIdle:
                break;
        }
        return drive;
    }

    // After a timer wake while latched (§13.4 HOLD_CHECK).
    void hold_check(uint32_t now, bool head, bool fb_set) {
        if (state_ != State::Latched) return;
        if (fb_set) return;
        if (head) {  // relay dropped out on its own: headlights would be on
            faults_ |= fault::kDroppedOut;
            seq_.start(now, true);
            enter(State::SetPulse, now);
        } else {
            enter(State::KeyoffIdle, now);
        }
    }

    // Key off, nothing pending, settled for `awake_ms` -> the node may deep-sleep.
    bool can_sleep(uint32_t now, uint32_t awake_ms) const {
        return (state_ == State::Latched || state_ == State::KeyoffIdle) && !seq_.busy() &&
               elapsed(now, since_) >= awake_ms;
    }

    // Deep sleep keeps this in RTC memory.
    struct Snapshot {
        State state;
        uint8_t faults;
    };
    Snapshot save() const { return {state_, faults_}; }
    void restore(const Snapshot& s, uint32_t now) {
        state_ = (s.state == State::Latched) ? State::Latched : State::KeyoffIdle;
        faults_ = s.faults;
        since_ = now;
    }

   private:
    void enter(State s, uint32_t now) {
        state_ = s;
        since_ = now;
    }
    State state_ = State::KeyoffIdle;  // power-up with the key off is not a key-off event
    CoilSequencer seq_;
    uint32_t since_ = 0;
    uint8_t faults_ = 0;
    bool key_on_reset_checked_ = false;
};

}  // namespace logic
