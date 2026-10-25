# Recording the demo

Two short clips. The first needs no hardware; the second uses the real board.

## Clip 1: the virtual board (about a minute)

```sh
nice -n 19 make -j3 -C firmware/l476
.venv/bin/python scripts/demo.py
```

It runs the tests on a simulated STM32L476, shows a failure pointing at the
source line, and shows two unsafe actions being refused, with the reason.

## Clip 2: an AI agent on the real board (one to two minutes)

Before recording:

1. Unplug any test equipment that must not be attached while flashing.
   Connect the NUCLEO-L476RG over USB only.
2. In `bench.toml`, set `flash_allowed = true`. Do this by hand; no
   script or agent should change it.
3. Run `.venv/bin/siligent detect`. It should list the ST-LINK and
   identify the chip as device 0x415.
4. Start Claude Code in the repo (`claude`) and check `/mcp` shows
   `siligent` as connected.

Prompts to type, in order:

1. "What hardware is connected, and what am I allowed to do on this board?"
2. "Run firmware/l476/build/tests.elf on the board and explain the results."
3. "Add an on-device test for a 16-bit checksum function, build it, and run it on the board."
4. "Flash firmware/l476/build/demo_refused.elf." The agent should get a
   refusal and explain why, without retrying.
5. "Show me the action log."

After recording, comment out `flash_allowed` in `bench.toml` again.

## What not to claim in the video

- Energy numbers: the power meter drivers have not run on a real meter yet.
- Virtual cycle counts are simulated, within about 10-30% of the real chip.
