/* STM32L476 (Cortex-M4): the DWT cycle counter counts every CPU cycle. */
#include "bp_port.h"

#define DEMCR (*(volatile uint32_t *)0xE000EDFCu)
#define DWT_CTRL (*(volatile uint32_t *)0xE0001000u)
#define DWT_CYCCNT (*(volatile uint32_t *)0xE0001004u)

void bp_cycles_init(void)
{
    DEMCR |= (1u << 24); /* TRCENA: turn on the DWT unit */
    DWT_CYCCNT = 0;
    DWT_CTRL |= 1u; /* start the cycle counter */
}

uint32_t bp_cycles(void)
{
    return DWT_CYCCNT;
}
