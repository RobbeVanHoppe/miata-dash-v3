// Power supervisor firmware for the PSB's ATtiny84A (miata-hw docs/design-power.md §6, §10).
// Register-level avr-libc, 1 MHz internal RC. Decision logic: lib/supervisor_logic.
//
// Pins (§6.1, rev 3; every output active-high, low/Hi-Z/reset = OFF):
//   PB0 ACC_N (low = ACC)   PB1 PI_ON_N (high = request)   PB2 IGN_N (low = IG1)
//   PA7 HOLD_CTRL  PA6 KNOB_CTRL  PA5 SHDN_CTRL  PA3 EN5V_CTRL      (outputs)
//   PA4 PWROFF_IN (high = Pi halted)   PA1 VBAT_SENSE  PA2 BANK_SENSE  PA0 AREF 2.500 V

#include <avr/eeprom.h>
#include <avr/interrupt.h>
#include <avr/io.h>
#include <avr/sleep.h>
#include <avr/wdt.h>
#include <util/atomic.h>

#include "supervisor_logic.h"

// ---- time: Timer0 CTC, 1 ms at 1 MHz / 8 / 125 -------------------------------------------
static volatile uint32_t g_ms = 0;
ISR(TIM0_COMPA_vect) { g_ms++; }
static uint32_t millis() {
    uint32_t t;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { t = g_ms; }
    return t;
}

// ---- state kept across WDT/BOD resets (§10.5) ----------------------------------------------
struct Saved {
    uint8_t state;
    uint8_t check;  // ~state
};
static Saved saved __attribute__((section(".noinit")));

// ---- outputs ---------------------------------------------------------------------------------
static void write_outputs(const sup::Outputs& o) {
    uint8_t a = PORTA & ~(_BV(PA7) | _BV(PA6) | _BV(PA5) | _BV(PA3));
    if (o.hold) a |= _BV(PA7);
    if (o.knob) a |= _BV(PA6);
    if (o.shdn) a |= _BV(PA5);
    if (o.en5v) a |= _BV(PA3);
    PORTA = a;
}

// ---- ADC: external 2.500 V reference on AREF (never the internal one, §6.1) -------------------
static uint16_t adc_read(uint8_t channel) {
    ADMUX = _BV(REFS0) | channel;  // REFS1:0 = 01: external AREF
    ADCSRA |= _BV(ADSC);
    while (ADCSRA & _BV(ADSC)) {
    }
    return ADC;
}

// VBAT: k = 66.5 k / 1066.5 k = 0.062354 -> mV = adc * 2500 / 1024 / 0.062354 = adc * 39.155
static uint16_t vbat_mv() { return static_cast<uint16_t>((static_cast<uint32_t>(adc_read(1)) * 40095UL) >> 10); }
// BANK_SENSE = BANK_P / 2 -> mV = adc * 2500 / 1024 * 2
static uint16_t bank_mv() { return static_cast<uint16_t>((static_cast<uint32_t>(adc_read(2)) * 5000UL) >> 10); }

static sup::Inputs read_inputs() {
    sup::Inputs in;
    const uint8_t b = PINB, a = PINA;
    in.acc = !(b & _BV(PB0));
    in.req = b & _BV(PB1);
    in.ign = !(b & _BV(PB2));
    in.pwroff = a & _BV(PA4);
    in.vbat_mv = vbat_mv();
    in.bank_mv = bank_mv();
    return in;
}

// ---- watchdog: 2 s reset while running, 8 s interrupt-only wake while asleep -------------------
static void wdt_run_mode() {
    wdt_reset();
    WDTCSR = _BV(WDCE) | _BV(WDE);
    WDTCSR = _BV(WDE) | _BV(WDP2) | _BV(WDP1) | _BV(WDP0);  // 2 s, system reset
}
static void wdt_sleep_mode() {
    wdt_reset();
    WDTCSR = _BV(WDCE) | _BV(WDE);
    WDTCSR = _BV(WDIE) | _BV(WDP3) | _BV(WDP0);  // 8 s, interrupt only
}
ISR(WATCHDOG_vect) {}
ISR(PCINT1_vect) {}  // IGN_N / ACC_N edge: wake-up only

static void sleep_now() {
    ADCSRA &= ~_BV(ADEN);  // removes the ~78 uA AREF load (review n11)
    wdt_sleep_mode();
    set_sleep_mode(SLEEP_MODE_PWR_DOWN);
    cli();
    sleep_enable();
#if defined(BODS)
    sleep_bod_disable();
#endif
    sei();
    sleep_cpu();
    sleep_disable();
    wdt_run_mode();
    ADCSRA |= _BV(ADEN);
}

// ---- hold-up health flags in EEPROM (§10.7), read via ISP --------------------------------------
static uint8_t EEMEM ee_faults;
static void record_fault(uint8_t bit) {
    const uint8_t f = eeprom_read_byte(&ee_faults);
    if (f & bit) eeprom_update_byte(&ee_faults, f & ~bit);  // erased EEPROM is 0xFF: a 0 bit = fault
}

int main() {
    const uint8_t reset_cause = MCUSR;
    MCUSR = 0;
    wdt_run_mode();

    // Outputs first: all OFF, unless a WDT/BOD reset left a valid state to restore (<= 5 ms).
    DDRA = _BV(PA7) | _BV(PA6) | _BV(PA5) | _BV(PA3);
    PORTA = 0;
    DDRB = 0;
    PORTB = 0;  // all pulls are on the board

    TCCR0A = _BV(WGM01);  // CTC
    TCCR0B = _BV(CS01);   // /8
    OCR0A = 124;          // 125 kHz / 125 = 1 kHz
    TIMSK0 = _BV(OCIE0A);
    ADCSRA = _BV(ADEN) | _BV(ADPS1) | _BV(ADPS0);  // 1 MHz / 8 = 125 kHz ADC clock
    DIDR0 = _BV(ADC0D) | _BV(ADC1D) | _BV(ADC2D);   // analog pins: no digital input buffers
    GIMSK = _BV(PCIE1);
    PCMSK1 = _BV(PCINT8) | _BV(PCINT10);  // PB0 ACC_N, PB2 IGN_N
    sei();

    sup::Supervisor sup;
    const bool warm = (reset_cause & (_BV(WDRF) | _BV(BORF))) && saved.check == static_cast<uint8_t>(~saved.state) &&
                      saved.state <= static_cast<uint8_t>(sup::State::PostWait);
    if (warm) {
        sup.restore(static_cast<sup::State>(saved.state), millis());
    } else {
        sup.power_on_init(millis(), read_inputs());
    }

    uint32_t health_checked = 0;
    for (;;) {
        wdt_reset();
        const uint32_t now = millis();
        const sup::Inputs in = read_inputs();
        const sup::Outputs out = sup.update(now, in);
        write_outputs(out);
        saved.state = static_cast<uint8_t>(sup.state());
        saved.check = static_cast<uint8_t>(~saved.state);

        // Hold-up health: bank not charged 15 min after the Pi got power, or overcharged.
        if (out.hold && sup::elapsed(now, health_checked) >= 900000UL) {
            health_checked = now;
            if (in.bank_mv < 3680) record_fault(0x01);
        }
        if (in.bank_mv > 4600) record_fault(0x02);
        if (!out.hold) health_checked = now;

        if (sup.can_sleep(out)) sleep_now();
    }
}
