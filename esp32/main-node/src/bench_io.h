// Bench build only (env main-node-bench, -DMAIN_BENCH): replaces the car with a
// serial console so the real firmware runs on a bare ESP32 (nothing wired to it).
//
// Commands (one per line):
//   ig1 0|1        key                  head 0|1     stalk in HEAD
//   rev 0|1        reverse              pi 0|1       simulate the Pi heartbeat (10 Hz)
//   req N 0|1      Pi relay request O02..O04 (N = 0..2)
//   auto 0|1       Pi_AutoLightsOff_En  vbat <volt>  battery voltage
//   stuck 0|1      RLY1 ignores its coils (tests retries)
//   selfreset 0|1  hardware IG1 self-reset of RLY1 present (default 1)
//   drop           RLY1 releases on its own   wake       simulate the 6 h timer wake
// Output: "T=<ms> OUT vrly=. set=. reset=. o=... fb=." on change, "T=<ms> EVENT <name>".
//
// RLY1 is simulated: a coil energised with its supply for >= 30 ms moves the
// contact (the G2RK-2 minimum pulse), as the real relay's feedback contact would.
#pragma once

#include <Arduino.h>

#include "canlink.h"
#include "miata.h"
#include "node_logic.h"

namespace bench {

static bool ig1 = false, head = false, rev = false;
static bool pi_on = false, auto_en = true;
static bool req[3] = {false, false, false};
static float vbat = 12.6f;
static bool relay_set = false, stuck = false, selfreset = true;
static bool timer_wake = false, sleep_reported = false;

static void event(uint32_t now, const char* name) { Serial.printf("T=%lu EVENT %s\n", (unsigned long)now, name); }

static void poll_serial() {
    static String line;
    while (Serial.available()) {
        const char c = static_cast<char>(Serial.read());
        if (c != '\n' && c != '\r') {
            line += c;
            continue;
        }
        if (line.length() == 0) continue;
        const int sp = line.indexOf(' ');
        const String cmd = sp < 0 ? line : line.substring(0, sp);
        const String arg = sp < 0 ? "" : line.substring(sp + 1);
        const bool v = arg.endsWith("1");
        if (cmd == "ig1") ig1 = v;
        else if (cmd == "head") head = v;
        else if (cmd == "rev") rev = v;
        else if (cmd == "pi") pi_on = v;
        else if (cmd == "auto") auto_en = v;
        else if (cmd == "vbat") vbat = arg.toFloat();
        else if (cmd == "stuck") stuck = v;
        else if (cmd == "selfreset") selfreset = v;
        else if (cmd == "drop") relay_set = false;
        else if (cmd == "wake") timer_wake = true;
        else if (cmd == "req") {
            const int i = arg.charAt(0) - '0';
            if (i >= 0 && i < 3) req[i] = v;
        }
        Serial.printf("T=%lu ACK %s\n", (unsigned long)millis(), line.c_str());
        line = "";
    }
}

static bool take_timer_wake() {
    const bool w = timer_wake;
    timer_wake = false;
    return w;
}

// Simulated PI_MAIN_CMD at 10 Hz, packed with the generated code.
static bool next_pi_frame(uint32_t now, canlink::Frame& f) {
    static uint32_t last = 0;
    static uint8_t counter = 0;
    if (!pi_on || logic::elapsed(now, last) < 100) return false;
    last = now;
    miata_pi_main_cmd_t m = {};
    m.pi_relay_req_o02 = req[0];
    m.pi_relay_req_o03 = req[1];
    m.pi_relay_req_o04 = req[2];
    m.pi_auto_lights_off_en = auto_en;
    m.pi_counter = counter++;
    f.id = MIATA_PI_MAIN_CMD_FRAME_ID;
    f.len = MIATA_PI_MAIN_CMD_LENGTH;
    miata_pi_main_cmd_pack(f.data, &m, sizeof f.data);
    return true;
}

// Called with the outputs every loop: simulate RLY1 and log changes.
static void drive(uint32_t now, bool vrly, bool set, bool reset, const bool relays[3]) {
    static uint32_t set_since = 0, reset_since = 0, ig1_since = 0;
    static bool set_prev = false, reset_prev = false, ig1_prev = false;
    if (set && !set_prev) set_since = now;
    if (reset && !reset_prev) reset_since = now;
    if (ig1 && !ig1_prev) ig1_since = now;
    set_prev = set;
    reset_prev = reset;
    ig1_prev = ig1;
    if (!stuck) {
        if (vrly && set && logic::elapsed(now, set_since) >= 30) relay_set = true;
        if (vrly && reset && logic::elapsed(now, reset_since) >= 30) relay_set = false;
        if (selfreset && ig1 && relay_set && logic::elapsed(now, ig1_since) >= 20) relay_set = false;
    }
    static int last = -1;
    const int code = vrly | set << 1 | reset << 2 | relays[0] << 3 | relays[1] << 4 | relays[2] << 5 | relay_set << 6;
    if (code == last) return;
    last = code;
    Serial.printf("T=%lu OUT vrly=%d set=%d reset=%d o=%d%d%d fb=%d\n", (unsigned long)now, vrly, set, reset,
                  relays[0], relays[1], relays[2], relay_set);
}

}  // namespace bench
