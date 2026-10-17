// Bench build only (env main-node-bench, -DMAIN_BENCH): replaces the car with a
// serial console so the real firmware can be exercised on a bare ESP32.
//
//   in:  "ig1 1", "head 0", "rev 1", "pi 1" (simulate Pi heartbeat), "req 0 1"
//        (Pi relay request O02..O04 = index 0..2), "auto 0" (Pi lights-off enable)
//   out: "T=<ms> OUT set=<0|1> reset=<0|1> o=<O02 O03 O04>" on every output change,
//        "T=<ms> EVENT sleep" where the real node would enter deep sleep.
#pragma once

#include <Arduino.h>

namespace bench {

static bool ig1 = false, head = false, rev = false;
static bool pi_on = false, auto_en = true;
static bool req[3] = {false, false, false};

inline void poll_serial() {
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
        else if (cmd == "req") {
            const int i = arg.charAt(0) - '0';
            if (i >= 0 && i < 3) req[i] = v;
        }
        Serial.printf("T=%lu ACK %s\n", (unsigned long)millis(), line.c_str());
        line = "";
    }
}

inline void log_outputs(uint32_t now, bool set, bool reset, const bool o[3]) {
    static int last = -1;
    const int code = set | reset << 1 | o[0] << 2 | o[1] << 3 | o[2] << 4;
    if (code == last) return;
    last = code;
    Serial.printf("T=%lu OUT set=%d reset=%d o=%d%d%d\n", (unsigned long)now, set, reset, o[0], o[1], o[2]);
}

}  // namespace bench
