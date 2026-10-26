/* Siligent must refuse to flash this image. It contains code that writes 0x5555
 * to the JTAG signature, which would permanently lock the debug port. The
 * function is never called, so the image is harmless even if flashed by another
 * tool, but Siligent sees the instruction ("55 55 80 FF") and refuses it. */
#include <stdint.h>

#pragma RETAIN(demo_lock_jtag)
void demo_lock_jtag(void)
{
    *(volatile uint16_t *)0xFF80u = 0x5555u;
}
