/* Minimal startup for the STM32L476: vector table, RAM setup, FPU on, then main(). */
#include <stdint.h>

extern uint32_t _sidata, _sdata, _edata, _sbss, _ebss, _estack;
int main(void);
void bp_mark_crashed(void);

#define SCB_CPACR (*(volatile uint32_t *)0xE000ED88u)

void Reset_Handler(void)
{
    uint32_t *src = &_sidata;
    for (uint32_t *dst = &_sdata; dst < &_edata;)
        *dst++ = *src++;
    for (uint32_t *dst = &_sbss; dst < &_ebss;)
        *dst++ = 0;

    SCB_CPACR |= (0xFu << 20); /* full access to the FPU (CP10, CP11) */
    __asm volatile("dsb; isb");

    main();
    for (;;) {
    }
}

void Default_Handler(void)
{
    for (;;) {
    }
}

/* Any fault ends up here: record which test crashed, then stop. */
void HardFault_Handler(void)
{
    bp_mark_crashed();
    for (;;) {
    }
}

__attribute__((section(".isr_vector"), used)) const void *const vectors[16] = {
    &_estack,
    Reset_Handler,
    Default_Handler,   /* NMI */
    HardFault_Handler, /* HardFault */
    HardFault_Handler, /* MemManage */
    HardFault_Handler, /* BusFault */
    HardFault_Handler, /* UsageFault */
    0, 0, 0, 0,
    Default_Handler, /* SVCall */
    Default_Handler, /* DebugMon */
    0,
    Default_Handler, /* PendSV */
    Default_Handler, /* SysTick */
};
