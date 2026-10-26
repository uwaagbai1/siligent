"""Board safety rules, loaded from TOML files in siligent/boards/."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path


@dataclass(frozen=True)
class Region:
    name: str
    start: int
    size: int
    reason: str = ""

    @property
    def end(self) -> int:
        return self.start + self.size

    def contains(self, addr: int, length: int = 1) -> bool:
        return self.start <= addr and addr + length <= self.end

    def overlaps(self, addr: int, length: int) -> bool:
        return addr < self.end and self.start < addr + length


@dataclass(frozen=True)
class ForbiddenWord:
    value: int
    reason: str


@dataclass(frozen=True)
class ProtectedPin:
    port: str
    pin: int
    required_mode: int
    reason: str


@dataclass(frozen=True)
class Protection:
    """How to tell, before flashing, that the chip is not locked or protected.

    kind "stm32_rdp": read the option register; the RDP byte must be level 0.
    kind "erased": the bytes at address..address+size must all read 0xFF
    (MSP430 FRAM parts: JTAG, BSL and IP-encapsulation signatures).
    """

    kind: str
    address: int
    size: int = 4
    mask: int = 0xFF
    expected: int = 0
    reason: str = ""


@dataclass(frozen=True)
class BoardRules:
    name: str
    chip: str
    dev_id: int
    idcode_address: int
    flash: Region
    ram: tuple[Region, ...]
    allow_write: tuple[Region, ...]
    forbid_write: tuple[Region, ...]
    forbid_image_words: tuple[ForbiddenWord, ...]
    protection: Protection
    gpio_base: int = 0
    gpio_stride: int = 0
    gpio_ports: str = ""
    protected_pins: tuple[ProtectedPin, ...] = field(default_factory=tuple)
    vdd_min_mv: int | None = None
    vdd_max_mv: int | None = None
    arch: str = "cortex-m"  # "cortex-m" or "msp430": decides the boot checks
    probe: str = "pyocd"  # which backend drives this chip: "pyocd" or "msp430"
    pyocd_target: str = ""
    idcode_mask: int = 0xFFF
    reset_vector_address: int | None = None  # msp430: where the reset vector lives
    image_erased_only: tuple[Region, ...] = field(default_factory=tuple)
    snapshot_page: int = 2048
    source: str = ""

    # Older code and tests read these three directly.
    @property
    def optr_address(self) -> int:
        return self.protection.address

    @property
    def rdp_mask(self) -> int:
        return self.protection.mask

    @property
    def rdp_level0(self) -> int:
        return self.protection.expected

    def moder_address(self, port: str) -> int:
        return self.gpio_base + self.gpio_ports.index(port.upper()) * self.gpio_stride

    def summary(self) -> dict:
        """A plain description an agent or a person can read."""
        hexs = lambda r: {"name": r.name, "start": hex(r.start), "end": hex(r.end)}  # noqa: E731
        return {
            "board": self.name,
            "chip": self.chip,
            "dev_id": hex(self.dev_id),
            "flash": hexs(self.flash),
            "ram": [hexs(r) for r in self.ram],
            "host_writes_allowed_in": [hexs(r) for r in self.allow_write],
            "host_writes_forbidden_in": [hexs(r) | {"reason": r.reason} for r in self.forbid_write],
            "firmware_refused_if_it_contains": [
                {"word": hex(w.value), "reason": w.reason} for w in self.forbid_image_words
            ],
            "protected_pins": [
                {"pin": f"P{p.port}{p.pin}", "reason": p.reason} for p in self.protected_pins
            ],
            "firmware_bytes_that_must_stay_erased": [
                hexs(r) | {"reason": r.reason} for r in self.image_erased_only
            ],
            "flash_requires": self._flash_requirement(),
            "supply_voltage_mv": (
                {"min": self.vdd_min_mv, "max": self.vdd_max_mv} if self.vdd_max_mv else "no meter may power this board"
            ),
        }


    def _flash_requirement(self) -> str:
        p = self.protection
        if p.kind == "erased":
            return f"bytes {hex(p.address)}-{hex(p.address + p.size - 1)} read as 0xFF (chip not locked)"
        return f"read protection level 0 (RDP byte {hex(p.expected)})"


def _region(d: dict, default_name: str = "") -> Region:
    return Region(d.get("name", default_name), int(d["start"]), int(d["size"]), d.get("reason", ""))


def _protection(prot: dict) -> Protection:
    kind = prot.get("kind", "stm32_rdp")
    if kind == "stm32_rdp":
        return Protection(kind, int(prot["optr_address"]), 4, int(prot["rdp_mask"]), int(prot["rdp_level0"]))
    if kind == "erased":
        return Protection(kind, int(prot["address"]), int(prot["size"]), reason=prot.get("reason", ""))
    raise ValueError(f"unknown protection kind '{kind}'")


def parse_rules(data: dict, source: str = "") -> BoardRules:
    board, mem, prot, gpio = data["board"], data["memory"], data["protection"], data.get("gpio", {})
    power = data.get("power", {})
    arch = board.get("arch", "cortex-m")
    if arch not in ("cortex-m", "msp430"):
        raise ValueError(f"unknown arch '{arch}'")
    return BoardRules(
        name=board["name"],
        chip=board["chip"],
        dev_id=int(board["dev_id"]),
        idcode_address=int(board["idcode_address"]),
        arch=arch,
        probe=board.get("probe", "pyocd"),
        pyocd_target=board.get("pyocd_target", ""),
        idcode_mask=int(board.get("idcode_mask", 0xFFF)),
        reset_vector_address=int(board["reset_vector_address"]) if "reset_vector_address" in board else None,
        flash=_region(mem["flash"], "Flash"),
        snapshot_page=int(mem.get("snapshot_page", 2048)),
        image_erased_only=tuple(_region(r) for r in data.get("image_erased_only", [])),
        ram=tuple(_region(r) for r in mem["ram"]),
        allow_write=tuple(_region(r) for r in data.get("allow_write", [])),
        forbid_write=tuple(_region(r) for r in data.get("forbid_write", [])),
        forbid_image_words=tuple(
            ForbiddenWord(int(w["value"]), w["reason"]) for w in data.get("forbid_image_word", [])
        ),
        protection=_protection(prot),
        gpio_base=int(gpio.get("base", 0)),
        gpio_stride=int(gpio.get("stride", 0)),
        gpio_ports=gpio.get("ports", ""),
        protected_pins=tuple(
            ProtectedPin(p["port"].upper(), int(p["pin"]), int(p["required_mode"]), p["reason"])
            for p in data.get("protected_pin", [])
        ),
        vdd_min_mv=power.get("vdd_min_mv"),
        vdd_max_mv=power.get("vdd_max_mv"),
        source=source,
    )


def builtin_boards() -> list[str]:
    return sorted(p.name[:-5] for p in resources.files("siligent.boards").iterdir() if p.name.endswith(".toml"))


def load_rules(name_or_path: str) -> BoardRules:
    """Load rules by built-in name (e.g. 'stm32l476rg') or by file path."""
    path = Path(name_or_path)
    if path.suffix == ".toml" and path.exists():
        return parse_rules(tomllib.loads(path.read_text()), str(path))
    res = resources.files("siligent.boards") / f"{name_or_path.lower()}.toml"
    if not res.is_file():
        raise FileNotFoundError(f"no rules for board '{name_or_path}'; built-in: {', '.join(builtin_boards())}")
    return parse_rules(tomllib.loads(res.read_text()), f"builtin:{name_or_path.lower()}")


def rules_for_dev_id(dev_id: int, probe: str | None = None) -> BoardRules | None:
    """Find built-in rules whose chip matches a device ID read from the chip.

    `probe` limits the search to one chip family, since ID numbers from
    different families can collide.
    """
    for name in builtin_boards():
        rules = load_rules(name)
        if rules.dev_id == dev_id and (probe is None or rules.probe == probe):
            return rules
    return None
