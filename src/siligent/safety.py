"""The safety gate: every write and every flash is checked here before it reaches a probe."""

from __future__ import annotations

from dataclasses import dataclass

from .errors import SafetyRefusal
from .image import Image
from .rules import BoardRules


@dataclass
class BenchState:
    """Facts about the physical bench that software cannot see for itself.

    `flash_allowed` must be set to True by a person in bench.toml, after
    checking the bench is safe to flash (for example, that test equipment
    which could be damaged is unplugged). Unset (None) or False refuses.
    """

    flash_allowed: bool | None = None


class SafetyGate:
    def __init__(self, rules: BoardRules, bench: BenchState | None = None) -> None:
        self.rules = rules
        self.bench = bench or BenchState()

    # Bench preconditions -------------------------------------------------

    def check_bench_for_flash(self) -> None:
        if self.bench.flash_allowed is not True:
            raise SafetyRefusal(
                "bench.flash_not_allowed",
                "A person has not confirmed this bench is safe to flash. Check the bench "
                "(for example, unplug test equipment that must not be attached while flashing), "
                "then set flash_allowed = true in bench.toml.",
                flash_allowed=self.bench.flash_allowed,
            )

    # Host memory writes --------------------------------------------------

    def check_host_write(self, address: int, data: bytes) -> None:
        length = len(data)
        if length == 0:
            return
        for region in self.rules.forbid_write:
            if region.overlaps(address, length):
                raise SafetyRefusal(
                    "write.forbidden_region",
                    f"Writes to {region.name} are not allowed. {region.reason}",
                    address=hex(address),
                    length=length,
                    region=region.name,
                )
        if not any(r.contains(address, length) for r in self.rules.allow_write):
            raise SafetyRefusal(
                "write.outside_allowed",
                "Host writes are only allowed in RAM, peripherals and core debug registers. "
                "Use the flash tool to change flash.",
                address=hex(address),
                length=length,
            )
        self._check_protected_pins(address, data)

    def _check_protected_pins(self, address: int, data: bytes) -> None:
        for pin in self.rules.protected_pins:
            moder = self.rules.moder_address(pin.port)
            if not (address < moder + 4 and moder < address + len(data)):
                continue
            # Only a whole, aligned 32-bit write can be checked bit by bit.
            if address > moder or address + len(data) < moder + 4:
                raise SafetyRefusal(
                    "pin.partial_moder_write",
                    f"Partial write to GPIO{pin.port}_MODER cannot be checked; write the whole register.",
                    address=hex(address),
                )
            off = moder - address
            value = int.from_bytes(data[off : off + 4], "little")
            mode = (value >> (2 * pin.pin)) & 0b11
            if mode != pin.required_mode:
                raise SafetyRefusal(
                    "pin.protected",
                    f"P{pin.port}{pin.pin} must stay in mode {pin.required_mode}: {pin.reason}",
                    pin=f"P{pin.port}{pin.pin}",
                    requested_mode=mode,
                )

    # Firmware images -----------------------------------------------------

    def check_image(self, image: Image) -> None:
        flash = self.rules.flash
        for seg in image.segments:
            if not flash.contains(seg.address, len(seg.data)):
                raise SafetyRefusal(
                    "image.outside_flash",
                    f"Firmware has data at {hex(seg.address)}-{hex(seg.end)}, outside flash "
                    f"({hex(flash.start)}-{hex(flash.end)}).",
                    address=hex(seg.address),
                    length=len(seg.data),
                )
        self._check_vector_table(image)
        self._check_forbidden_words(image)

    def _check_vector_table(self, image: Image) -> None:
        start = self.rules.flash.start
        sp, reset = image.word_at(start), image.word_at(start + 4)
        if sp is None or reset is None:
            raise SafetyRefusal(
                "image.no_vector_table",
                f"Firmware does not start with a vector table at {hex(start)}, so the chip would not boot it.",
            )
        if not any(r.start < sp <= r.end for r in self.rules.ram):
            raise SafetyRefusal(
                "image.bad_stack_pointer",
                f"Initial stack pointer {hex(sp)} is not inside RAM.",
                stack_pointer=hex(sp),
            )
        if not (reset & 1) or not self.rules.flash.contains(reset & ~1):
            raise SafetyRefusal(
                "image.bad_reset_vector",
                f"Reset handler {hex(reset)} must be an odd (Thumb) address inside flash.",
                reset_vector=hex(reset),
            )

    def _check_forbidden_words(self, image: Image) -> None:
        for word in self.rules.forbid_image_words:
            needle = word.value.to_bytes(4, "little")
            for seg in image.segments:
                pos = seg.data.find(needle)
                if pos >= 0:
                    raise SafetyRefusal(
                        "image.forbidden_word",
                        word.reason,
                        word=hex(word.value),
                        address=hex(seg.address + pos),
                    )

    # Power ------------------------------------------------------------------

    def check_supply_voltage(self, voltage_mv: int) -> None:
        """A meter that powers the board may only apply a voltage inside the board's range."""
        lo, hi = self.rules.vdd_min_mv, self.rules.vdd_max_mv
        if lo is None or hi is None:
            raise SafetyRefusal(
                "power.no_limits",
                f"The rules for {self.rules.name} give no supply range, so no meter may power it.",
            )
        if not lo <= voltage_mv <= hi:
            raise SafetyRefusal(
                "power.voltage_out_of_range",
                f"{voltage_mv} mV is outside the {self.rules.chip} supply range ({lo}-{hi} mV).",
                voltage_mv=voltage_mv,
            )

    # Chip state ------------------------------------------------------------

    def check_read_protection(self, optr: int) -> None:
        if optr == 0:
            # Reserved bits read as 1 on a real chip, so all zeros means the read failed.
            raise SafetyRefusal(
                "chip.unreadable",
                "The option register read as all zeros, which a real chip never reports. The core may "
                "be asleep or the probe link is bad; protection state is unknown, so flashing is refused.",
            )
        rdp = optr & self.rules.rdp_mask
        if rdp != self.rules.rdp_level0:
            raise SafetyRefusal(
                "chip.read_protection",
                f"Read protection is not at level 0 (RDP byte {hex(rdp)}, expected {hex(self.rules.rdp_level0)}). "
                "Flashing could mass-erase or the chip may be locked; a person must handle this.",
                optr=hex(optr),
            )
