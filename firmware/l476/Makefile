# Build with: nice -n 19 make -j3
CC = arm-none-eabi-gcc
BUILD = build
CFLAGS = -mcpu=cortex-m4 -mthumb -mfloat-abi=hard -mfpu=fpv4-sp-d16 \
         -O2 -g -Wall -Wextra -ffreestanding -fno-tree-loop-distribute-patterns \
         -ffunction-sections -fdata-sections
LDFLAGS = -nostdlib -T linker.ld -Wl,--gc-sections -lgcc

COMMON = startup.c bp_test.c main.c

all: $(BUILD)/tests.elf $(BUILD)/tests_fail.elf $(BUILD)/demo_refused.elf

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/tests.elf: $(COMMON) tests.c bp_test.h linker.ld | $(BUILD)
	$(CC) $(CFLAGS) $(COMMON) tests.c -o $@ $(LDFLAGS)

# Same tests plus one that fails on purpose.
$(BUILD)/tests_fail.elf: $(COMMON) tests.c bp_test.h linker.ld | $(BUILD)
	$(CC) $(CFLAGS) -DBP_DEMO_FAIL $(COMMON) tests.c -o $@ $(LDFLAGS)

# Carries option-byte keys: Siligent must refuse to flash it.
$(BUILD)/demo_refused.elf: $(COMMON) tests.c demo_refused.c bp_test.h linker.ld | $(BUILD)
	$(CC) $(CFLAGS) $(COMMON) tests.c demo_refused.c -o $@ $(LDFLAGS) -Wl,--undefined=demo_option_keys

clean:
	rm -rf $(BUILD)

.PHONY: all clean
