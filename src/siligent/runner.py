"""Run on-device tests and collect pass/fail, cycles and energy.

The firmware (see firmware/) writes its results to a RAM "mailbox" at the
symbol `bp_results`. The host never talks to the chip while it measures
energy: debug traffic adds current. With an external meter it waits a quiet
period first and only then polls the mailbox. With a meter built into the
debugger (TI EnergyTrace) it does not poll at all: it lets the run go for a
fixed window, then halts and reads the results once.
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


def _per_ms(records, until_s):
    """Resample the cumulative energy counter to 1 ms steps: [(t_s, energy_j, voltage_v)]."""
    recs = [r for r in records if until_s is None or r[0] < until_s]
    if len(recs) < 2:
        return []
    out, k = [], 0
    t = recs[0][0]
    while t <= recs[-1][0]:
        while k + 1 < len(recs) and recs[k + 1][0] <= t:
            k += 1
        out.append((t, recs[k][3], recs[k][2]))
        t += 0.001
    return out


def find_test_burst(records, until_s: float | None = None, cpu_s: float | None = None) -> dict | None:
    """Find the tests in an EnergyTrace recording and measure them.

    The MSP430 harness waits in LPM0 before the tests and sleeps in LPM4 after
    them, so the tests are the last stretch of activity, a step up from the
    wait. Works on the debugger's cumulative energy counter, not its smoothed
    current readings. Returns None when there is no clear stretch.
    Records are (time_s, current_a, voltage_v, cumulative_energy_j); records
    after until_s (the debugger taking the chip back) are ignored.
    """
    steps = _per_ms(records, until_s)
    if len(steps) < 30:
        return None
    raw = [(steps[i + 1][1] - steps[i][1]) / 0.001 / max(steps[i][2], 0.1) for i in range(len(steps) - 1)]
    cur = [sum(raw[max(0, i - 1) : i + 2]) / len(raw[max(0, i - 1) : i + 2]) for i in range(len(raw))]
    peak = max(cur)
    if peak <= 0:
        return None
    active = [i for i, c in enumerate(cur) if c > 0.2 * peak]
    end = active[-1]
    if end >= len(cur) - 10:
        return None  # still busy when the recording stopped: no sleep after the tests
    span = max(3, round((cpu_s or 0.01) * 1000))
    tests = sorted(cur[max(0, end - int(span * 0.6)) : end + 1])
    before = sorted(cur[max(0, end - span - 60) : max(0, end - span - 10)])
    if not tests or not before:
        return None
    level, base = tests[len(tests) // 2], before[len(before) // 2]
    if level - base < 0.15 * level:
        return None  # no step up from the wait: cannot tell where the tests began
    threshold = (level + base) / 2
    start = end
    while start > 1 and not (cur[start - 1] < threshold and cur[start - 2] < threshold):
        start -= 1
    e0, e1 = steps[start][1], steps[end + 1][1]
    duration = steps[end + 1][0] - steps[start][0]
    after = steps[end + 5 :]
    sleep_a = (
        (after[-1][1] - after[0][1]) / max(after[-1][0] - after[0][0], 1e-9) / max(after[0][2], 0.1)
        if len(after) > 50
        else None
    )
    v = sum(x[2] for x in steps[start : end + 2]) / (end + 2 - start)
    return {
        "measured": True,
        "meter": None,  # filled in by the caller
        "scope": "whole target rail of the board, while the tests ran",
        "energy_uj": round((e1 - e0) * 1e6, 2),
        "duration_ms": round(duration * 1e3, 1),
        "avg_current_ua": round((e1 - e0) / max(duration, 1e-9) / v * 1e6, 1),
        "current_before_tests_ua": round(base * 1e6, 1),
        "sleep_current_after_ua": round(sleep_a * 1e6, 2) if sleep_a is not None else None,
        "voltage_v": round(v, 3),
    }


def _run_without_polling(probe, mailbox, meter, timeout_s, window_s):
    """For meters inside the debugger: no debug traffic until the window ends."""
    window_s = getattr(meter, "window_s", window_s)
    meter.start()
    started = time.monotonic()
    run = getattr(meter, "run_target", None)
    run() if run else probe.resume()
    time.sleep(min(window_s, timeout_s))
    elapsed = time.monotonic() - started
    reading = meter.stop()  # halts the core
    result = parse_mailbox(probe.read_memory(mailbox, MAILBOX_SIZE))
    probe.resume()
    outlasted = result.get("state") not in ("done", "crashed")
    while outlasted and time.monotonic() - started < timeout_s:
        # Longer than the energy window: finish by polling, without energy for the tests.
        time.sleep(0.1)
        header = HEADER.unpack(probe.read_memory(mailbox, HEADER.size))
        if header[0] == MAGIC and header[2] in (2, 3):
            probe.halt()
            result = parse_mailbox(probe.read_memory(mailbox, MAILBOX_SIZE))
            probe.resume()
            break
    state = result.get("state") if result.get("state") in ("done", "crashed") else "timeout"
    if outlasted:
        reading = None
    return result, state, time.monotonic() - started if outlasted else elapsed, reading


def run_tests(
    probe: GuardedProbe,
    image: Image,
    meter: PowerMeter | None = None,
    timeout_s: float = 10.0,
    quiet_s: float = 0.5,
    energy_window_s: float = 1.0,
) -> dict:
    if "bp_results" not in image.symbols:
        raise SiligentError(
            "this firmware has no 'bp_results' symbol; build it with the Siligent test harness (firmware/)"
        )
    mailbox = image.symbols["bp_results"][0]

    flashed = probe.flash(image)
    probe.reset(halt=True)
    probe.write_memory(mailbox, bytes(HEADER.size))  # clear any old results

    outlasted_window = False
    if meter is not None and getattr(meter, "no_polling", False):
        result, state, elapsed, reading = _run_without_polling(probe, mailbox, meter, timeout_s, energy_window_s)
        outlasted_window = reading is None
    else:
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
    records = getattr(meter, "records", None) if meter is not None and not outlasted_window else None
    cpu_s = sum(t["cycles"] for t in result.get("tests", [])) / result["clock_hz"] if result.get("clock_hz") else 0
    phase = find_test_burst(records, until_s=elapsed - 0.05, cpu_s=cpu_s) if records else None
    if phase and not (cpu_s * 0.8 <= phase["duration_ms"] / 1e3 <= cpu_s * 1.5 + 0.005):
        # The burst does not line up with how long the tests took: do not claim it.
        phase = None
        burst_mismatch = True
    else:
        burst_mismatch = False
    if phase:
        phase["meter"] = reading.meter
        phase["test_cpu_time_ms"] = round(cpu_s * 1e3, 2)  # cross-check against duration_ms
        if cpu_s:
            # Shares of the measured burst by CPU time: an estimate, since tests
            # draw different currents. Measured per-test energy needs markers.
            for t in result["tests"]:
                t["energy_uj_estimate"] = round(phase["energy_uj"] * (t["cycles"] / result["clock_hz"]) / phase["duration_ms"] * 1e3, 3)
        phase["window"] = reading.to_dict()
        result["energy"] = phase
    elif reading:
        result["energy"] = reading.to_dict()
        if records is not None:
            result["energy"]["note"] = (
                "whole measurement window only: the tests could not be picked out of the recording"
                + (" (the burst found did not match the tests' CPU time)" if burst_mismatch else "")
            )
    elif outlasted_window:
        result["energy"] = {
            "measured": False,
            "detail": "the tests ran longer than the energy measurement window, so their energy was not measured",
        }
    elif virtual:
        result["energy"] = {"measured": False, "detail": "virtual board: energy is not simulated yet"}
    else:
        result["energy"] = {"measured": False, "detail": "no power meter configured in bench.toml"}
    if virtual:
        result["virtual"] = True
        result["note"] = "simulated in Renode: cycle counts come from simulated time, not the real chip"
        limits = getattr(probe.backend, "limits", None)
        if limits:
            result["note"] += f"; on this virtual board {limits}"
    probe.log.record(
        "run_tests",
        result["state"],
        passed=result.get("passed"),
        failed=result.get("failed"),
        sha256=flashed["sha256"],
        energy_uj=phase["energy_uj"] if phase else (reading.energy_uj if reading else None),
    )
    return result
