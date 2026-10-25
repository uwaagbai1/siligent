"""Run on-device tests and collect pass/fail, cycles and energy.

The firmware (see firmware/) writes its results to a RAM "mailbox" at the
symbol `bp_results`. The host never talks to the chip while it measures
energy: debug traffic adds current, so it waits a quiet period first and only
then polls the mailbox for the result.
"""

from __future__ import annotations

import struct
import time
from dataclasses import asdict, dataclass

from .errors import SiligentError
from .image import Image
from .meters.base import PowerMeter
from .probes.guarded import GuardedProbe

MAGIC = 0x42504C54  # "BPLT"
VERSION = 1
HEADER = struct.Struct("<8I")  # magic, version, state, count, passed, failed, clock_hz, current
ENTRY = struct.Struct("<20sIII")  # name, status, cycles, fail_line
MAX_TESTS = 32
MAILBOX_SIZE = HEADER.size + MAX_TESTS * ENTRY.size

STATES = {0: "idle", 1: "running", 2: "done", 3: "crashed"}
STATUS = {0: "not run", 1: "pass", 2: "fail", 3: "crashed"}


@dataclass(frozen=True)
class TestResult:
    name: str
    status: str
    cycles: int
    time_us: float | None
    fail_line: int | None


def parse_mailbox(data: bytes) -> dict:
    magic, version, state, count, passed, failed, clock_hz, current = HEADER.unpack_from(data, 0)
    if magic != MAGIC:
        return {"state": "no results", "detail": "the firmware did not write a results mailbox"}
    if version != VERSION:
        return {"state": "unsupported", "detail": f"mailbox version {version}, expected {VERSION}"}
    tests = []
    for i in range(min(count, MAX_TESTS)):
        name, status, cycles, line = ENTRY.unpack_from(data, HEADER.size + i * ENTRY.size)
        tests.append(
            TestResult(
                name=name.split(b"\0", 1)[0].decode(errors="replace"),
                status=STATUS.get(status, f"unknown ({status})"),
                cycles=cycles,
                time_us=round(cycles / clock_hz * 1e6, 3) if clock_hz else None,
                fail_line=line if status == 2 else None,
            )
        )
    result = {
        "state": STATES.get(state, f"unknown ({state})"),
        "passed": passed,
        "failed": failed,
        "clock_hz": clock_hz,
        "tests": [asdict(t) for t in tests],
    }
    if state == 3:
        result["crashed_in"] = tests[current].name if current < len(tests) else f"test #{current}"
    return result


def run_tests(
    probe: GuardedProbe,
    image: Image,
    meter: PowerMeter | None = None,
    timeout_s: float = 10.0,
    quiet_s: float = 0.5,
) -> dict:
    if "bp_results" not in image.symbols:
        raise SiligentError(
            "this firmware has no 'bp_results' symbol; build it with the Siligent test harness (firmware/)"
        )
    mailbox = image.symbols["bp_results"][0]

    flashed = probe.flash(image)
    probe.reset(halt=True)
    probe.write_memory(mailbox, bytes(HEADER.size))  # clear any old results
    if meter:
        meter.start()
    started = time.monotonic()
    probe.resume()

    if meter:
        time.sleep(quiet_s)
    state = "timeout"
    while time.monotonic() - started < timeout_s:
        header = HEADER.unpack(probe.read_memory(mailbox, HEADER.size))
        if header[0] == MAGIC and header[2] in (2, 3):
            state = STATES[header[2]]
            break
        time.sleep(0.05)
    elapsed = time.monotonic() - started
    reading = meter.stop() if meter else None

    # Read the final results with the core halted, so the read can be trusted
    # whatever state the firmware left the chip in.
    probe.halt()
    result = parse_mailbox(probe.read_memory(mailbox, MAILBOX_SIZE))
    probe.resume()
    if state == "timeout" and result.get("state") in ("done", "crashed"):
        state = result["state"]  # finished, but polling could not see it
    if state == "timeout":
        result["state"] = "timeout"
        result["detail"] = f"tests did not finish within {timeout_s} s"
    result["ok"] = result["state"] == "done" and result.get("failed", 1) == 0
    result["wall_time_s"] = round(elapsed, 3)
    result["image_sha256"] = flashed["sha256"]
    virtual = not getattr(probe.backend, "persistent", True)
    if reading:
        result["energy"] = reading.to_dict()
    elif virtual:
        result["energy"] = {"measured": False, "detail": "virtual board: energy is not simulated yet"}
    else:
        result["energy"] = {"measured": False, "detail": "no power meter configured in bench.toml"}
    if virtual:
        result["virtual"] = True
        result["note"] = "simulated in Renode: cycle counts come from simulated time, not the real chip"
    probe.log.record(
        "run_tests",
        result["state"],
        passed=result.get("passed"),
        failed=result.get("failed"),
        sha256=flashed["sha256"],
        energy_uj=reading.energy_uj if reading else None,
    )
    return result
