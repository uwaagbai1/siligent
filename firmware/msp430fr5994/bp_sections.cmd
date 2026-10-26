/* Siligent harness sections, added to TI's lnk_msp430fr5994.cmd. */
SECTIONS
{
    /* Test cases registered with BP_TEST(). */
    .bp_cases : {} > FRAM, RUN_START(__bp_cases_start), RUN_END(__bp_cases_end)
    /* Results mailbox the host reads through the debugger. Not zeroed at start-up. */
    .bp_results : {} > RAM, type = NOINIT
}
