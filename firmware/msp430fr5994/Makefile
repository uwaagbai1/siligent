# MSP430FR5994 test firmware, built with TI's MSP430 compiler (cl430), which
# comes with Code Composer Studio. Build with: nice -n 19 make -j3
#
# Paths default to the newest CCS under ~/ti; override TI_CGT and TI_MSP_INC.
TI_CGT ?= $(lastword $(sort $(wildcard $(HOME)/ti/ccs*/ccs/tools/compiler/ti-cgt-msp430_*)))
TI_MSP_INC ?= $(lastword $(sort $(wildcard $(HOME)/ti/ccs*/ccs/ccs_base/msp430/include)))
CC = $(TI_CGT)/bin/cl430
BUILD = build

CFLAGS = -vmspx --code_model=large --data_model=restricted --use_hw_mpy=F5 \
         --define=__MSP430FR5994__ -O2 -g --c99 --diag_warning=225 --display_error_number \
         --silicon_errata=CPU21 --silicon_errata=CPU22 --silicon_errata=CPU40 \
         -I../common -I$(TI_MSP_INC) -I$(TI_CGT)/include
LDFLAGS = -z --rom_model --heap_size=0 --stack_size=256 --reread_libs \
          -i$(TI_MSP_INC) -i$(TI_CGT)/lib -i$(TI_CGT)/include \
          -llnk_msp430fr5994.cmd bp_sections.cmd -llibc.a

SRC = main.c bp_port.c ../common/bp_test.c ../common/tests.c
DEPS = $(SRC) ../common/bp_test.h ../common/bp_port.h bp_sections.cmd

all: $(BUILD)/tests.elf $(BUILD)/tests_fail.elf $(BUILD)/demo_refused.elf

check-tools:
	@test -x "$(CC)" || { echo "cl430 not found: install Code Composer Studio or set TI_CGT"; exit 1; }

$(BUILD): check-tools
	mkdir -p $(BUILD)

$(BUILD)/tests.elf: $(DEPS) | $(BUILD)
	mkdir -p $(BUILD)/ok
	$(CC) $(CFLAGS) --obj_directory=$(BUILD)/ok $(SRC) $(LDFLAGS) -m $(BUILD)/tests.map -o $@

# Same tests plus one that fails on purpose.
$(BUILD)/tests_fail.elf: $(DEPS) | $(BUILD)
	mkdir -p $(BUILD)/fail
	$(CC) $(CFLAGS) -DBP_DEMO_FAIL --obj_directory=$(BUILD)/fail $(SRC) $(LDFLAGS) -m $(BUILD)/tests_fail.map -o $@

# Carries code that would lock the debug port: Siligent must refuse to flash it.
$(BUILD)/demo_refused.elf: $(DEPS) demo_refused.c | $(BUILD)
	mkdir -p $(BUILD)/refused
	$(CC) $(CFLAGS) --obj_directory=$(BUILD)/refused $(SRC) demo_refused.c $(LDFLAGS) -m $(BUILD)/demo_refused.map -o $@

clean:
	rm -rf $(BUILD)

.PHONY: all clean check-tools
