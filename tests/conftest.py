from __future__ import annotations

import struct
from pathlib import Path

import pytest

from siligent.image import Image, Segment
from siligent.log import ActionLog
from siligent.probes.guarded import GuardedProbe
from siligent.rules import load_rules
from siligent.safety import BenchState, SafetyGate

FIRMWARE = Path(__file__).resolve().parent.parent / "firmware" / "l476" / "build"


class FakeBackend:
    """Stands in for a probe: memory is a dict of address -> byte."""

    def __init__(self, dev_id: int = 0x415, optr: int = 0xFFEFF8AA, sleeping: bool = False) -> None:
        # sleeping=True mimics the real L476 + ST-LINK: while the core sleeps,
        # every read returns zeros, until the core is halted.
        self.core = "sleeping" if sleeping else "running"
        self.events: list[str] = []
        self.unique_id = "FAKE123"
        self.description = "fake probe"
        self.mem: dict[int, int] = {}
        self.regs = {0xE0042000: 0x10006000 | dev_id, 0x40022020: optr}
        self.programmed: list[Segment] = []
        self.writes: list[tuple[int, bytes]] = []
        self.on_resume = None
        self.resets: list[bool] = []

    def open(self) -> None: ...

    def close(self) -> None: ...

    def read32(self, address: int) -> int:
        self.events.append(f"read32 {address:#x}")
        if self.core == "sleeping" and address != 0xE0042000:  # DBGMCU stays readable
            return 0
        if address in self.regs:
            return self.regs[address]
        return int.from_bytes(self.read_memory(address, 4), "little")

    def read_memory(self, address: int, length: int) -> bytes:
        if self.core == "sleeping":
            return bytes(length)
        return bytes(self.mem.get(address + i, 0xFF if 0x08000000 <= address < 0x08100000 else 0) for i in range(length))

    def write_memory(self, address: int, data: bytes) -> None:
        self.writes.append((address, data))
        for i, b in enumerate(data):
            self.mem[address + i] = b

    def program(self, segments) -> None:
        self.programmed.extend(segments)
        for s in segments:
            for i, b in enumerate(s.data):
                self.mem[s.address + i] = b

    def reset(self, halt: bool = False) -> None:
        self.resets.append(halt)

    def resume(self) -> None:
        self.events.append("resume")
        self.core = "running"
        if self.on_resume:
            self.on_resume(self)
            self.on_resume = None

    def halt(self) -> None:
        self.events.append("halt")
        self.core = "halted"

    def state(self) -> str:
        return self.core


def good_image(extra: bytes = b"") -> Image:
    vectors = struct.pack("<II", 0x20018000, 0x08000045)
    return Image("good.elf", [Segment(0x08000000, vectors + bytes(0x40) + extra)])


@pytest.fixture
def rules():
    return load_rules("stm32l476rg")


@pytest.fixture
def gate(rules):
    return SafetyGate(rules, BenchState(flash_allowed=True))


@pytest.fixture
def backend():
    return FakeBackend()


@pytest.fixture
def probe(backend, rules, gate, tmp_path):
    return GuardedProbe(backend, rules, gate, ActionLog(tmp_path / "log.jsonl"), tmp_path / "snaps")
