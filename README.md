# Siligent

An open-source framework that lets AI coding agents flash, test and measure
real microcontroller boards, behind safety rules, so a firmware change is
proven on the chip, not just compiled.

## Try it in a minute (no hardware)

```sh
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
nice -n 19 make -j3 -C firmware/l476     # needs arm-none-eabi-gcc
.venv/bin/python scripts/demo.py         # needs Renode, see "Virtual board"
```

## Status: early alpha, working on two real boards

| Board | Chip | Probe | Energy |
|---|---|---|---|
| NUCLEO-L476RG | STM32L476RG (Cortex-M4) | ST-LINK, via pyOCD | needs an external meter |
| MSP-EXP430FR5994 | MSP430FR5994 (16-bit, FRAM) | on-board eZ-FET, via TI's libmsp430 | built in: TI EnergyTrace |

Expect the interfaces to change.

- Detects probes by USB ID and chips by their device ID, then loads that board's rules.
- Flashes through a safety gate; refusals say which rule fired and why.
- Runs on-device tests and reports pass/fail and CPU cycles per test, plus
  measured energy on the MSP430 LaunchPad.
- Backs up program memory before every write; `siligent restore` puts a backup back.
- Logs every action, allowed or refused.
- MCP server so agents (Claude Code, Cursor, others) can use all of the above.
- With several boards connected, pick one with `--probe <serial>` (agent tools take `probe`).
  A pinned probe that is missing is an error, never a reason to use another board.

Two external meter drivers are written and unit-tested but have not run on a
real meter yet: the INA219 (active current only) and the Nordic PPK2 (down to
sub-microamp, see `plugins/siligent-ppk2`, a separate GPL-2.0 package because
its library is GPL).

Meters are plugins: a package registers a factory under the
`siligent.meters` entry-point group. When a meter powers the board, the core
checks the voltage against the board's supply range before any plugin runs.

## Virtual board (Renode)

Add `--virtual` to any command to use a simulated board instead of real
hardware (agent tools take `virtual: true`). The same safety rules apply.
Both boards have a virtual version; Siligent picks the one the firmware was
built for (or the `board` in `bench.toml`).

```sh
.venv/bin/siligent --virtual test firmware/l476/build/tests.elf
.venv/bin/siligent --virtual test firmware/msp430fr5994/prebuilt/tests.elf   # no TI compiler needed
```

- Needs [Renode](https://github.com/renode/renode/releases) (tested with
  1.17.0). It is found on PATH, in `~/.local/opt/renode-*/`, or via `RENODE=`.
- Renode has no model of either chip, so `src/siligent/boards/renode/` describes
  just enough of each to run test firmware: core, memory, device ID, and the
  cycle counter (DWT on the L476, Timer_A0/A1 on the MSP430). Other peripherals
  are not modelled; their limits are listed at the top of each `.repl` file.
- Renode 1.17's MSP430X core gets the multi-bit rotate instructions wrong, which
  breaks TI's software floating point. Siligent patches each one in the loaded
  firmware with a CPU hook (`renode_backend.py`, tested in
  `tests/test_msp430x_rotate_fix.py`).
- Cycle counts are simulated, not timed on the chip, and every virtual result
  says so in its `note`. No energy.
  - L476: within about 10-30% of the real chip for the example tests (the real
    chip is slower, mostly flash wait states).
  - MSP430: about 40% below the real chip (10.5k cycles against 17.3k for the
    example tests), because every instruction counts as one cycle while real
    ones take 1-6. Counts also shift a little from run to run, and a test
    longer than 65,535 cycles reports a wrong count: Renode's timer never
    signals the overflow the cycle counter relies on.

## Quick start

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pyocd pack install stm32l476rg
nice -n 19 make -j3 -C firmware/l476
cp bench.example.toml bench.toml   # then set flash_allowed = true when the bench is safe to flash

.venv/bin/siligent detect
.venv/bin/siligent test firmware/l476/build/tests.elf
.venv/bin/siligent flash firmware/l476/build/demo_refused.elf   # refused on purpose
.venv/bin/siligent log
```

Every command prints JSON. Other commands: `rules`, `check` (image checks
without hardware; the board is worked out from the file), `read`, `write`, `restore`.

Agent access (MCP, stdio): run `siligent-mcp`. Tools: `detect_hardware`,
`board_rules`, `check_image`, `flash`, `run_tests`, `read_memory`,
`write_memory`, `action_log`.

## What the safety gate enforces (NUCLEO-L476RG)

Rules live in `src/siligent/boards/stm32l476rg.toml`.

- Flash only after a person confirms the bench is safe to flash: `flash_allowed`
  in `bench.toml`, either a list of probe serials or `true`. `true` is refused
  when several boards are connected, so allowing one board never allows another.
- Flash only if the chip matches the rules (device ID 0x415) and read protection is level 0.
- Firmware must sit inside flash, start with a valid vector table, and must not
  contain the option-byte unlock keys (heuristic: catches firmware that could lock the chip).
- Host writes only to RAM, peripherals and core debug registers; never to the
  flash controller, option bytes or OTP.
- SWD pins (PA13, PA14) cannot be switched out of debug mode by a host write.
- The core is halted (with a reset) before erasing: erasing under running
  firmware locks the core up.

## MSP430FR5994 LaunchPad

```sh
nice -n 19 make -j3 -C firmware/msp430fr5994    # TI's cl430, from Code Composer Studio
.venv/bin/siligent check firmware/msp430fr5994/build/demo_refused.elf   # refused: would lock the debug port
.venv/bin/siligent test firmware/msp430fr5994/build/tests.elf
```

- Needs TI's debug library, `libmsp430.so`, which comes with Code Composer
  Studio. It is found under `~/ti/ccs*/` or via `SILIGENT_LIBMSP430=`.
- Siligent never updates the debugger's firmware. If the library asks for an
  update, it stops and says so; update once with TI's tools.
- The eZ-FET supplies about 3.3 V whatever is asked for (3.31 V measured); the
  measured supply is checked against the chip's range too.
- `siligent restore` writes the whole of program memory back (FRAM is not
  erased first, so skipping bytes would leave newer code behind) and reads it
  back to confirm.
- The firmware builds with TI's `cl430` compiler from CCS (`TI_CGT=` to point
  elsewhere). CI does not build it yet.

What the gate enforces on this chip (`src/siligent/boards/msp430fr5994.toml`):

- 0xFF80-0xFF8F hold the JTAG, BSL and IP-encapsulation signatures. Writing
  0x5555 to the JTAG signature locks the debug port for good. Firmware may only
  contain 0xFF there, and those bytes must read 0xFF on the chip before any flash.
- Firmware containing `mov #0x5555, &0xFF80` (or 0xAAAA) is refused: code that
  would lock the chip at run time.
- Host writes only to RAM, LEA RAM and peripherals; never to FRAM, the
  bootloader, the device descriptor (calibration data), the FRAM controller or
  the memory protection unit.
- Firmware built for another architecture is refused (`image.wrong_architecture`).

### Energy with TI EnergyTrace

On this LaunchPad the debugger measures energy itself, so `siligent test`
reports it with no extra hardware (`[meter] type = "auto"`, the default). An
example run of `firmware/msp430fr5994/build/tests.elf` at 1 MHz:

| | Measured |
|---|---|
| Energy while the tests ran | 14.2-15.1 uJ (14 runs) |
| Time | 21-22 ms (the tests' own CPU time: 17.3 ms) |
| Average current | about 200 uA at 3.29 V |
| Sleep current afterwards (LPM4) | below EnergyTrace's resolution |

What this is and is not:

- It is the whole target side of the board, not the chip alone.
- The chip runs with the debugger released: attached, the debugger keeps the
  clocks running and low-power modes never show.
- The harness wakes EnergyTrace with 0.2 s of busy work, waits 0.3 s in LPM0,
  runs the tests, then sleeps in LPM4. The tests are found as the step up
  between the wait and the sleep, using the debugger's energy counter. If the
  step's length does not match the tests' CPU time, no energy is claimed.
- Per-test figures (`energy_uj_estimate`) split the measured total by CPU time.
  They are estimates; measuring each test separately needs markers.

## Writing on-device tests

The harness is in `firmware/common/` and is shared by both boards; see
`firmware/common/tests.c`:

```c
#include "bp_test.h"
BP_TEST(my_test) { BP_ASSERT_EQ(add(2, 2), 4); }
```

Results go to a RAM mailbox (`bp_results`) that the host reads after the run.
Each chip supplies a cycle counter in `bp_port.c`: the DWT counter on the
STM32, Timer_A on the MSP430 (which has none).

## Tests

```sh
.venv/bin/pytest -q
```

The virtual-board tests skip if Renode or the built firmware is missing. Set
`SILIGENT_REQUIRE_RENODE=1` to make them fail instead, as CI does. CI
(`.github/workflows/ci.yml`) runs the unit tests on Python 3.11-3.13 and runs
the example firmware on both virtual boards. CI builds the L476 firmware; for
the MSP430 it runs the copies in `firmware/msp430fr5994/prebuilt/`, built with
TI's compiler from Code Composer Studio. After changing the firmware, rebuild
them with `make -C firmware/msp430fr5994 prebuilt` and commit them.

## Licence

Apache-2.0 (see `LICENSE`). The PPK2 plugin in `plugins/siligent-ppk2` is a
separate package under GPL-2.0, because the library it uses is GPL-2.0.
