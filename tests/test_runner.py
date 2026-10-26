import struct

import pytest

from siligent.errors import SiligentError
from siligent.image import load_image
from siligent.meters.base import integrate
from siligent.runner import ENTRY, HEADER, MAGIC, parse_mailbox, run_tests

from .conftest import FIRMWARE, good_image


def mailbox(state=2, tests=(("crc32", 1, 4000, 0), ("bad", 2, 10, 42)), clock=4_000_000, current=0):
    passed = sum(1 for t in tests if t[1] == 1)
    failed = sum(1 for t in tests if t[1] == 2)
    data = HEADER.pack(MAGIC, 1, state, len(tests), passed, failed, clock, current)
    for name, status, cycles, line in tests:
        data += ENTRY.pack(name.encode(), status, cycles, line)
    return data


def test_parse_results():
    r = parse_mailbox(mailbox())
    assert r["state"] == "done" and r["passed"] == 1 and r["failed"] == 1
    assert r["tests"][0] == {"name": "crc32", "status": "pass", "cycles": 4000, "time_us": 1000.0, "fail_line": None}
    assert r["tests"][1]["fail_line"] == 42


def test_parse_crash_names_the_test():
    r = parse_mailbox(mailbox(state=3, tests=(("ok", 1, 5, 0), ("boom", 3, 0, 0)), current=1))
    assert r["state"] == "crashed" and r["crashed_in"] == "boom"


def test_parse_empty_mailbox():
    assert parse_mailbox(bytes(64))["state"] == "no results"


def test_run_requires_harness_symbol(probe):
    with pytest.raises(SiligentError):
        run_tests(probe, good_image())


def test_run_end_to_end_with_fake_board(probe, backend):
    img = good_image()
    img.symbols["bp_results"] = (0x20000000, 0x420)

    def firmware_runs(b):
        b.write_memory(0x20000000, mailbox())

    backend.on_resume = firmware_runs
    r = run_tests(probe, img, timeout_s=1)
    assert r["state"] == "done" and r["ok"] is False  # one test failed
    assert backend.resets == [True]
    # The old mailbox header was cleared before the firmware started.
    assert backend.writes[0] == (0x20000000, bytes(HEADER.size))
    assert r["energy"]["measured"] is False


def test_run_times_out(probe, backend):
    img = good_image()
    img.symbols["bp_results"] = (0x20000000, 0x420)
    r = run_tests(probe, img, timeout_s=0.2)
    assert r["state"] == "timeout" and r["ok"] is False


@pytest.mark.skipif(not (FIRMWARE / "tests.elf").exists(), reason="firmware not built (make -C firmware/l476)")
def test_built_firmware_passes_gate_and_has_mailbox(gate):
    img = load_image(FIRMWARE / "tests.elf")
    gate.check_image(img)
    assert img.symbols["bp_results"][0] == 0x20000000


@pytest.mark.skipif(not (FIRMWARE / "demo_refused.elf").exists(), reason="firmware not built")
def test_built_refusal_demo_is_refused(gate):
    from siligent.errors import SafetyRefusal

    with pytest.raises(SafetyRefusal) as e:
        gate.check_image(load_image(FIRMWARE / "demo_refused.elf"))
    assert e.value.rule == "image.forbidden_word"


def test_energy_integration():
    # 10 mA at 3.3 V for 1 s = 33 mJ = 33000 uJ
    samples = [(t / 10, 0.010, 3.3) for t in range(11)]
    r = integrate("test", 1e-4, samples)
    assert r.energy_uj == pytest.approx(33000)
    assert r.to_dict()["below_resolution"] is False


def test_energy_flags_readings_below_resolution():
    samples = [(0.0, 2e-6, 3.3), (1.0, 2e-6, 3.3)]
    assert integrate("INA219", 1e-4, samples).to_dict()["below_resolution"] is True


class _InDebuggerMeter:
    """Stands in for EnergyTrace: no polling during the window."""

    name = "fake built-in meter"
    resolution_a = 1e-7
    no_polling = True
    window_s = 0.05

    def __init__(self, backend, finish_after_window: bool):
        self.backend = backend
        self.finish_after_window = finish_after_window
        self.records = []

    def start(self):
        pass

    def stop(self):
        from siligent.meters.base import MeterReading

        if self.finish_after_window:
            # The firmware finishes only after the energy window has closed.
            self.backend.on_resume = lambda b: b.write_memory(0x20000000, mailbox(tests=(("slow", 1, 9, 0),)))
        return MeterReading(self.name, 0.05, 0, 1.0, 1e-4, 1e-4, 1e-4, 3.3, 1e-7)


def test_run_longer_than_energy_window_falls_back_to_polling(probe, backend):
    img = good_image()
    img.symbols["bp_results"] = (0x20000000, 0x420)
    r = run_tests(probe, img, meter=_InDebuggerMeter(backend, finish_after_window=True), timeout_s=2)
    assert r["state"] == "done" and r["passed"] == 1
    assert r["energy"]["measured"] is False and "window" in r["energy"]["detail"]


def test_run_without_polling_reads_results_once(probe, backend):
    img = good_image()
    img.symbols["bp_results"] = (0x20000000, 0x420)
    backend.on_resume = lambda b: b.write_memory(0x20000000, mailbox(tests=(("quick", 1, 9, 0),)))
    r = run_tests(probe, img, meter=_InDebuggerMeter(backend, finish_after_window=False), timeout_s=2)
    assert r["state"] == "done" and r["ok"] is True
