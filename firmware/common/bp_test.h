/* Siligent on-device test harness.
 *
 *   BP_TEST(my_test) { BP_ASSERT(x == 3); }
 *
 * Each test is timed in CPU cycles (see bp_port.h for how each chip counts),
 * and results land in a RAM mailbox (bp_results) that the host reads through
 * the debug probe.
 */
#pragma once
#include <stdint.h>

typedef struct {
    const char *name;
    void (*fn)(void);
} bp_case_t;

/* Keep each test's record even though nothing refers to it by name.
 * TI's linker also needs "retain"; GCC's linker script KEEPs the section. */
#if defined(__TI_COMPILER_VERSION__)
#define BP_KEEP __attribute__((used, retain))
#else
#define BP_KEEP __attribute__((used))
#endif

#define BP_TEST(fn_name)                                                                \
    static void fn_name(void);                                                          \
    static const bp_case_t bp_case_##fn_name BP_KEEP __attribute__((section(".bp_cases"))) = { \
        #fn_name, fn_name};                                                             \
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
