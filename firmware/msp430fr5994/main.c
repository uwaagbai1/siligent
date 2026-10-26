#include <msp430.h>
#include "bp_test.h"

/* After reset the FR5994 runs MCLK and SMCLK at 1 MHz (8 MHz DCO / 8). */
#define CLOCK_HZ 1000000ul

/* Floating input pins draw current, which would hide the chip's own sleep
 * current. Drive unused pins low, as TI's low-power examples do. P2.0/P2.1
 * are the UART to the debugger: P2.0 (TX) idles high, P2.1 (RX) is driven by
 * the debugger, so it stays an input. */
static void pins_low_power(void)
{
    P1OUT = 0; P1DIR = 0xFF;
    P2OUT = BIT0; P2DIR = (uint8_t)~BIT1;
    P3OUT = 0; P3DIR = 0xFF;
    P4OUT = 0; P4DIR = 0xFF;
    P5OUT = 0; P5DIR = 0xFF;
    P6OUT = 0; P6DIR = 0xFF;
    P7OUT = 0; P7DIR = 0xFF;
    P8OUT = 0; P8DIR = 0xFF;
    PJOUT = 0; PJDIR = 0xFFFF;
    PM5CTL0 &= ~LOCKLPM5; /* apply the pin settings */
}

/* Measured with the debugger released: EnergyTrace reports nothing until about
 * 100 ms after the chip first does real work, and drops what came before. So:
 * 0.2 s of busy work to wake it up. It also goes quiet again after a while
 * near zero current, so the wait before the tests is in LPM0 (CPU off, clocks
 * on, ~75 uA), not a deeper mode. Then the tests (CPU on), then LPM4. The tests
 * stand out as a step up from the LPM0 level. */
#pragma vector = TIMER1_A0_VECTOR
__interrupt void settle_done(void)
{
    TA1CTL = 0;
    __bic_SR_register_on_exit(LPM0_bits);
}

static void settle_before_tests(void)
{
    CSCTL0_H = CSKEY_H;
    CSCTL2 = (CSCTL2 & ~SELA_7) | SELA__VLOCLK; /* ACLK from the ~10 kHz VLO */
    CSCTL0_H = 0;
    TA1CCR0 = 3000; /* about 0.3 s at the VLO's typical 9.4 kHz (it varies by +-40%) */
    TA1CCTL0 = CCIE;
    TA1CTL = TASSEL__ACLK | MC__UP | TACLR;
    __bis_SR_register(LPM0_bits | GIE);
    __disable_interrupt();
}

int main(void)
{
    WDTCTL = WDTPW | WDTHOLD; /* the watchdog is on after reset and would cut the tests short */
    pins_low_power();
    __delay_cycles(200000); /* 0.2 s at 1 MHz: wakes EnergyTrace up */
    settle_before_tests();
    bp_run_all(CLOCK_HZ);
    TA0CTL = 0; /* stop the cycle timer, or its clock request keeps the DCO on in LPM4 */
    /* Sleep in LPM4 (CPU and clocks off). The drop in current marks the end of
     * the tests in an EnergyTrace recording, and what follows is the board's
     * sleep current. Not LPMx.5: that powers RAM down and drops the debugger. */
    __disable_interrupt();
    for (;;) {
        __bis_SR_register(LPM4_bits);
    }
}
