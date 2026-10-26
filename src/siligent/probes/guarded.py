"""GuardedProbe: the only way Siligent touches a probe.

Every write and flash passes the safety gate here, and every action (allowed
or refused) is written to the action log. The CLI, the Python API and the
MCP server all go through this class, so none of them can get around the rules.
"""

from __future__ import annotations

import time
from pathlib import Path

from ..errors import SafetyRefusal
from ..image import Image, Segment
from ..log import ActionLog
from ..rules import BoardRules
from ..safety import SafetyGate
from .base import ProbeBackend


class GuardedProbe:
    def __init__(
        self,
        backend: ProbeBackend,
        rules: BoardRules,
        gate: SafetyGate,
        log: ActionLog,
        snapshot_dir: str | Path,
    ) -> None:
        self.backend = backend
        self.rules = rules
        self.gate = gate
        self.log = log
        self.snapshot_dir = Path(snapshot_dir).expanduser()

    # Reads are allowed anywhere. Some peripheral registers clear flags when read,
    # so reads are still logged.
    def read_memory(self, address: int, length: int) -> bytes:
        data, _ = self.read_memory_checked(address, length)
        return data

    def read_memory_checked(self, address: int, length: int) -> tuple[bytes, bool]:
        """Read memory; returns (data, halted_to_read).

        While the core sleeps, the ST-LINK returns zeros for RAM and flash with no
        error. So a sleeping core is halted for the read, then resumed.
        """
        halted = self.backend.state() == "sleeping"
        if halted:
            self.backend.halt()
        try:
            data = self.backend.read_memory(address, length)
        finally:
            if halted:
                self.backend.resume()
        self.log.record("read_memory", "ok", address=hex(address), length=length, halted_to_read=halted)
        return data, halted

    def read32(self, address: int) -> int:
        return self.backend.read32(address)

    def device_id(self) -> int:
        return self.backend.read32(self.rules.idcode_address) & self.rules.idcode_mask

    def write_memory(self, address: int, data: bytes) -> None:
        try:
            self.gate.check_host_write(address, data)
        except SafetyRefusal as e:
            self.log.record("write_memory", "refused", rule=e.rule, address=hex(address), length=len(data))
            raise
        self.backend.write_memory(address, data)
        self.log.record("write_memory", "ok", address=hex(address), length=len(data))

    def flash(self, image: Image) -> dict:
        """Check, snapshot, then program an image. Returns what was done."""
        halted = False
        try:
            self.gate.check_bench_for_flash()
            self.gate.check_image(image)
            # Halt first: while the core sleeps, register reads come back as zeros.
            self.backend.halt()
            halted = True
            dev_id = self.device_id()
            if dev_id != self.rules.dev_id:
                raise SafetyRefusal(
                    "chip.mismatch",
                    f"The connected chip (device ID {hex(dev_id)}) is not a {self.rules.chip} "
                    f"({hex(self.rules.dev_id)}); these rules do not apply to it.",
                )
            self._check_protection()
        except SafetyRefusal as e:
            if halted:
                self.backend.resume()  # a refusal leaves the board as it was
            self.log.record("flash", "refused", rule=e.rule, image=image.path, sha256=image.sha256)
            raise

        # A virtual board starts fresh each time, so there is nothing to back up.
        snapshot = self._snapshot(image.segments) if getattr(self.backend, "persistent", True) else None
        self.backend.program(image.segments)
        result = {
            "ok": True,
            "image": image.path,
            "sha256": image.sha256,
            "bytes": image.size,
            "snapshot": str(snapshot) if snapshot else None,
        }
        self.log.record("flash", "ok", **result)
        return result

    def _check_protection(self) -> None:
        p = self.rules.protection
        if p.kind == "erased":
            self.gate.check_protection_bytes(self.backend.read_memory(p.address, p.size))
        else:
            self.gate.check_read_protection(self.backend.read32(p.address))

    def reset(self, halt: bool = False) -> None:
        self.backend.reset(halt=halt)
        self.log.record("reset", "ok", halt=halt)

    def resume(self) -> None:
        self.backend.resume()

    def halt(self) -> None:
        self.backend.halt()

    def _snapshot(self, segments: list[Segment]) -> Path:
        """Save the flash pages about to be overwritten, so they can be put back.

        The first flash on a device also saves the whole flash as a baseline.
        """
        uid = self.backend.unique_id or "unknown"
        folder = self.snapshot_dir / uid
        folder.mkdir(parents=True, exist_ok=True)
        baseline = folder / "baseline-full-flash.bin"
        if not baseline.exists():
            baseline.write_bytes(self.backend.read_memory(self.rules.flash.start, self.rules.flash.size))
            self.log.record("snapshot", "ok", kind="baseline", path=str(baseline))

        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = folder / f"pages-{stamp}.bin"
        page = self.rules.snapshot_page
        with path.open("wb") as f:
            for seg in segments:
                start = seg.address - (seg.address % page)
                end = -(-seg.end // page) * page
                f.write(start.to_bytes(4, "little") + (end - start).to_bytes(4, "little"))
                f.write(self.backend.read_memory(start, end - start))
        return path
