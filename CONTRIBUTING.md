# Contributing

Thanks for looking. Siligent is early, so issues describing your bench
(board, probe, meter) and what you would want an agent to do on it are as
useful as code.

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
nice -n 19 make -j3 -C firmware/l476      # needs arm-none-eabi-gcc
.venv/bin/pytest -q
```

Virtual-board tests need [Renode](https://github.com/renode/renode/releases)
1.17.0; they skip without it.

The MSP430 firmware builds with TI's `cl430` from Code Composer Studio. If you
change anything in `firmware/common/` or `firmware/msp430fr5994/`, run
`nice -n 19 make -C firmware/msp430fr5994 prebuilt` and commit the copies in
`prebuilt/`: CI tests those on the virtual board, since it has no TI compiler.
`tests/test_prebuilt_firmware.py` fails if the sources changed without a rebuild.

## Ground rules

- Every write and flash goes through `GuardedProbe` and the safety gate.
  Nothing else should talk to a probe backend directly.
- A refusal names its rule and gives a reason a person can act on. New rules
  get a test that shows them firing.
- When in doubt, refuse. Unknown bench state counts as unsafe.
- Board facts live in `src/siligent/boards/<board>.toml`, not in code.

## Lessons from the real bench (STM32L476 over ST-LINK)

- Halt the core (reset and halt) before erasing flash. Erasing while firmware
  runs locked the core up and the erase timed out.
- While the core sleeps (WFI), ST-LINK reads of RAM, flash and peripherals can
  return zeros with no error. Halt before trusting a read. The test firmware
  busy-waits after finishing for the same reason.
- Read the read-protection option bytes with the core halted; a zero result
  means the read failed, not that protection is off.

## Lessons from the real bench (MSP430FR5994 LaunchPad, eZ-FET, EnergyTrace)

- `int` is 16 bits: `1u << 30` is undefined. Shared test code must use
  `(uint32_t)1 << 30` and friends.
- TI's linker drops unreferenced data even with `used`; it needs `retain`
  (`BP_KEEP` in `bp_test.h`).
- With the debugger attached, the chip's clocks keep running, so EnergyTrace
  shows a flat current and low-power modes are invisible. Run with the
  debugger released.
- Released, EnergyTrace reports nothing until about 100 ms after the chip first
  does real work, and goes quiet again after a long stretch near zero. Hence
  the harness's warm-up and LPM0 wait before the tests.
- Re-attaching the debugger draws several hundred uA for ~200 ms: stop the
  recording first.
- EnergyTrace's timeline starts about 60 ms after the chip starts running
  (a 200 ms warm-up shows as ~137 ms). Measure bursts from their own edges,
  never from time zero.
- The eZ-FET supplies about 3.3 V whatever MSP430_VCC asks for.
- A running timer's clock request keeps the DCO on in LPM4 (~75 uA). Stop it
  before sleeping.
- The current readings EnergyTrace reports are heavily smoothed; its
  cumulative energy counter is the reliable signal.
- mspdebug 0.22 misreads newer TI libraries and asks for a firmware update
  that is not needed. Siligent talks to libmsp430 directly.

## Lessons from the virtual MSP430 (Renode 1.17)

- The MSP430X core starts at address 0 after a reset instead of reading the
  reset vector, so the backend sets the PC from 0xFFFE itself.
- RRCM, RRAM, RLAM and RRUM keep bits above the operand width and lose the
  carry flag. TI's float library uses them, so 1.5 * 2.0 came out wrong.
- The core counts every instruction as one step of time, so cycle counts came
  out about 40% low.
- Both are fixed in `boards/renode/SiligentMSP430X.cs`, a subclass of the
  stock core that Renode compiles at start (`include` before the platform
  file). It does the rotates itself and charges each instruction its cycles
  from TI's tables (SLAU367P 4-8 to 4-19). It never reports more cycles than
  Renode's scheduler asked for; leftovers carry into the next call.
- An earlier fix used Python CPU hooks. Avoid them: an exception in one kills
  Renode, `machine Reset` drops them, and `cpu Step` skips them.
- Renode syncs peripherals with the CPU once per quantum (100 us by default),
  and the MSP430 timer never asks for the exact time, so counts came out in
  steps of 100 cycles. The backend sets a 1 us quantum for this board.
- Timer_A ignores the clock-source bits (every timer counts the platform's
  fixed clock) and raises its overflow flag only in up mode.

## Adding a board

1. Copy `src/siligent/boards/stm32l476rg.toml` and fill in the memory map,
   supply range, protection registers and protected pins from the datasheet
   and reference manual.
2. Add tests for the new rules in `tests/`.
3. Optional: a Renode platform in `src/siligent/boards/renode/`.

## Licence

Contributions to the core are under Apache-2.0. The PPK2 plugin is GPL-2.0.
