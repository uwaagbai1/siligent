/* STM32L476RG: 1 MB flash, 96 KB SRAM1. */
ENTRY(Reset_Handler)

MEMORY
{
  FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = 1024K
  RAM   (rwx) : ORIGIN = 0x20000000, LENGTH = 96K
}

_estack = ORIGIN(RAM) + LENGTH(RAM);

SECTIONS
{
  .isr_vector : { KEEP(*(.isr_vector)) } > FLASH

  .text : { *(.text*) *(.rodata*) . = ALIGN(4); } > FLASH

  /* Test cases registered with BP_TEST(). */
  .bp_cases : { . = ALIGN(4); __bp_cases_start = .; KEEP(*(.bp_cases)) __bp_cases_end = .; } > FLASH

  .ARM.exidx : { *(.ARM.exidx*) } > FLASH

  /* Results mailbox the host reads through the debug probe. Not loaded, not zeroed by startup. */
  .bp_results (NOLOAD) : { . = ALIGN(4); KEEP(*(.bp_results)) } > RAM

  .data : { . = ALIGN(4); _sdata = .; *(.data*) . = ALIGN(4); _edata = .; } > RAM AT > FLASH
  _sidata = LOADADDR(.data);

  .bss : { . = ALIGN(4); _sbss = .; *(.bss*) *(COMMON) . = ALIGN(4); _ebss = .; } > RAM
}
