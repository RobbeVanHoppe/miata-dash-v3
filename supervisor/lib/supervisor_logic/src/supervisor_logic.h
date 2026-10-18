// Power-supervisor decision logic (miata-hw docs/design-power.md §10), hardware-free
// and integer-only so it fits the ATtiny84A and also runs in the ESP32 bench tests.
//
// It decides when the Pi gets its 5 V (and the supercap hold-up), when it is asked to
// shut down, and when the knob rail is on. Owner rules behind it: the Pi stays off
// during cranking and only boots once the engine runs; after the key goes off it shuts
// down cleanly and its power is cut only well after it has halted.
#pragma once

#include <stdint.h>

namespace sup {

inline uint32_t elapsed(uint32_t now, uint32_t since) { return now - since; }

// Parameters (§10.1). [P] = placeholder until the battery measurements are in.
struct Params {
    uint16_t v_run_mv = 13200;        // engine running = alternator charging [P]
    uint32_t t_run_ms = 3000;         // decided
    uint16_t v_lv_mv = 11800;         // low-battery shutdown threshold [P]
    uint32_t t_lv_ms = 30000;         // [P]
    uint16_t v_rearm_mv = 12100;      // manual Pi-on needs at least this [P]
    uint32_t t_deb_ms = 10000;        // key off this long before shutting the Pi down
    uint32_t t_req_ms = 100;          // PI_ON_REQ must be held this long
    uint32_t t_stable_ms = 2000;      // POWEROFF_DONE high this long = Pi halted
    uint32_t t_post_ms = 10000;       // extra margin after the Pi halted
    uint32_t t_lr_ms = 300000;        // last-resort forced cut after a shutdown request (5 min)
    uint32_t t_knob_hold_ms = 5000;   // knob rail stays on this long after the key [P]
    uint16_t bank_alive_mv = 3000;    // power-on recovery: bank charged = Pi was probably on
};

struct Inputs {
    bool ign = false;     // IG1 on
    bool acc = false;     // ACC on
    bool req = false;     // PI_ON_REQ from the knob (only meaningful with the knob rail on)
    bool pwroff = false;  // POWEROFF_DONE from the Pi (GPIO26 high = halted)
    uint16_t vbat_mv = 0;
    uint16_t bank_mv = 0;
};

struct Outputs {
    bool en5v = false;
    bool hold = false;
    bool shdn = false;
    bool knob = false;
};

enum class State : uint8_t { Off, WaitRun, On, OnManual, Shutdown, PostWait };

class Supervisor {
   public:
    explicit Supervisor(const Params& p = Params()) : p_(p) {}

    State state() const { return state_; }
    bool lowbat_lockout() const { return lockout_; }

    Outputs update(uint32_t now, const Inputs& in) {
        const bool key = in.ign || in.acc;
        // Knob rail: on with the key, plus a hold so ACC dropping in START does not reset it.
        if (key) key_seen_ = now, key_ever_ = true;
        const bool knob = key || (key_ever_ && elapsed(now, key_seen_) < p_.t_knob_hold_ms);
        const bool req = knob && held(in.req, now, req_since_, req_on_, p_.t_req_ms);
        const bool running = held(in.vbat_mv > p_.v_run_mv, now, run_since_, run_on_, p_.t_run_ms);
        const bool lowbat = held(in.vbat_mv < p_.v_lv_mv, now, lv_since_, lv_on_, p_.t_lv_ms);
        if (running) lockout_ = false;

        switch (state_) {
            case State::Off:
                if (in.ign) enter(State::WaitRun, now);
                else if (req && in.vbat_mv >= p_.v_rearm_mv) enter(State::OnManual, now);
                break;
            case State::WaitRun:
                if (!in.ign) enter(State::Off, now);
                else if (running) enter(State::On, now);
                else if (req && in.vbat_mv >= p_.v_rearm_mv) enter(State::OnManual, now);
                break;
            case State::On:
                if (lowbat) shutdown(now, true);
                else if (off_for(in.ign, now, p_.t_deb_ms)) shutdown(now, false);
                break;
            case State::OnManual:
                if (lowbat) shutdown(now, true);
                else if (running) enter(State::On, now);
                else if (off_for(key, now, p_.t_deb_ms)) shutdown(now, false);
                break;
            case State::Shutdown:
                // POWEROFF_DONE only counts after our request (the overlay also toggles on reboot).
                if (held(in.pwroff, now, pwroff_since_, pwroff_on_, p_.t_stable_ms)) enter(State::PostWait, now);
                else if (elapsed(now, since_) >= p_.t_lr_ms) enter(State::Off, now);  // hung Pi: forced cut
                break;
            case State::PostWait:
                if (elapsed(now, since_) >= p_.t_post_ms) enter(State::Off, now);
                break;
        }

        Outputs out;
        const bool pi_powered = state_ == State::On || state_ == State::OnManual || state_ == State::Shutdown ||
                                state_ == State::PostWait;
        out.en5v = out.hold = pi_powered;
        out.shdn = state_ == State::Shutdown || state_ == State::PostWait;
        out.knob = knob;
        return out;
    }

    // Deep sleep is allowed only with everything off and no key.
    bool can_sleep(const Outputs& o) const { return state_ == State::Off && !o.knob; }

    // §10.5: after a power-on reset (no valid saved state): Pi probably on if IG1 is on
    // and the bank is charged -> keep it on rather than cutting it mid-run.
    void power_on_init(uint32_t now, const Inputs& in) {
        enter(in.ign && in.bank_mv > p_.bank_alive_mv ? State::On : State::Off, now);
    }
    void restore(State s, uint32_t now) { enter(s, now); }

   private:
    void enter(State s, uint32_t now) {
        state_ = s;
        since_ = now;
        pwroff_on_ = false;
        key_off_on_ = false;
    }
    void shutdown(uint32_t now, bool low) {
        if (low) lockout_ = true;
        enter(State::Shutdown, now);
    }
    // True once `cond` has been true continuously for `ms`.
    static bool held(bool cond, uint32_t now, uint32_t& since, bool& on, uint32_t ms) {
        if (!cond) {
            on = false;
            return false;
        }
        if (!on) {
            on = true;
            since = now;
        }
        return elapsed(now, since) >= ms;
    }
    bool off_for(bool active, uint32_t now, uint32_t ms) { return held(!active, now, key_off_since_, key_off_on_, ms); }

    Params p_;
    State state_ = State::Off;
    uint32_t since_ = 0;
    uint32_t key_seen_ = 0, req_since_ = 0, run_since_ = 0, lv_since_ = 0, pwroff_since_ = 0, key_off_since_ = 0;
    bool key_ever_ = false, req_on_ = false, run_on_ = false, lv_on_ = false, pwroff_on_ = false, key_off_on_ = false;
    bool lockout_ = false;
};

}  // namespace sup
