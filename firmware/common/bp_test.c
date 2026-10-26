#include "bp_test.h"
#include "bp_port.h"

/* Must match MAGIC, HEADER and ENTRY in src/siligent/runner.py. */
#define BP_MAGIC 0x42504C54u
#define BP_VERSION 1u
#define BP_MAX_TESTS 32u

enum { ST_IDLE = 0, ST_RUNNING = 1, ST_DONE = 2, ST_CRASHED = 3 };
enum { R_NOT_RUN = 0, R_PASS = 1, R_FAIL = 2, R_CRASHED = 3 };

typedef struct {
    char name[20];
    uint32_t status;
    uint32_t cycles;
    uint32_t fail_line;
} bp_entry_t;

typedef struct {
    uint32_t magic, version, state, count, passed, failed, clock_hz, current;
    bp_entry_t entries[BP_MAX_TESTS];
} bp_results_t;

volatile bp_results_t bp_results __attribute__((section(".bp_results"), used));

/* The host parses this layout byte for byte: 32-byte header, 32-byte entries. */
typedef char bp_layout_check[(sizeof(bp_results_t) == 32u + 32u * BP_MAX_TESTS) ? 1 : -1];

extern const bp_case_t __bp_cases_start[], __bp_cases_end[];

static volatile uint32_t fail_line;

void bp_fail(int line)
{
    if (!fail_line)
        fail_line = (uint32_t)line;
}

void bp_mark_crashed(void)
{
    uint32_t i = bp_results.current;
    if (i < BP_MAX_TESTS)
        bp_results.entries[i].status = R_CRASHED;
    bp_results.state = ST_CRASHED;
}

void bp_run_all(uint32_t clock_hz)
{
    bp_cycles_init();

    uint32_t n = (uint32_t)(__bp_cases_end - __bp_cases_start);
    if (n > BP_MAX_TESTS)
        n = BP_MAX_TESTS;

    bp_results.version = BP_VERSION;
    bp_results.count = n;
    bp_results.passed = 0;
    bp_results.failed = 0;
    bp_results.clock_hz = clock_hz;
    bp_results.current = 0;
    for (uint32_t i = 0; i < n; i++) {
        const char *s = __bp_cases_start[i].name;
        uint32_t k = 0;
        for (; k < sizeof bp_results.entries[i].name - 1 && s[k]; k++)
            bp_results.entries[i].name[k] = s[k];
        for (; k < sizeof bp_results.entries[i].name; k++)
            bp_results.entries[i].name[k] = 0;
        bp_results.entries[i].status = R_NOT_RUN;
        bp_results.entries[i].cycles = 0;
        bp_results.entries[i].fail_line = 0;
    }
    bp_results.state = ST_RUNNING;
    bp_results.magic = BP_MAGIC; /* written last: the header is now valid */

    /* Cost of the timing itself, subtracted from every test. */
    uint32_t t0 = bp_cycles();
    uint32_t overhead = bp_cycles() - t0;

    for (uint32_t i = 0; i < n; i++) {
        bp_results.current = i;
        fail_line = 0;
        uint32_t start = bp_cycles();
        __bp_cases_start[i].fn();
        uint32_t cycles = bp_cycles() - start;
        bp_results.entries[i].cycles = cycles > overhead ? cycles - overhead : 0;
        if (fail_line) {
            bp_results.entries[i].status = R_FAIL;
            bp_results.entries[i].fail_line = fail_line;
            bp_results.failed++;
        } else {
            bp_results.entries[i].status = R_PASS;
            bp_results.passed++;
        }
    }
    bp_results.state = ST_DONE;
}
