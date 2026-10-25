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
class BoardRules:
    name: str
    chip: str
    dev_id: int
    idcode_address: int
    pyocd_target: str
    flash: Region
    ram: tuple[Region, ...]
    allow_write: tuple[Region, ...]
    forbid_write: tuple[Region, ...]
    forbid_image_words: tuple[ForbiddenWord, ...]
    optr_address: int
    rdp_mask: int
    rdp_level0: int
    gpio_base: int
    gpio_stride: int
    gpio_ports: str
    protected_pins: tuple[ProtectedPin, ...] = field(default_factory=tuple)
    vdd_min_mv: int | None = None
    vdd_max_mv: int | None = None
    source: str = ""

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
            "flash_requires": f"read protection level 0 (RDP byte {hex(self.rdp_level0)})",
            "supply_voltage_mv": (
                {"min": self.vdd_min_mv, "max": self.vdd_max_mv} if self.vdd_max_mv else "no meter may power this board"
            ),
        }


def _region(d: dict, default_name: str = "") -> Region:
    return Region(d.get("name", default_name), int(d["start"]), int(d["size"]), d.get("reason", ""))


def parse_rules(data: dict, source: str = "") -> BoardRules:
    board, mem, prot, gpio = data["board"], data["memory"], data["protection"], data.get("gpio", {})
    power = data.get("power", {})
    return BoardRules(
        name=board["name"],
        chip=board["chip"],
        dev_id=int(board["dev_id"]),
        idcode_address=int(board["idcode_address"]),
        pyocd_target=board["pyocd_target"],
        flash=_region(mem["flash"], "Flash"),
        ram=tuple(_region(r) for r in mem["ram"]),
        allow_write=tuple(_region(r) for r in data.get("allow_write", [])),
        forbid_write=tuple(_region(r) for r in data.get("forbid_write", [])),
        forbid_image_words=tuple(
            ForbiddenWord(int(w["value"]), w["reason"]) for w in data.get("forbid_image_word", [])
        ),
        optr_address=int(prot["optr_address"]),
        rdp_mask=int(prot["rdp_mask"]),
        rdp_level0=int(prot["rdp_level0"]),
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


def rules_for_dev_id(dev_id: int) -> BoardRules | None:
    """Find built-in rules whose chip matches a device ID read from the chip."""
    for name in builtin_boards():
        rules = load_rules(name)
        if rules.dev_id == dev_id:
            return rules
    return None
