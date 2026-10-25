"""ST-LINK (and other CMSIS-DAP style probes) through pyOCD."""

from __future__ import annotations

import logging

from ..errors import HardwareError
from ..image import Segment


class PyOCDBackend:
    def __init__(self, target: str, unique_id: str | None = None, frequency_hz: int = 4_000_000) -> None:
        self.target_name = target
        self.unique_id = unique_id or ""
        self.description = ""
        self.frequency_hz = frequency_hz
        self._session = None
        self._target = None

    def open(self) -> None:
        from pyocd.core.helpers import ConnectHelper

        logging.getLogger("pyocd").setLevel(logging.WARNING)
        session = ConnectHelper.session_with_chosen_probe(
            blocking=False,
            unique_id=self.unique_id or None,
            options={
                "target_override": self.target_name,
                # Attach without halting, so reading never disturbs running firmware.
                "connect_mode": "attach",
                "frequency": self.frequency_hz,
                "hide_programming_progress": True,
            },
        )
        if session is None:
            raise HardwareError("no debug probe found (is the board plugged in?)")
        try:
            session.open()
        except Exception as e:
            raise HardwareError(f"could not connect to the chip through the probe: {e}") from e
        self._session = session
        self._target = session.board.target
        self.unique_id = session.probe.unique_id
        self.description = session.probe.description

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None
            self._target = None

    def _t(self):
        if self._target is None:
            raise HardwareError("probe is not open")
        return self._target

    def read32(self, address: int) -> int:
        return self._t().read32(address)

    def read_memory(self, address: int, length: int) -> bytes:
        return bytes(self._t().read_memory_block8(address, length))

    def write_memory(self, address: int, data: bytes) -> None:
        self._t().write_memory_block8(address, list(data))

    def program(self, segments: list[Segment]) -> None:
        from pyocd.flash.loader import MemoryLoader

        # Stop whatever firmware is running first. Erasing flash under running code
        # locks the core up and makes the erase time out (seen on the real L476).
        self._t().reset_and_halt()
        # Sector erase only: a chip erase would wipe more than the image covers.
        loader = MemoryLoader(self._session, chip_erase="sector", smart_flash=True, keep_unwritten=True)
        for seg in segments:
            loader.add_data(seg.address, seg.data)
        loader.commit()

    def reset(self, halt: bool = False) -> None:
        if halt:
            self._t().reset_and_halt()
        else:
            self._t().reset()

    def halt(self) -> None:
        self._t().halt()

    def resume(self) -> None:
        self._t().resume()

    def state(self) -> str:
        return self._t().get_state().name.lower()
