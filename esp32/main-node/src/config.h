// Main node pin map and constants.
//
// PROVISIONAL: the pin numbers below are placeholders until
// miata-hw/docs/design-main-node.md is final; replace them from its GPIO map.
// Rules kept even for placeholders (CLAUDE.md): inputs avoid strapping pins
// 0/2/5/12/15, analog inputs use ADC1 (GPIO32-39), wake inputs are RTC GPIOs.
#pragma once

#include "driver/gpio.h"

namespace cfg {

// CAN (transceiver breakout)
constexpr gpio_num_t kCanTx = GPIO_NUM_17;
constexpr gpio_num_t kCanRx = GPIO_NUM_16;
constexpr gpio_num_t kCanPower = GPIO_NUM_4;  // high = transceiver powered (off in deep sleep)

// Inputs, after the protection/RC stage on the board. Active level PROVISIONAL.
constexpr gpio_num_t kIg1 = GPIO_NUM_34;      // RTC GPIO: wake source
constexpr gpio_num_t kHead = GPIO_NUM_35;     // stalk HEAD line, sensed upstream of RLY1; RTC GPIO
constexpr gpio_num_t kReverse = GPIO_NUM_39;
constexpr bool kIg1ActiveHigh = true;
constexpr bool kHeadActiveHigh = true;
constexpr bool kReverseActiveHigh = true;

// Battery sense: ADC1 channel, divider ratio from the design doc.
constexpr int kVbatPin = 36;               // ADC1_CH0
constexpr float kVbatDivider = 0.0f;       // PROVISIONAL: V_pin / V_bat; 0 = not fitted, report 0 V

// Outputs (low-side coil drivers, default off with pull-downs on the board)
constexpr gpio_num_t kRelayO02 = GPIO_NUM_26;
constexpr gpio_num_t kRelayO03 = GPIO_NUM_27;
constexpr gpio_num_t kRelayO04 = GPIO_NUM_33;
constexpr gpio_num_t kRly1Set = GPIO_NUM_32;    // latching relay SET coil: lights off
constexpr gpio_num_t kRly1Reset = GPIO_NUM_25;  // latching relay RESET coil: factory

// Timing
constexpr uint32_t kDebounceMs = 30;
constexpr uint32_t kPiTimeoutMs = 1000;    // release Pi relays after this without PI_MAIN_CMD
constexpr uint32_t kCoilPulseMs = 50;      // PROVISIONAL: latching relay pulse, from relay datasheet
constexpr uint32_t kStatusPeriodMs = 100;  // MAIN_STATUS at 10 Hz
constexpr uint32_t kSleepAfterMs = 5000;   // key off and nothing pending for this long -> deep sleep

}  // namespace cfg
