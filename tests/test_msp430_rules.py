"""Safety rules for the MSP430FR5994: the chip whose debug port a single write can lock."""

import pytest

from siligent.errors import SafetyRefusal
from siligent.image import Image, Segment
from siligent.log import ActionLog
from siligent.probes.guarded import GuardedProbe
from siligent.rules import load_rules, rules_for_dev_id
from siligent.safety import BenchState, SafetyGate


@pytest.fixture
def rules():
    return load_rules("msp430fr5994")


@pytest.fixture
def gate(rules):
    return SafetyGate(rules, BenchState(flash_allowed=True))


def refused(fn, *args):
    with pytest.raises(SafetyRefusal) as e:
        fn(*args)
    return e.value.rule


def msp_image(signatures: bytes = b"\xff" * 16, reset: int = 0x4400, code: bytes = b"\x03\x43" * 8) -> Image:
    """Code at 0x4400, then 0xFF80-0xFFFF: signatures, unused vectors, reset vector."""
    vectors = signatures + b"\xff" * (0x7E - len(signatures)) + reset.to_bytes(2, "little")
    return Image("msp.elf", [Segment(0x4400, code), Segment(0xFF80, vectors)])


class FakeMSP430:
    unique_id = "FAKE-EZFET"
    description = "fake eZ-FET"

    def __init__(self, signatures: bytes = b"\xff" * 16, dev_id: int = 0x82A1):
        self.mem = {0x1A04 + i: b for i, b in enumerate(dev_id.to_bytes(2, "little") + b"\x21\x10")}
        self.mem.update({0xFF80 + i: b for i, b in enumerate(signatures)})
        self.programmed = []
        self.events = []

    def read32(self, address):
        return int.from_bytes(self.read_memory(address, 4), "little")

    def read_memory(self, address, length):
        return bytes(self.mem.get(address + i, 0xFF) for i in range(length))

    def write_memory(self, address, data):
        self.mem.update({address + i: b for i, b in enumerate(data)})

    def program(self, segments):
        self.programmed.extend(segments)

    def halt(self):
        self.events.append("halt")

    def resume(self):
        self.events.append("resume")

    def reset(self, halt=False): ...

    def state(self):
        return "running"


def make_probe(backend, rules, gate, tmp_path):
    return GuardedProbe(backend, rules, gate, ActionLog(tmp_path / "log.jsonl"), tmp_path / "snaps")


# Image checks ----------------------------------------------------------------------


def test_normal_image_passes(gate):
    gate.check_image(msp_image())


def test_image_that_locks_jtag_is_refused(gate):
    sig = b"\x55\x55\x55\x55" + b"\xff" * 12
    assert refused(gate.check_image, msp_image(signatures=sig)) == "image.protection_signature"


def test_image_that_enables_ip_encapsulation_is_refused(gate):
    sig = b"\xff" * 8 + b"\xaa\xaa\x00\x50" + b"\xff" * 4
    assert refused(gate.check_image, msp_image(signatures=sig)) == "image.protection_signature"


def test_image_without_reset_vector_is_refused(gate):
    img = Image("msp.elf", [Segment(0x4400, b"\x03\x43" * 8)])
    assert refused(gate.check_image, img) == "image.no_reset_vector"


@pytest.mark.parametrize("reset", [0x4401, 0x1C00, 0xFF90])
def test_bad_reset_vector_is_refused(gate, reset):
    assert refused(gate.check_image, msp_image(reset=reset)) == "image.bad_reset_vector"


def test_code_that_writes_the_jtag_lock_is_refused(gate):
    # mov.w #0x5555, &0xFF80
    code = b"\x03\x43" * 4 + b"\xb2\x40\x55\x55\x80\xff" + b"\x03\x43" * 4
    assert refused(gate.check_image, msp_image(code=code)) == "image.forbidden_word"


def test_image_outside_program_memory_is_refused(gate):
    img = msp_image()
    img.segments.append(Segment(0x1800, b"\x00" * 4))  # INFO memory
    assert refused(gate.check_image, img) == "image.outside_flash"


# Host writes -------------------------------------------------------------------------


def test_ram_and_peripheral_writes_allowed(gate):
    gate.check_host_write(0x1C00, b"\x01\x02")
    gate.check_host_write(0x2C00, b"\x00" * 4)
    gate.check_host_write(0x0202, b"\x01")  # P1OUT


@pytest.mark.parametrize(
    "address",
    [0xFF80, 0x4400, 0x10000, 0x1A00, 0x1000, 0x05A0, 0x0140],
    ids=["jtag-signature", "fram", "upper-fram", "tlv", "bsl", "mpu", "fram-controller"],
)
def test_dangerous_host_writes_refused(gate, address):
    assert refused(gate.check_host_write, address, b"\x55\x55") == "write.forbidden_region"


def test_info_memory_write_refused(gate):
    assert refused(gate.check_host_write, 0x1800, b"\x00") == "write.outside_allowed"


# Flash preconditions --------------------------------------------------------------


def test_flash_checks_chip_and_signatures_then_programs(rules, gate, tmp_path):
    backend = FakeMSP430()
    result = make_probe(backend, rules, gate, tmp_path).flash(msp_image())
    assert result["ok"] and backend.programmed
    baseline = tmp_path / "snaps" / "FAKE-EZFET" / "baseline-full-flash.bin"
    assert baseline.stat().st_size == rules.flash.size


def test_flash_refused_on_locked_chip(rules, gate, tmp_path):
    backend = FakeMSP430(signatures=b"\xff" * 8 + b"\x00\x00\x00\x00" + b"\xff" * 4)
    probe = make_probe(backend, rules, gate, tmp_path)
    assert refused(probe.flash, msp_image()) == "chip.protected"
    assert not backend.programmed and backend.events[-1] == "resume"


def test_flash_refused_when_signatures_read_as_zeros(rules, gate, tmp_path):
    probe = make_probe(FakeMSP430(signatures=bytes(16)), rules, gate, tmp_path)
    assert refused(probe.flash, msp_image()) == "chip.unreadable"


def test_flash_refused_on_wrong_chip(rules, gate, tmp_path):
    probe = make_probe(FakeMSP430(dev_id=0x8240), rules, gate, tmp_path)
    assert refused(probe.flash, msp_image()) == "chip.mismatch"


def test_rules_are_found_by_device_id_within_family():
    assert rules_for_dev_id(0x82A1, probe="msp430").chip == "MSP430FR5994"
    assert rules_for_dev_id(0x82A1, probe="pyocd") is None
    assert rules_for_dev_id(0x415, probe="pyocd").chip == "STM32L476RG"


def test_summary_explains_the_lock_bytes(rules):
    s = rules.summary()
    assert "0xff80" in s["flash_requires"]
    assert {r["name"] for r in s["firmware_bytes_that_must_stay_erased"]} == {
        "JTAG signature",
        "BSL signature",
        "IP-encapsulation signature",
    }


def test_restore_on_fram_writes_every_byte_and_verifies(rules, tmp_path, monkeypatch):
    """FRAM is not erased before writing, so a restore must not skip trailing 0xFF."""
    from siligent import api
    from siligent.bench import BenchConfig

    backend = FakeMSP430()
    backend.erases = False
    written = {}

    def program(segments):
        for s in segments:
            written[s.address] = s.data
            backend.mem.update({s.address + i: b for i, b in enumerate(s.data)})

    backend.program = program
    snapshot = tmp_path / "baseline.bin"
    snapshot.write_bytes(b"\x03\x43" * 8 + b"\xff" * (rules.flash.size - 16))
    reset_vec = 0xFFFE - rules.flash.start
    data = bytearray(snapshot.read_bytes())
    data[reset_vec : reset_vec + 2] = (0x4000).to_bytes(2, "little")
    snapshot.write_bytes(bytes(data))

    class FakeBench:
        def __init__(self, cfg):
            self.rules = rules
            self.probe = make_probe(backend, rules, SafetyGate(rules, BenchState(flash_allowed=True)), tmp_path)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

    monkeypatch.setattr(api, "Bench", FakeBench)
    result = api.restore(str(snapshot), config=BenchConfig(flash_allowed=True))
    assert result["ok"] and result["verified"]
    assert len(written[rules.flash.start]) == rules.flash.size
