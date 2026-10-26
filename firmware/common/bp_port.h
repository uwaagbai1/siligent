/* What each chip provides to the harness: a free-running CPU cycle count. */
#pragma once
#include <stdint.h>

void bp_cycles_init(void);
uint32_t bp_cycles(void);
