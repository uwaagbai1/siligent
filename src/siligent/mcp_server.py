"""MCP server: exposes the bench to AI agents (Claude Code, Cursor, others) over stdio.

Refusals come back as normal results with "refused": true, the rule that fired
and the reason, so the agent can explain or change course instead of retrying.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from . import api
from .bench import BenchConfig

INSTRUCTIONS = """\
Siligent controls a real hardware bench. Every write and flash passes a safety gate.
If a result has "refused": true, do not retry the same action: read "rule" and "reason",
then explain it to the user or choose a different approach. Reads are always allowed.
Start with detect_hardware, then board_rules to see what is allowed on this board.
If several boards are connected, pass probe=<probe_uid from detect_hardware> to the hardware tools.
Use check_image before flash or run_tests to catch problems without touching hardware.
With virtual=true the tools use a simulated board in Renode instead: flash and run_tests
simulate the board the file was built for (STM32L476 or MSP430FR5994). Read the "note"
in virtual results: simulated cycle counts are not the real chip's.
"""

server = MCPServer("siligent", instructions=INSTRUCTIONS)


def _cfg(virtual: bool = False, probe: str | None = None, board: str | None = None) -> BenchConfig:
    cfg = BenchConfig.load()
    if virtual:
        cfg.virtual = True
        if board:  # real hardware identifies its own chip
            cfg.board = board
    if probe:
        cfg.probe_uid = probe
    return cfg


@server.tool()
def detect_hardware() -> dict:
    """List connected debug probes and identify each board and the safety rules that apply."""
    return api.detect(_cfg())


@server.tool()
def board_rules(board: str = "stm32l476rg") -> dict:
    """Show what is allowed and forbidden on a board: memory regions, protected pins, flash preconditions."""
    return api.rules(board)


@server.tool()
def check_image(path: str, board: str | None = None) -> dict:
    """Check a firmware file (.elf) against a board's rules without touching hardware.
    The board is worked out from the file when only one built-in board fits."""
    return api.check_image(path, board=board, config=_cfg())


@server.tool()
def flash(path: str, virtual: bool = False, probe: str | None = None) -> dict:
    """Flash a firmware file (.elf) to the board. The flash pages are backed up first.
    virtual=true uses a Renode virtual board (the one the file was built for) instead of real hardware."""
    return api.flash(path, config=_cfg(virtual, probe))


@server.tool()
def run_tests(path: str, timeout_s: float = 10.0, virtual: bool = False, probe: str | None = None) -> dict:
    """Flash test firmware built with the Siligent harness, run it, and return
    pass/fail and CPU cycles per test, plus energy when the bench can measure it
    (a configured power meter, or TI EnergyTrace on MSP430 LaunchPads).
    virtual=true runs it on a Renode virtual board of the chip the file was built for
    (no hardware needed; cycle counts are simulated, see "note", and there is no energy)."""
    return api.run_tests(path, timeout_s=timeout_s, config=_cfg(virtual, probe))


@server.tool()
def read_memory(
    address: int, length: int, virtual: bool = False, probe: str | None = None, board: str | None = None
) -> dict:
    """Read up to 4096 bytes of chip memory. Returns hex.
    board picks the virtual board (stm32l476rg or msp430fr5994) when virtual=true."""
    return api.read_memory(address, length, config=_cfg(virtual, probe, board))


@server.tool()
def write_memory(
    address: int, data_hex: str, virtual: bool = False, probe: str | None = None, board: str | None = None
) -> dict:
    """Write bytes to RAM or a peripheral register. Flash, lock bytes, protection registers
    and protected pins are refused.
    board picks the virtual board (stm32l476rg or msp430fr5994) when virtual=true."""
    return api.write_memory(address, data_hex, config=_cfg(virtual, probe, board))


@server.tool()
def action_log(n: int = 20) -> dict:
    """The most recent bench actions, including refused ones."""
    return api.action_log(n, config=_cfg())


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
