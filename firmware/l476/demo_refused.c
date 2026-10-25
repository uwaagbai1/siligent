/* Demo of a refusal: this firmware carries the option-byte unlock keys, which
 * firmware would need to change read protection and lock the chip. Siligent
 * refuses to flash it. The keys are never used here, so even if it were
 * flashed it would do nothing harmful. */
#include <stdint.h>

__attribute__((used)) const uint32_t demo_option_keys[2] = {0x08192A3Bu, 0x4C5D6E7Fu};
