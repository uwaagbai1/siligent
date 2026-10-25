/* Siligent on-device test harness.
 *
 *   BP_TEST(my_test) { BP_ASSERT(x == 3); }
 *
 * Each test is timed with the CPU cycle counter, and results land in a RAM
 * mailbox (bp_results) that the host reads through the debug probe.
 */
#pragma once
#include <stdint.h>

typedef struct {
    const char *name;
    void (*fn)(void);
} bp_case_t;

#define BP_TEST(fn_name)                                                                     \
    static void fn_name(void);                                                               \
    static const bp_case_t bp_case_##fn_name __attribute__((used, section(".bp_cases"))) = { \
        #fn_name, fn_name};                                                                  \
    static void fn_name(void)

void bp_fail(int line);

#define BP_ASSERT(cond)           \
    do {                          \
        if (!(cond)) {            \
            bp_fail(__LINE__);    \
            return;               \
        }                         \
    } while (0)

#define BP_ASSERT_EQ(a, b) BP_ASSERT((a) == (b))

void bp_run_all(uint32_t clock_hz);
void bp_mark_crashed(void);
