"""The patch for Renode's MSP430X multi-bit rotates, checked against TI's manual.
Runs the hook code with stand-ins for Renode's objects, so no Renode is needed."""

import sys
import types

import pytest

from siligent.image import Segment
from siligent.probes.renode_backend import MSP430X_ROTATE_FIX, msp430x_rotate_addresses

C, Z, N, V = 1, 2, 4, 0x100


class Reg:
    def __init__(self, v):
        self.RawValue = v

    @staticmethod
    def Create(v, bits):
        return Reg(v & ((1 << bits) - 1))


class FakeCPU:
    def __init__(self, words, regs, sr):
        self.mem = {0x4400 + 2 * i: w for i, w in enumerate(words)}
        self.regs = {**regs, 2: sr}
        self.PC = Reg(0x4400)
        self.Bus = self

    def ReadWord(self, address):
        return self.mem.get(address, 0x4303)  # NOP

    def GetRegister(self, n):
        return Reg(self.regs.get(n, 0))

    def SetRegister(self, n, value):
        self.regs[n] = value.RawValue


@pytest.fixture(autouse=True)
def renode_module(monkeypatch):
    mod = types.ModuleType("Antmicro.Renode.Peripherals.CPU")
    mod.RegisterValue = Reg
    for name in ("Antmicro", "Antmicro.Renode", "Antmicro.Renode.Peripherals"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "Antmicro.Renode.Peripherals.CPU", mod)


def run(words, regs, sr=0):
    cpu = FakeCPU(words, regs, sr)
    exec(MSP430X_ROTATE_FIX, {"self": cpu})
    return cpu


@pytest.mark.parametrize(
    "name, word, value, carry_in, result, flags",
    [
        ("RRCM.W #1: bit 0 goes to carry", 0x005E, 0x0001, 0, 0x0000, C | Z),
        ("RRCM.W #1: carry goes to bit 15", 0x005E, 0x0000, 1, 0x8000, N),
        ("RRCM.W #2", 0x045E, 0x0006, 1, 0x4001, C),
        ("RRCM.A #1: carry goes to bit 19", 0x004E, 0x00000, 1, 0x80000, N),
        ("RLAM.W #1 drops bit 16", 0x025E, 0x8000, 0, 0x0000, C | Z),
        ("RLAM.W #4", 0x0E5E, 0x1234, 0, 0x2340, C),
        ("RLAM.A #1 keeps 20 bits", 0x024E, 0x48000, 0, 0x90000, N),
        ("RRAM.W #2 keeps the sign", 0x055E, 0x8004, 0, 0xE001, N),
        ("RRUM.W #1 shifts in zero", 0x035E, 0x8001, 1, 0x4000, C),
    ],
)
def test_rotates_match_the_manual(name, word, value, carry_in, result, flags):
    cpu = run([word], {14: value}, sr=carry_in | V)
    assert cpu.regs[14] == result, name
    assert cpu.regs[2] & (C | Z | N | V) == flags, name  # V is always cleared
    assert cpu.PC.RawValue == 0x4402


def test_back_to_back_rotates_are_all_patched():
    # RRCM.W #1,R9 then RRCM.W #1,R8: the carry from R9 moves into R8.
    cpu = run([0x0059, 0x0058], {9: 0x0001, 8: 0x0000})
    assert cpu.regs[9] == 0 and cpu.regs[8] == 0x8000
    assert cpu.PC.RawValue == 0x4404


def test_other_instructions_are_left_alone():
    cpu = run([0x4303], {14: 5})
    assert cpu.PC.RawValue == 0x4400 and cpu.regs[14] == 5


def test_finds_rotates_on_even_addresses_only():
    seg = Segment(0x4400, bytes.fromhex("5e00" "0343" "5a02") + b"\x00\x5e\x00")
    assert msp430x_rotate_addresses([seg]) == [0x4400, 0x4404]
