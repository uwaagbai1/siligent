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
Use check_image before flash or run_tests to catch problems without touching hardware.
"""

server = MCPServer("siligent", instructions=INSTRUCTIONS)


def _cfg(virtual: bool = False) -> BenchConfig:
    cfg = BenchConfig.load()
    if virtual:
        cfg.virtual = True
    return cfg


@server.tool()
def detect_hardware() -> dict:
    """List connected debug probes and identify the board and the safety rules that apply."""
    return api.detect(_cfg())


@server.tool()
def board_rules(board: str = "stm32l476rg") -> dict:
    """Show what is allowed and forbidden on a board: memory regions, protected pins, flash preconditions."""
    return api.rules(board)


@server.tool()
def check_image(path: str) -> dict:
    """Check a firmware file (.elf) against the board's rules without touching hardware."""
    return api.check_image(path, config=_cfg())


@server.tool()
def flash(path: str, virtual: bool = False) -> dict:
    """Flash a firmware file (.elf) to the board. The flash pages are backed up first.
    virtual=true uses a Renode virtual board instead of real hardware."""
    return api.flash(path, config=_cfg(virtual))


@server.tool()
def run_tests(path: str, timeout_s: float = 10.0, virtual: bool = False) -> dict:
    """Flash test firmware built with the Siligent harness, run it, and return
    pass/fail and CPU cycles per test, plus energy if a power meter is configured.
    virtual=true runs it on a Renode virtual board (no hardware needed; cycle counts
    are simulated, and there is no energy)."""
    return api.run_tests(path, timeout_s=timeout_s, config=_cfg(virtual))


@server.tool()
def read_memory(address: int, length: int, virtual: bool = False) -> dict:
    """Read up to 4096 bytes of chip memory. Returns hex."""
    return api.read_memory(address, length, config=_cfg(virtual))


@server.tool()
def write_memory(address: int, data_hex: str, virtual: bool = False) -> dict:
    """Write bytes to RAM or a peripheral register. Flash, option bytes and protected pins are refused."""
    return api.write_memory(address, data_hex, config=_cfg(virtual))


@server.tool()
def action_log(n: int = 20) -> dict:
    """The most recent bench actions, including refused ones."""
    return api.action_log(n, config=_cfg())


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
