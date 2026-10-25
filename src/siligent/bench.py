"""The bench: configuration plus the pieces wired together (probe, rules, meter, log)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import SiligentError, SafetyRefusal
from .log import ActionLog
from .meters.base import PowerMeter
from .probes.guarded import GuardedProbe
from .rules import BoardRules, load_rules, rules_for_dev_id
from .safety import BenchState, SafetyGate

DATA_DIR = Path(os.environ.get("SILIGENT_HOME", "~/.local/share/siligent")).expanduser()
CONFIG_PATHS = [
    *([Path(os.environ["SILIGENT_CONFIG"]).expanduser()] if os.environ.get("SILIGENT_CONFIG") else []),
    Path("bench.toml"),
    Path("~/.config/siligent/bench.toml").expanduser(),
]


@dataclass
class BenchConfig:
    flash_allowed: bool | None = None
    probe_uid: str | None = None
    board: str | None = None  # rules name or path; None = detect from the chip
    meter: dict = field(default_factory=lambda: {"type": "none"})
    log_path: Path = DATA_DIR / "actions.jsonl"
    snapshot_dir: Path = DATA_DIR / "snapshots"
    virtual: bool = False  # use a Renode virtual board instead of real hardware
    renode_path: str | None = None
    source: str = "defaults"

    @classmethod
    def load(cls, path: str | Path | None = None) -> BenchConfig:
        candidates = [Path(path)] if path else CONFIG_PATHS
        for p in candidates:
            if p.is_file():
                d = tomllib.loads(p.read_text())
                bench = d.get("bench", {})
                return cls(
                    flash_allowed=bench.get("flash_allowed"),
                    probe_uid=bench.get("probe_uid"),
                    board=bench.get("board"),
                    meter=d.get("meter", {"type": "none"}),
                    log_path=Path(bench.get("log_path", DATA_DIR / "actions.jsonl")).expanduser(),
                    snapshot_dir=Path(bench.get("snapshot_dir", DATA_DIR / "snapshots")).expanduser(),
                    virtual=bool(bench.get("virtual", False)),
                    renode_path=bench.get("renode_path"),
                    source=str(p),
                )
        if path:
            raise SiligentError(f"no bench config at {path}")
        return cls()


METER_PLUGIN_GROUP = "siligent.meters"


def meter_plugins() -> dict:
    from importlib.metadata import entry_points

    return {ep.name: ep for ep in entry_points(group=METER_PLUGIN_GROUP)}


def make_meter(cfg: dict, gate: SafetyGate | None) -> PowerMeter | None:
    """Build the configured meter. A meter that powers the board ("mode = source")
    is checked against the board's supply range here, before any plugin code runs."""
    kind = cfg.get("type", "none").lower()
    if kind == "none":
        return None
    if cfg.get("mode") == "source":
        if gate is None:
            raise SiligentError("connect to the board first: its rules decide the allowed supply voltage")
        if "voltage_mv" not in cfg:
            raise SiligentError("a meter in source mode needs voltage_mv in bench.toml")
        gate.check_supply_voltage(int(cfg["voltage_mv"]))
    if kind == "ina219":
        from .meters.ina219 import INA219, LinuxI2CBus

        return INA219(
            LinuxI2CBus(int(cfg["i2c_bus"])),
            address=int(cfg.get("address", 0x40)),
            shunt_ohms=float(cfg.get("shunt_ohms", 0.1)),
        )
    plugins = meter_plugins()
    if kind in plugins:
        return plugins[kind].load()(cfg)
    available = ", ".join(["none", "ina219", *sorted(plugins)])
    raise SiligentError(f"unknown meter type '{kind}' (available: {available})")


class Bench:
    """Open a probe, work out which board it is on, and apply that board's rules."""

    def __init__(self, config: BenchConfig | None = None) -> None:
        self.config = config or BenchConfig.load()
        self.log = ActionLog(self.config.log_path)
        self.probe: GuardedProbe | None = None
        self.rules: BoardRules | None = None
        self._backend = None
        self._meter: PowerMeter | None = None
        self._meter_made = False

    @property
    def meter(self) -> PowerMeter | None:
        if self.config.virtual:
            return None  # a physical meter cannot measure a simulated chip
        if not self._meter_made:
            self._meter = make_meter(self.config.meter, self.probe.gate if self.probe else None)
            self._meter_made = True
        return self._meter

    def connect(self) -> GuardedProbe:
        if self.config.virtual:
            return self._connect_virtual()
        from .probes.pyocd_backend import PyOCDBackend

        # Read the chip ID with a generic Cortex-M connection, then load the matching rules.
        hint = load_rules(self.config.board) if self.config.board else None
        backend = PyOCDBackend(
            target=hint.pyocd_target if hint else "cortex_m", unique_id=self.config.probe_uid
        )
        backend.open()
        try:
            rules = hint
            if rules is None:
                # DBGMCU_IDCODE lives at 0xE0042000 on STM32 F4/L4; other families add their own rules.
                dev_id = backend.read32(0xE0042000) & 0xFFF
                rules = rules_for_dev_id(dev_id)
                if rules is None:
                    raise SafetyRefusal(
                        "board.unknown",
                        f"No safety rules for this chip (device ID {hex(dev_id)}), so only reads are allowed.",
                        dev_id=hex(dev_id),
                    )
                backend.close()
                backend = PyOCDBackend(target=rules.pyocd_target, unique_id=backend.unique_id)
                backend.open()
        except Exception:
            backend.close()
            raise
        gate = SafetyGate(rules, BenchState(flash_allowed=self.config.flash_allowed))
        self._backend = backend
        self.rules = rules
        self.probe = GuardedProbe(backend, rules, gate, self.log, self.config.snapshot_dir)
        self.log.record("connect", "ok", probe=backend.unique_id, board=rules.name)
        return self.probe

    def _connect_virtual(self) -> GuardedProbe:
        from .probes.renode_backend import RenodeBackend

        board = self.config.board or "stm32l476rg"
        rules = load_rules(board)
        backend = RenodeBackend(board=Path(board).stem, renode=self.config.renode_path)
        backend.open()
        # Every rule applies as on real hardware, except the bench check:
        # nothing physical is flashed, so no equipment can be harmed.
        gate = SafetyGate(rules, BenchState(flash_allowed=True))
        self._backend = backend
        self.rules = rules
        self.probe = GuardedProbe(backend, rules, gate, self.log, self.config.snapshot_dir)
        self.log.record("connect", "ok", probe=backend.unique_id, board=rules.name, virtual=True)
        return self.probe

    def close(self) -> None:
        if self._backend is not None:
            self._backend.close()
            self._backend = None
            self.probe = None

    def __enter__(self) -> Bench:
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
