import pytest

from siligent.detect import scan_usb
from siligent.meters.ina219 import INA219, REG_BUS, REG_CONFIG, REG_SHUNT
from siligent.rules import builtin_boards, load_rules, rules_for_dev_id


class FakeI2C:
    def __init__(self, regs):
        self.regs = regs
        self.written = {}

    def read_word(self, address, register):
        return self.regs[register]

    def write_word(self, address, register, value):
        self.written[register] = value


def test_ina219_converts_registers():
    # 1000 LSB of shunt = 10 mV over 0.1 ohm = 100 mA; bus 3.3 V = 825 LSB << 3
    bus = FakeI2C({REG_SHUNT: 1000, REG_BUS: 825 << 3})
    m = INA219(bus, shunt_ohms=0.1)
    current, volts = m.read_once()
    assert current == pytest.approx(0.1)
    assert volts == pytest.approx(3.3)
    assert REG_CONFIG in bus.written
    assert m.resolution_a == pytest.approx(1e-4)  # why it cannot see sleep current


def test_ina219_negative_current():
    m = INA219(FakeI2C({REG_SHUNT: 0x10000 - 100, REG_BUS: 0}), shunt_ohms=0.1)
    assert m.read_once()[0] == pytest.approx(-0.01)


def make_dev(root, name, vid, pid, serial="S1", product="P"):
    d = root / name
    d.mkdir()
    (d / "idVendor").write_text(vid + "\n")
    (d / "idProduct").write_text(pid + "\n")
    (d / "serial").write_text(serial)
    (d / "product").write_text(product)


def test_scan_usb_finds_known_probes(tmp_path):
    make_dev(tmp_path, "1-1", "0483", "374b", "066EFF")
    make_dev(tmp_path, "1-2", "1366", "0105")
    make_dev(tmp_path, "1-3", "046d", "c52b")  # a mouse
    found = scan_usb(tmp_path)
    assert [p.kind for p in found] == ["ST-LINK/V2-1", "SEGGER J-Link"]
    assert found[0].supported and not found[1].supported


def test_rules_load_and_match_chip():
    assert "stm32l476rg" in builtin_boards()
    r = load_rules("stm32l476rg")
    assert r.dev_id == 0x415 and r.flash.size == 0x100000
    assert rules_for_dev_id(0x415).name == "NUCLEO-L476RG"
    assert rules_for_dev_id(0x433) is None  # F401: no rules yet, so it stays read-only
    assert r.summary()["flash_requires"].startswith("read protection level 0")
