"""Plain functions shared by the CLI and the MCP server. Each returns a JSON-ready dict."""

from __future__ import annotations

from .bench import Bench, BenchConfig
from .detect import scan_meters, scan_usb
from .errors import SiligentError, SafetyRefusal
from .image import load_image
from .rules import load_rules
from .runner import run_tests as _run_tests
from .safety import BenchState, SafetyGate


def _guard(fn):
    """Turn refusals and errors into structured results an agent can act on."""

    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except SafetyRefusal as e:
            return e.to_dict()
        except SiligentError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:  # hardware and library errors: report, don't crash the agent's tool call
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


@_guard
def detect(config: BenchConfig | None = None, connect: bool = True) -> dict:
    """List debug probes and, for supported ones, identify the board and its rules."""
    probes = [p.to_dict() for p in scan_usb()]
    result: dict = {"ok": True, "probes": probes, "meters": scan_meters()}
    if connect and any(p["supported"] for p in probes):
        try:
            with Bench(config) as bench:
                result["board"] = {
                    "name": bench.rules.name,
                    "chip": bench.rules.chip,
                    "dev_id": hex(bench.probe.device_id()),
                    "probe_uid": bench.probe.backend.unique_id,
                    "rules": bench.rules.source,
                }
        except SafetyRefusal as e:
            result["board"] = e.to_dict()
    return result


@_guard
def rules(board: str = "stm32l476rg") -> dict:
    """Show the safety rules for a board."""
    return {"ok": True, **load_rules(board).summary()}


@_guard
def check_image(path: str, board: str = "stm32l476rg", config: BenchConfig | None = None) -> dict:
    """Check a firmware image against a board's rules without touching hardware."""
    cfg = config or BenchConfig.load()
    image = load_image(path)
    gate = SafetyGate(load_rules(cfg.board or board), BenchState(cfg.flash_allowed))
    gate.check_image(image)
    return {"ok": True, "image": path, "sha256": image.sha256, "bytes": image.size, "detail": "passes image checks"}


@_guard
def flash(path: str, config: BenchConfig | None = None) -> dict:
    """Flash a firmware image through the safety gate."""
    with Bench(config) as bench:
        return bench.probe.flash(load_image(path))


@_guard
def restore(path: str, config: BenchConfig | None = None) -> dict:
    """Put a saved full-flash snapshot (raw .bin from the start of flash) back on the chip.

    The same safety checks apply as for any flash. Trailing erased bytes are skipped.
    """
    from pathlib import Path

    from .image import Image, Segment

    raw = Path(path).read_bytes().rstrip(b"\xff")
    raw += b"\xff" * (-len(raw) % 8)  # flash programs in 8-byte double words
    with Bench(config) as bench:
        image = Image(path, [Segment(bench.rules.flash.start, raw)])
        return bench.probe.flash(image)


@_guard
def run_tests(path: str, timeout_s: float = 10.0, config: BenchConfig | None = None) -> dict:
    """Flash test firmware, run it, and return pass/fail, cycles and energy."""
    with Bench(config) as bench:
        return _run_tests(bench.probe, load_image(path), meter=bench.meter, timeout_s=timeout_s)


@_guard
def read_memory(address: int, length: int, config: BenchConfig | None = None) -> dict:
    """Read memory from the chip."""
    if not 0 < length <= 4096:
        raise SiligentError("length must be between 1 and 4096 bytes")
    with Bench(config) as bench:
        data, halted = bench.probe.read_memory_checked(address, length)
    out = {"ok": True, "address": hex(address), "length": length, "hex": data.hex()}
    if halted:
        out["note"] = "the core was asleep, so it was halted briefly for this read and then resumed"
    return out


@_guard
def write_memory(address: int, data_hex: str, config: BenchConfig | None = None) -> dict:
    """Write bytes to RAM or a peripheral register, subject to the board's rules."""
    data = bytes.fromhex(data_hex)
    with Bench(config) as bench:
        bench.probe.write_memory(address, data)
    return {"ok": True, "address": hex(address), "length": len(data)}


@_guard
def action_log(n: int = 20, config: BenchConfig | None = None) -> dict:
    """The most recent bench actions, allowed and refused."""
    cfg = config or BenchConfig.load()
    from .log import ActionLog

    return {"ok": True, "entries": ActionLog(cfg.log_path).tail(n)}
