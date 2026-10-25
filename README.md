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

## Status: early alpha, working on a real NUCLEO-L476RG

One board so far (STM32L476RG over ST-LINK). Expect the interfaces to change.

- Detects the ST-LINK by USB ID and the chip by its device ID, then loads that board's rules.
- Flashes through a safety gate; refusals say which rule fired and why.
- Runs on-device tests and reports pass/fail and CPU cycles per test.
- Backs up flash before every write; `siligent restore` puts a backup back.
- Logs every action, allowed or refused.
- MCP server so agents (Claude Code, Cursor, others) can use all of the above.

Not done yet: energy numbers on real hardware. Two meter drivers are written
and unit-tested but have not run on a real meter: the INA219 (active current
only) and the Nordic PPK2 (down to sub-microamp, see `plugins/siligent-ppk2`,
a separate GPL-2.0 package because its library is GPL).

Meters are plugins: a package registers a factory under the
`siligent.meters` entry-point group. When a meter powers the board, the core
checks the voltage against the board's supply range before any plugin runs.

## Virtual board (Renode)

Add `--virtual` to any command to use a simulated L476 instead of real
hardware (agent tools take `virtual: true`). The same safety rules apply.

```sh
.venv/bin/siligent --virtual test firmware/l476/build/tests.elf
```

- Needs [Renode](https://github.com/renode/renode/releases) (tested with
  1.17.0). It is found on PATH, in `~/.local/opt/renode-*/`, or via `RENODE=`.
- Renode has no STM32L4 model, so `src/siligent/boards/renode/stm32l476rg.repl`
  describes the core, cycle counter, flash, SRAM and ID registers. Peripherals
  are not modelled yet.
- Cycle counts are simulated: within about 10-30% of the real chip for the
  example tests (the real chip is slower, mostly flash wait states). No energy.

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
without hardware), `read`, `write`, `restore`.

Agent access (MCP, stdio): run `siligent-mcp`. Tools: `detect_hardware`,
`board_rules`, `check_image`, `flash`, `run_tests`, `read_memory`,
`write_memory`, `action_log`.

## What the safety gate enforces (NUCLEO-L476RG)

Rules live in `src/siligent/boards/stm32l476rg.toml`.

- Flash only after a person confirms the bench is safe to flash (`flash_allowed = true` in `bench.toml`).
- Flash only if the chip matches the rules (device ID 0x415) and read protection is level 0.
- Firmware must sit inside flash, start with a valid vector table, and must not
  contain the option-byte unlock keys (heuristic: catches firmware that could lock the chip).
- Host writes only to RAM, peripherals and core debug registers; never to the
  flash controller, option bytes or OTP.
- SWD pins (PA13, PA14) cannot be switched out of debug mode by a host write.
- The core is halted (with a reset) before erasing: erasing under running
  firmware locks the core up.

## Writing on-device tests

See `firmware/l476/tests.c`:

```c
#include "bp_test.h"
BP_TEST(my_test) { BP_ASSERT_EQ(add(2, 2), 4); }
```

Results go to a RAM mailbox (`bp_results`) that the host reads after the run.

## Tests

```sh
.venv/bin/pytest -q
```

The virtual-board tests skip if Renode or the built firmware is missing. Set
`SILIGENT_REQUIRE_RENODE=1` to make them fail instead, as CI does. CI
(`.github/workflows/ci.yml`) runs the unit tests on Python 3.11-3.13 and runs
the example firmware on the virtual board.

## Licence

Apache-2.0 (see `LICENSE`). The PPK2 plugin in `plugins/siligent-ppk2` is a
separate package under GPL-2.0, because the library it uses is GPL-2.0.
