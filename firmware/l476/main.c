#include "bp_test.h"

/* The L476 starts on its 4 MHz MSI clock; the tests run at that speed. */
#define CLOCK_HZ 4000000u

int main(void)
{
    bp_run_all(CLOCK_HZ);
    /* Stay awake: on this board, debug reads of RAM return zeros while the core
     * sleeps, so the host could not read the results. */
    for (;;) {
    }
}
