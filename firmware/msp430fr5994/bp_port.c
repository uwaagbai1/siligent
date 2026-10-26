/* MSP430FR5994: no cycle counter, so Timer_A0 counts SMCLK, which runs at the
 * CPU clock (1 MHz after reset). Its 16 bits are extended to 32 by counting
 * overflows. */
#include <msp430.h>
#include "bp_port.h"

static volatile uint16_t overflows;

#pragma vector = TIMER0_A1_VECTOR
__interrupt void bp_timer_overflow(void)
{
    if (TA0IV == TAIV__TAIFG) /* reading TA0IV clears the flag */
        overflows++;
}

void bp_cycles_init(void)
{
    TA0CTL = TASSEL__SMCLK | MC__CONTINUOUS | TACLR | TAIE;
    __enable_interrupt();
}

uint32_t bp_cycles(void)
{
    uint16_t hi, lo;
    do { /* re-read if an overflow lands between the two halves */
        hi = overflows;
        lo = TA0R;
    } while (hi != overflows);
    return ((uint32_t)hi << 16) | lo;
}
