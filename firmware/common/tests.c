/* Example on-device tests. Copy this file to write your own. */
#include "bp_test.h"

static uint32_t crc32(const uint8_t *p, uint32_t n)
{
    uint32_t crc = 0xFFFFFFFFu;
    while (n--) {
        crc ^= *p++;
        for (int k = 0; k < 8; k++)
            crc = (crc >> 1) ^ (0xEDB88320u & -(crc & 1u));
    }
    return ~crc;
}

static uint32_t isqrt(uint32_t x)
{
    uint32_t r = 0, bit = (uint32_t)1 << 30; /* not 1u: int is 16 bits on MSP430 */
    while (bit > x)
        bit >>= 2;
    while (bit) {
        if (x >= r + bit) {
            x -= r + bit;
            r = (r >> 1) + bit;
        } else {
            r >>= 1;
        }
        bit >>= 2;
    }
    return r;
}

BP_TEST(crc32_check_value)
{
    static const uint8_t msg[] = "123456789";
    BP_ASSERT_EQ(crc32(msg, 9), 0xCBF43926u); /* the standard CRC-32 check value */
}

BP_TEST(sum_to_1000)
{
    volatile uint32_t n = 1000; /* volatile so the compiler cannot fold the loop away */
    uint32_t sum = 0;
    for (uint32_t i = 0; i < n; i++)
        sum += i;
    BP_ASSERT_EQ(sum, 499500u);
}

BP_TEST(integer_sqrt)
{
    volatile uint32_t x = 1000000;
    BP_ASSERT_EQ(isqrt(x), 1000u);
}

BP_TEST(float_multiply)
{
    volatile float a = 1.5f, b = 2.0f;
    BP_ASSERT(a * b == 3.0f);
}

#ifdef BP_DEMO_FAIL
BP_TEST(deliberate_failure)
{
    volatile int x = 2;
    BP_ASSERT_EQ(x + x, 5); /* fails on purpose, to show what a failure looks like */
}
#endif
