import pytest

from siligent.errors import SafetyRefusal

from .conftest import FakeBackend, good_image


def test_refused_write_never_reaches_probe(probe, backend):
    with pytest.raises(SafetyRefusal):
        probe.write_memory(0x40022020, b"\x00\x00\x00\x00")
    assert backend.writes == []
    assert probe.log.tail(1)[0]["outcome"] == "refused"


def test_allowed_write_reaches_probe_and_is_logged(probe, backend):
    probe.write_memory(0x20000000, b"\x01\x02")
    assert backend.writes == [(0x20000000, b"\x01\x02")]
    last = probe.log.tail(1)[0]
    assert (last["action"], last["outcome"]) == ("write_memory", "ok")


def test_flash_snapshots_then_programs(probe, backend, tmp_path):
    result = probe.flash(good_image())
    assert result["ok"]
    assert backend.programmed
    assert (tmp_path / "snaps" / "FAKE123" / "baseline-full-flash.bin").stat().st_size == 0x100000
    assert list((tmp_path / "snaps" / "FAKE123").glob("pages-*.bin"))


def test_flash_refused_on_wrong_chip(rules, gate, tmp_path):
    from siligent.log import ActionLog
    from siligent.probes.guarded import GuardedProbe

    backend = FakeBackend(dev_id=0x433)  # an F401, not an L476
    p = GuardedProbe(backend, rules, gate, ActionLog(tmp_path / "l.jsonl"), tmp_path / "s")
    with pytest.raises(SafetyRefusal) as e:
        p.flash(good_image())
    assert e.value.rule == "chip.mismatch"
    assert backend.programmed == []


def _probe(tmp_path, backend):
    from siligent.log import ActionLog
    from siligent.probes.guarded import GuardedProbe
    from siligent.rules import load_rules
    from siligent.safety import BenchState, SafetyGate

    rules = load_rules("stm32l476rg")
    return GuardedProbe(
        backend, rules, SafetyGate(rules, BenchState(flash_allowed=True)), ActionLog(tmp_path / "l.jsonl"), tmp_path / "s"
    )


def test_flash_halts_before_reading_protection_on_sleeping_core(tmp_path):
    # Real-board lesson: a sleeping core reads as zeros, so the protection check
    # must happen after a halt, or it sees RDP = 0.
    backend = FakeBackend(sleeping=True)
    assert _probe(tmp_path, backend).flash(good_image())["ok"]
    assert backend.events.index("halt") < backend.events.index("read32 0x40022020")


def test_refused_flash_resumes_the_core(tmp_path):
    backend = FakeBackend(dev_id=0x433)
    with pytest.raises(SafetyRefusal):
        _probe(tmp_path, backend).flash(good_image())
    assert backend.events[-1] == "resume" and backend.core == "running"


def test_read_on_sleeping_core_halts_then_resumes(tmp_path):
    backend = FakeBackend(sleeping=True)
    backend.mem[0x20000000] = 0x42
    data, halted = _probe(tmp_path, backend).read_memory_checked(0x20000000, 1)
    assert data == b"\x42" and halted
    assert backend.events[-2:] == ["halt", "resume"]


def test_flash_refused_on_read_protected_chip(rules, gate, tmp_path):
    from siligent.log import ActionLog
    from siligent.probes.guarded import GuardedProbe

    backend = FakeBackend(optr=0xFFEFF8BB)  # RDP level 1
    p = GuardedProbe(backend, rules, gate, ActionLog(tmp_path / "l.jsonl"), tmp_path / "s")
    with pytest.raises(SafetyRefusal) as e:
        p.flash(good_image())
    assert e.value.rule == "chip.read_protection"
    assert backend.programmed == []
    assert not (tmp_path / "s").exists()  # refused before any snapshot or read of flash
