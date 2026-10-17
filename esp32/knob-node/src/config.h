// Knob node pin map and constants.
//
// PROVISIONAL: placeholders until miata-hw/docs/design-knob-node.md exists;
// replace from its GPIO map. Motor values are typical for a 2204/2804 gimbal
// motor and must be checked against the motor actually fitted.
#pragma once

#include "driver/gpio.h"

namespace cfg {

// CAN (transceiver breakout; its 120 ohm must be removed: the knob is a stub)
constexpr gpio_num_t kCanTx = GPIO_NUM_17;
constexpr gpio_num_t kCanRx = GPIO_NUM_16;

// AS5600 magnetic angle sensor on local I²C (on-board, < 30 cm)
constexpr int kSda = 21;
constexpr int kScl = 22;

// SimpleFOC Mini (DRV8313): three PWM inputs + enable
constexpr int kPwmA = 25;
constexpr int kPwmB = 26;
constexpr int kPwmC = 27;
constexpr int kMotorEnable = 14;  // pull-down on the board: motor off at boot

// Motor (PROVISIONAL)
constexpr int kPolePairs = 7;
constexpr float kSupplyV = 9.0f;      // regulated motor rail (architecture K3)
constexpr float kMaxTorqueV = 3.0f;   // voltage at 100 % haptic strength; limit heating

// 9 V motor rail sense (ADC1), divider from the design doc
constexpr int kVmSensePin = 33;
constexpr float kVmDivider = 0.0f;    // PROVISIONAL: V_pin / V_motor; 0 = not fitted, assume present
constexpr float kVmMinV = 7.5f;       // below this the motor is treated as unpowered

// Push button (to GND, internal pull-up) and PI_ON_REQ N-FET gate (high = request)
constexpr int kButton = 32;
constexpr int kPiOnReqGate = 13;

// Timing
constexpr uint32_t kStatePeriodMs = 50;      // KNOB_STATE at 20 Hz (plus on change)
constexpr uint32_t kPiAbsentMs = 2000;       // no PI_STATUS for this long = Pi off
constexpr uint32_t kLongPressMs = 800;
constexpr uint32_t kPiOnPulseMs = 300;       // power design: >= 100 ms
constexpr uint32_t kPiOnCooldownMs = 10000;

}  // namespace cfg
