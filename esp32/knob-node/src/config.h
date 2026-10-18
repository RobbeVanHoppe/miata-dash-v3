// Knob node pin map and constants, from miata-hw docs/design-knob-node.md (§16 firmware
// notes). Motor: iFlight GBM2804H-100T (7 pole pairs, 10 ohm); driver: SimpleFOC Mini v1.1.
#pragma once

#include "driver/gpio.h"

namespace cfg {

// CAN (transceiver breakout; its 120 ohm removed: the knob is a stub)
constexpr gpio_num_t kCanTx = GPIO_NUM_17;
constexpr gpio_num_t kCanRx = GPIO_NUM_16;

// AS5600 magnetic angle sensor on local I²C (on-board, < 30 cm)
constexpr int kSda = 21;
constexpr int kScl = 22;

// SimpleFOC Mini (DRV8313)
constexpr int kPwmA = 25;
constexpr int kPwmB = 26;
constexpr int kPwmC = 27;
constexpr int kMotorEnable = 4;   // GPIO4: pulled low at reset (GPIO14 would enable the driver at boot)
constexpr int kDrvNFault = 34;    // input only, external pull-up; low = driver fault
constexpr int kDrvNReset = 19;    // high = run; low pulse >= 1 ms clears a latched fault

// Motor
constexpr int kPolePairs = 7;
constexpr float kSupplyV = 9.0f;      // D36V6F9 rail
constexpr float kMaxTorqueV = 3.0f;   // peak (0.60 A nominal, 0.85 A cold)
constexpr float kThermalLimitV = 1.3f;   // 60 s RMS limit: winding stays within the motor's rating
constexpr float kThermalWindowS = 60.0f;

// VM sense: 130 k / 10.0 k divider (k = 0.07143), ADC1 at 0 dB attenuation
constexpr int kVmSensePin = 33;
constexpr float kVmDivider = 10.0f / 140.0f;
constexpr float kVmOnV = 8.3f;        // DRV8313 UVLO can release as late as 8.0 V
constexpr float kVmOffV = 7.9f;
constexpr uint32_t kVmOnHoldMs = 200;

// Push button (external pull-up) and PI_ON_REQ N-FET gate (high = request)
constexpr int kButton = 32;
constexpr int kPiOnReqGate = 13;

// Timing
constexpr uint32_t kStatePeriodMs = 50;      // KNOB_STATE at 20 Hz (plus on change)
constexpr uint32_t kPiAbsentMs = 2000;       // no PI_STATUS for this long = Pi off
constexpr uint32_t kLongPressMs = 800;
constexpr uint32_t kPiOnPulseMs = 300;       // power design: >= 100 ms
constexpr uint32_t kPiOnCooldownMs = 10000;

}  // namespace cfg
