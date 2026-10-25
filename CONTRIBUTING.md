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

## Adding a board

1. Copy `src/siligent/boards/stm32l476rg.toml` and fill in the memory map,
   supply range, protection registers and protected pins from the datasheet
   and reference manual.
2. Add tests for the new rules in `tests/`.
3. Optional: a Renode platform in `src/siligent/boards/renode/`.

## Licence

Contributions to the core are under Apache-2.0. The PPK2 plugin is GPL-2.0.
