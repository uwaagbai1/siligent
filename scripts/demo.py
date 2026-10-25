"""A two-minute tour of Siligent on the virtual L476. No hardware needed.

    nice -n 19 make -j3 -C firmware/l476
    .venv/bin/python scripts/demo.py

Shows: a passing test run, a failing run that points at the line, two unsafe
actions being refused, and the action log that recorded all of it.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

from siligent import api
from siligent.bench import BenchConfig

ROOT = Path(__file__).resolve().parent.parent
FW = ROOT / "firmware" / "l476" / "build"
PAUSE = 0.0 if "--fast" in sys.argv else 1.2


def say(title: str) -> None:
    print(f"\n\033[1m== {title}\033[0m")
    time.sleep(PAUSE)


def show_run(r: dict) -> None:
    for t in r.get("tests", []):
        mark = "\033[32mpass\033[0m" if t["status"] == "pass" else "\033[31mFAIL\033[0m"
        where = f"  (line {t['fail_line']})" if t.get("fail_line") else ""
        print(f"  {mark}  {t['name']:<20} {t['cycles']:>6} cycles{where}")
    print(f"  -> {r.get('passed', 0)} passed, {r.get('failed', 0)} failed")


def show_refusal(r: dict, expected_rule: str) -> bool:
    if r.get("refused") and r.get("rule") == expected_rule:
        print(f"  \033[33mREFUSED\033[0m by rule {r['rule']}")
        print(f"  why: {r['reason']}")
        return True
    print(f"  unexpected: {r}")
    return False


def main() -> int:
    if not (FW / "tests.elf").exists():
        print("Build the firmware first: nice -n 19 make -j3 -C firmware/l476")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        cfg = BenchConfig(virtual=True, log_path=Path(tmp) / "log.jsonl", snapshot_dir=Path(tmp) / "snaps")

        say("1. Run the on-device tests on a virtual STM32L476")
        r = api.run_tests(str(FW / "tests.elf"), config=cfg)
        if "tests" not in r:
            print(f"  could not run: {r.get('error') or r}")
            return 1
        show_run(r)
        good = r.get("passed") == 4 and r.get("failed") == 0

        say("2. Same tests plus a broken one: the failure points at the source line")
        r = api.run_tests(str(FW / "tests_fail.elf"), config=cfg)
        show_run(r)
        failed = [t["name"] for t in r.get("tests", []) if t["status"] == "fail"]
        good &= failed == ["deliberate_failure"]

        say("3. Flash firmware that could lock the chip (it carries the option-byte keys)")
        good &= show_refusal(api.flash(str(FW / "demo_refused.elf"), config=cfg), "image.forbidden_word")

        say("4. Write straight to the flash controller's option register")
        good &= show_refusal(api.write_memory(0x40022020, "00000000", config=cfg), "write.forbidden_region")

        say("5. Every action, allowed or refused, is logged (low-level steps hidden here)")
        for e in api.action_log(50, config=cfg).get("entries", []):
            if e.get("action") != "run_tests" and e.get("outcome") != "refused":
                continue
            print(f"  {e.get('action', '?'):<14} {e.get('outcome', '?'):<9} {e.get('rule', '')}")
    print()
    if not good:
        print("Demo did not go as expected (see above).")
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
