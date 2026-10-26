"""Command line: `siligent <command>`. Every command prints JSON."""

from __future__ import annotations

import argparse
import json
import sys

from . import api
from .bench import BenchConfig


def _int(s: str) -> int:
    return int(s, 0)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="siligent", description="Safe, measured access to real hardware.")
    p.add_argument("--config", help="path to bench.toml (default: ./bench.toml or ~/.config/siligent/bench.toml)")
    p.add_argument("--virtual", action="store_true", help="use a Renode virtual board instead of real hardware")
    p.add_argument("--probe", help="serial of the probe to use (see `siligent detect`) when several are connected")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("detect", help="find probes and identify the board")
    d.add_argument("--no-connect", action="store_true", help="only scan USB; do not open the probe")
    r = sub.add_parser("rules", help="show a board's safety rules")
    r.add_argument("board", nargs="?", default="stm32l476rg")
    c = sub.add_parser("check", help="check a firmware image against the rules, without hardware")
    c.add_argument("image")
    c.add_argument("--board", help="board rules to check against (default: from the image)")
    f = sub.add_parser("flash", help="flash a firmware image")
    f.add_argument("image")
    rs = sub.add_parser("restore", help="put a full-flash snapshot (.bin) back on the chip")
    rs.add_argument("snapshot")
    t = sub.add_parser("test",help="flash test firmware, run it, report pass/fail and cycles (plus energy if a power meter is set up)")
    t.add_argument("image")
    t.add_argument("--timeout", type=float, default=10.0)
    rd = sub.add_parser("read", help="read memory")
    rd.add_argument("address", type=_int)
    rd.add_argument("length", type=_int)
    w = sub.add_parser("write", help="write bytes (hex) to RAM or a peripheral")
    w.add_argument("address", type=_int)
    w.add_argument("data_hex")
    lg = sub.add_parser("log", help="show recent bench actions")
    lg.add_argument("-n", type=int, default=20)

    a = p.parse_args(argv)
    cfg = BenchConfig.load(a.config)
    if a.virtual:
        cfg.virtual = True
    if a.probe:
        cfg.probe_uid = a.probe
    if a.cmd == "detect":
        out = api.detect(cfg, connect=not a.no_connect)
    elif a.cmd == "rules":
        out = api.rules(a.board)
    elif a.cmd == "check":
        out = api.check_image(a.image, board=a.board, config=cfg)
    elif a.cmd == "flash":
        out = api.flash(a.image, config=cfg)
    elif a.cmd == "restore":
        out = api.restore(a.snapshot, config=cfg)
    elif a.cmd == "test":
        out = api.run_tests(a.image, timeout_s=a.timeout, config=cfg)
    elif a.cmd == "read":
        out = api.read_memory(a.address, a.length, config=cfg)
    elif a.cmd == "write":
        out = api.write_memory(a.address, a.data_hex, config=cfg)
    else:
        out = api.action_log(a.n, config=cfg)
    json.dump(out, sys.stdout, indent=2)
    print()
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
