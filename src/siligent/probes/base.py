"""The interface a probe plugin implements. Plugins never apply safety rules themselves."""

from __future__ import annotations

from typing import Protocol

from ..image import Segment


class ProbeBackend(Protocol):
    unique_id: str
    description: str

    def open(self) -> None: ...

    def close(self) -> None: ...

    def read32(self, address: int) -> int: ...

    def read_memory(self, address: int, length: int) -> bytes: ...

    def write_memory(self, address: int, data: bytes) -> None: ...

    def program(self, segments: list[Segment]) -> None:
        """Halt the core with a reset, then erase the sectors these segments cover and program
        them. Never a whole-chip erase."""

    def reset(self, halt: bool = False) -> None: ...

    def halt(self) -> None: ...

    def resume(self) -> None: ...

    def state(self) -> str:
        """'running', 'halted', 'sleeping', 'lockup' or 'reset'."""
