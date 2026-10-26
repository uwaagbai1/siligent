"""Plain functions shared by the CLI and the MCP server. Each returns a JSON-ready dict."""

from __future__ import annotations

import dataclasses

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
    """List debug probes and, for each supported one, identify the board and its rules."""
    import dataclasses

    cfg = config or BenchConfig.load()
    found = scan_usb()
    result: dict = {"ok": True, "probes": [p.to_dict() for p in found], "meters": scan_meters()}
    if not connect:
        return result
    boards = []
    for p in (p for p in found if p.supported):
        one = dataclasses.replace(cfg, probe_uid=p.serial, virtual=False)
        try:
            with Bench(one) as bench:
                boards.append(
                    {
                        "probe_uid": p.serial,
                        "probe": p.kind,
                        "name": bench.rules.name,
                        "chip": bench.rules.chip,
                        "dev_id": hex(bench.probe.device_id()),
                        "rules": bench.rules.source,
                        "energy": "built-in (TI EnergyTrace)" if hasattr(bench._backend, "energy_meter") else None,
                    }
                )
        except SafetyRefusal as e:
            boards.append({"probe_uid": p.serial, "probe": p.kind, **e.to_dict()})
        except Exception as e:
            boards.append({"probe_uid": p.serial, "probe": p.kind, "ok": False, "error": f"{type(e).__name__}: {e}"})
    result["boards"] = boards
    if cfg.probe_uid and not any(b["probe_uid"] == cfg.probe_uid for b in boards):
        result["note"] = f"bench.toml pins probe {cfg.probe_uid}, which is not connected"
    return result


@_guard
def rules(board: str = "stm32l476rg") -> dict:
    """Show the safety rules for a board."""
    from .rules import builtin_boards

    return {"ok": True, **load_rules(board).summary(), "built_in_boards": builtin_boards()}


def _board_for(image, board: str | None, cfg: BenchConfig) -> str:
    """Explicit board, then bench.toml, then the only built-in board for the image's architecture."""
    if board or cfg.board:
        return board or cfg.board
    from .rules import builtin_boards

    matches = [b for b in builtin_boards() if load_rules(b).arch == image.arch]
    if len(matches) == 1:
        return matches[0]
    raise SiligentError(f"say which board to check against ({', '.join(builtin_boards())})")


@_guard
def check_image(path: str, board: str | None = None, config: BenchConfig | None = None) -> dict:
    """Check a firmware image against a board's rules without touching hardware."""
    cfg = config or BenchConfig.load()
    image = load_image(path)
    name = _board_for(image, board, cfg)
    gate = SafetyGate(load_rules(name), BenchState(cfg.flash_allowed))
    gate.check_image(image)
    return {"ok": True, "image": path, "board": name, "sha256": image.sha256, "bytes": image.size,
            "detail": "passes image checks"}


def _for_image(image, config: BenchConfig | None) -> BenchConfig:
    """A virtual bench simulates the board the image was built for, unless one is named."""
    cfg = config or BenchConfig.load()
    if cfg.virtual and not cfg.board:
        cfg = dataclasses.replace(cfg, board=_board_for(image, None, cfg))
    return cfg


@_guard
def flash(path: str, config: BenchConfig | None = None) -> dict:
    """Flash a firmware image through the safety gate."""
    image = load_image(path)
    with Bench(_for_image(image, config)) as bench:
        return bench.probe.flash(image)


@_guard
def restore(path: str, config: BenchConfig | None = None) -> dict:
    """Put a saved full-flash snapshot (raw .bin from the start of flash) back on the chip.

    The same safety checks apply as for any flash. Trailing erased bytes are skipped.
    """
    from pathlib import Path

    from .image import Image, Segment

    import hashlib

    data = Path(path).read_bytes()
    with Bench(config) as bench:
        if getattr(bench.probe.backend, "erases", True):
            # Flash is erased before programming, so trailing erased bytes need not be written.
            raw = data.rstrip(b"\xff")
            raw += b"\xff" * (-len(raw) % 8)  # flash programs in 8-byte double words
        else:
            # FRAM is written in place: write every byte, or newer code would survive past the end.
            raw = data
        image = Image(path, [Segment(bench.rules.flash.start, raw)])
        result = bench.probe.flash(image)
        back = bench.probe.read_memory(bench.rules.flash.start, len(raw))
        result["verified"] = back == raw
        result["restored_sha256"] = hashlib.sha256(back).hexdigest()
        if not result["verified"]:
            result["ok"] = False
            result["error"] = "read-back after restore does not match the snapshot"
        return result


@_guard
def run_tests(path: str, timeout_s: float = 10.0, config: BenchConfig | None = None) -> dict:
    """Flash test firmware, run it, and return pass/fail, cycles and energy."""
    image = load_image(path)
    with Bench(_for_image(image, config)) as bench:
        return _run_tests(bench.probe, image, meter=bench.meter, timeout_s=timeout_s)


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
