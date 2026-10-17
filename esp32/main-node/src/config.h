// Main node pin map and constants, from miata-hw docs/design-main-node.md §13
// (ESP32-WROOM-32E bare module). Items marked Proposal are open owner questions (§16).
#pragma once

#include "driver/gpio.h"

namespace cfg {

// CAN: transceiver breakout, powered through a P-FET (low = on), off in deep sleep
constexpr gpio_num_t kCanTx = GPIO_NUM_21;
constexpr gpio_num_t kCanRx = GPIO_NUM_22;
constexpr gpio_num_t kCanPowerN = GPIO_NUM_25;

// Inputs (after series R + zener clamp + RC + Schmitt buffer on the board)
constexpr gpio_num_t kIgn = GPIO_NUM_34;      // high = IG1 on; ext1 wake
constexpr gpio_num_t kHeadN = GPIO_NUM_35;    // low = stalk in HEAD; ext1 wake while latched
constexpr gpio_num_t kRly1FbN = GPIO_NUM_32;  // low = RLY1 in SET (lights-off) position
constexpr gpio_num_t kRev = GPIO_NUM_33;
constexpr bool kRevActiveLow = false;          // JP4 open = active high
constexpr gpio_num_t kTnsN = GPIO_NUM_39;     // low = stalk in TNS or HEAD (reported only)

// Battery sense: 1.00 MΩ / 62.0 kΩ divider -> k = 0.05838 (§10)
constexpr int kVbatPin = 36;
constexpr float kVbatK = 62.0f / 1062.0f;
constexpr float kVbatOffsetV = 0.20f;  // PSB chain + cable drop under typical load (§10)

// Outputs (pull-downs on the board: off at reset)
constexpr gpio_num_t kRelayPins[3] = {GPIO_NUM_16, GPIO_NUM_17, GPIO_NUM_18};  // O02..O04
constexpr gpio_num_t kVrlyEn = GPIO_NUM_19;   // RLY1 pulse supply
constexpr gpio_num_t kSetEn = GPIO_NUM_23;    // SET coil (hardware-inhibited while IG1 on)
constexpr gpio_num_t kRstEn = GPIO_NUM_26;    // RESET coil

// Debounce (§13.2)
constexpr uint32_t kIgnDebounceMs = 50;
constexpr uint32_t kHeadDebounceMs = 50;
constexpr uint32_t kRevDebounceMs = 100;
constexpr uint32_t kFbDebounceMs = 20;

// Timing
constexpr uint32_t kPiTimeoutMs = 1000;        // §13.6 heartbeat (Proposal, Q6)
constexpr uint32_t kStatusPeriodMs = 100;      // MAIN_STATUS at 10 Hz
constexpr uint32_t kAwakeKeyoffMs = 10000;     // §13.4 T_AWAKE_KEYOFF (Proposal)
constexpr uint64_t kHoldCheckUs = 6ULL * 3600 * 1000000;  // §13.4 T_CHECK 6 h (Proposal, Q7)
constexpr bool kReleaseRelaysAtKeyOff = true;  // Proposal, Q4
constexpr uint32_t kWatchdogS = 2;

// Battery faults (§13.6)
constexpr float kVbatLowV = 11.0f;             // [P]
constexpr uint32_t kVbatLowMs = 30000;
constexpr float kVbatHighV = 16.0f;

}  // namespace cfg
