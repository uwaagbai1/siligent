"""A virtual board in Renode, driven through Renode's monitor port.

It implements the same interface as a real probe, so the safety gate, the
test runner, the CLI and the MCP tools work on it unchanged.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from importlib import resources
from pathlib import Path

from ..errors import HardwareError
from ..image import Segment

PROMPT = re.compile(r"\((?:monitor|machine-\d+)\) $")
TELNET_IAC = re.compile(rb"\xff[\xfb-\xfe].|\xff[\xf0-\xfa]")


def find_renode(explicit: str | None = None) -> str:
    candidates = [explicit, os.environ.get("RENODE"), shutil.which("renode")]
    candidates += sorted(glob.glob(os.path.expanduser("~/.local/opt/renode-*/renode")), reverse=True)
    for c in candidates:
        if c and Path(c).is_file():
            return c
    raise HardwareError(
        "Renode not found. Install it (https://github.com/renode/renode/releases) "
        "or set RENODE=/path/to/renode."
    )


def platform_file(board: str) -> Path:
    res = resources.files("siligent.boards") / "renode" / f"{board}.repl"
    if not res.is_file():
        raise HardwareError(f"no Renode platform for '{board}' yet")
    return Path(str(res))


def core_file(board: str) -> Path | None:
    name = CORES.get(board)
    return Path(str(resources.files("siligent.boards") / "renode" / name)) if name else None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# Instructions per microsecond, set to the chip's clock after reset, so cycle
# counts land near the real chip's (Renode's default is 100 MIPS).
MIPS = {"stm32l476rg": 4, "msp430fr5994": 1}

# Seconds of virtual time between syncs of the CPU and the peripherals. Renode's
# default (100 us) would make the MSP430 timer move in steps of 100 cycles,
# because its timer never asks the core for the exact time; 1 us is one cycle.
QUANTUM = {"msp430fr5994": "0.000001"}

# What a result from each virtual board cannot tell you, shown with every run.
LIMITS = {
    "stm32l476rg": "cycle counts land within about 10-30% of the real chip (lower: no flash wait states)",
    "msp430fr5994": (
        "cycle counts use TI's published cost for each instruction and land within about 2% of "
        "the real chip for the example tests, but a test longer than 65,535 cycles wraps the "
        "timer and reports a wrong count without warning"
    ),
}

# C# code Renode compiles before loading a board's platform file. The MSP430
# core charges TI's cycle cost per instruction and fixes the multi-bit rotates
# (see the comments in the file).
CORES = {"msp430fr5994": "SiligentMSP430X.cs"}


class RenodeBackend:
    persistent = False  # nothing to back up: the virtual board starts fresh each time

    def __init__(
        self,
        board: str = "stm32l476rg",
        renode: str | None = None,
        mips: int | None = None,
        erased: tuple[int, int] | None = None,
        reset_vector_address: int | None = None,
        arch: str = "cortex-m",
    ):
        """erased: (start, size) of program memory, filled with 0xFF on start as on a
        blank chip. reset_vector_address: where the core reads its start address after
        reset, for cores Renode does not start from their vector (MSP430)."""
        self.board = board
        self.mips = mips or MIPS.get(board, 4)
        self.erased = erased
        self.reset_vector_address = reset_vector_address
        self.arch = arch
        self.renode = find_renode(renode)
        self.unique_id = f"renode-virtual-{board}"
        self.description = f"Renode virtual {board}"
        self.limits = LIMITS.get(board)
        self._proc: subprocess.Popen | None = None
        self._sock: socket.socket | None = None
        self._running = False
        self._tmp = tempfile.TemporaryDirectory(prefix="siligent-renode-")

    # Monitor connection ---------------------------------------------------------

    def open(self) -> None:
        port = _free_port()
        self._proc = subprocess.Popen(
            [self.renode, "--disable-gui", "--plain", "--hide-log", "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 60
        while True:
            try:
                self._sock = socket.create_connection(("127.0.0.1", port), timeout=1)
                break
            except OSError:
                if self._proc.poll() is not None or time.monotonic() > deadline:
                    self.close()
                    raise HardwareError("Renode did not start")
                time.sleep(0.2)
        self._read_until_prompt(timeout=60)
        core = core_file(self.board)
        if core is not None:
            self.command(f"include @{core}", timeout=120)
        self.command("mach create")
        self.command(f"machine LoadPlatformDescription @{platform_file(self.board)}", timeout=60)
        self.command(f"cpu PerformanceInMips {self.mips}")
        if self.board in QUANTUM:
            self.command(f'emulation SetGlobalQuantum "{QUANTUM[self.board]}"')
        if self.erased:
            start, size = self.erased
            blank = Path(self._tmp.name) / "erased.bin"
            blank.write_bytes(b"\xff" * size)
            self.command(f"sysbus LoadBinary @{blank} {start:#x}", timeout=60)

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.sendall(b"quit\n")
            except OSError:
                pass
            self._sock.close()
            self._sock = None
        if self._proc is not None:
            try:
                self._proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        self._tmp.cleanup()

    def _read_until_prompt(self, timeout: float = 30) -> str:
        assert self._sock is not None
        buf = b""
        deadline = time.monotonic() + timeout
        self._sock.settimeout(0.5)
        while time.monotonic() < deadline:
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                raise HardwareError("Renode closed the monitor connection")
            buf += chunk
            text = TELNET_IAC.sub(b"", buf).decode(errors="replace")
            if PROMPT.search(text):
                return text
        raise HardwareError("Renode did not answer in time")

    def command(self, line: str, timeout: float = 30) -> str:
        """Run one monitor command and return its output (without the echo and prompt)."""
        if self._sock is None:
            raise HardwareError("virtual board is not open")
        self._sock.sendall(line.encode() + b"\n")
        text = self._read_until_prompt(timeout)
        body = PROMPT.sub("", text).replace("\r", "")
        lines = [ln for ln in body.split("\n") if ln.strip() and ln.strip() != line]
        out = "\n".join(lines).strip()
        if out.startswith("There was an error") or "Could not find" in out:
            raise HardwareError(f"Renode: {out}")
        return out

    # Probe interface --------------------------------------------------------------

    def read32(self, address: int) -> int:
        return int(self.command(f"sysbus ReadDoubleWord {address:#x}"), 16)

    def read_memory(self, address: int, length: int) -> bytes:
        out = self.command(f"sysbus ReadBytes {address:#x} {length}")
        values = re.findall(r"0x([0-9A-Fa-f]{1,2})", out)
        if len(values) != length:
            raise HardwareError(f"Renode returned {len(values)} bytes, expected {length}")
        return bytes(int(v, 16) for v in values)

    def write_memory(self, address: int, data: bytes) -> None:
        i = 0
        while i < len(data):
            a = address + i
            if a % 4 == 0 and i + 4 <= len(data):
                word = int.from_bytes(data[i : i + 4], "little")
                self.command(f"sysbus WriteDoubleWord {a:#x} {word:#x}")
                i += 4
            else:
                self.command(f"sysbus WriteByte {a:#x} {data[i]:#x}")
                i += 1

    def program(self, segments: list[Segment]) -> None:
        self.halt()
        for n, seg in enumerate(segments):
            path = Path(self._tmp.name) / f"segment{n}.bin"
            path.write_bytes(seg.data)
            self.command(f"sysbus LoadBinary @{path} {seg.address:#x}")
        self.reset(halt=True)

    def reset(self, halt: bool = False) -> None:
        # The platform mirrors flash at address 0, so the core boots from the
        # vector table there, as the real chip does.
        self.halt()
        self.command("machine Reset")
        if self.reset_vector_address is not None:
            # Renode's MSP430 core starts at address 0; the real chip loads its PC
            # from the reset vector.
            vector = int.from_bytes(self.read_memory(self.reset_vector_address, 2), "little")
            self.command(f"cpu PC {vector:#x}")
        if not halt:
            self.resume()

    def halt(self) -> None:
        if self._running:
            self.command("pause")
            self._running = False

    def resume(self) -> None:
        if not self._running:
            self.command("start")
            self._running = True

    def state(self) -> str:
        return "running" if self._running else "halted"
