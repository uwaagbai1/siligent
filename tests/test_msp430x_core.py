"""Siligent's MSP430X core for Renode (boards/renode/SiligentMSP430X.cs), checked
against TI's manual (SLAU367P): the rotates it fixes, and the cycle cost it
charges per instruction. Skipped without Renode."""

import os

import pytest

from siligent.errors import HardwareError
from siligent.probes.renode_backend import RenodeBackend, find_renode

try:
    find_renode()
    HAVE_RENODE = True
except HardwareError:
    HAVE_RENODE = False

if not HAVE_RENODE and os.environ.get("SILIGENT_REQUIRE_RENODE") == "1":
    raise RuntimeError("SILIGENT_REQUIRE_RENODE is set but Renode was not found")

pytestmark = pytest.mark.skipif(not HAVE_RENODE, reason="needs Renode")

C, Z, N, V = 1, 2, 4, 0x100
START = 0x4400
JMP_SELF = 0x3FFF


@pytest.fixture(scope="module")
def board():
    b = RenodeBackend(board="msp430fr5994", arch="msp430")
    b.open()
    yield b
    b.close()


def load(board, words):
    for i, w in enumerate(words):
        board.command(f"sysbus WriteWord {START + 2 * i:#x} {w:#x}")


def register(board, n):
    return int(board.command(f"cpu GetRegister {n}"), 16)


def run(board, words, regs, sr=0):
    """Run the words, then spin on a JMP $, for 100 cycles."""
    load(board, [*words, JMP_SELF])
    for n, value in {**regs, 2: sr}.items():
        board.command(f"cpu SetRegister {n} {value:#x}")
    board.command(f"cpu PC {START:#x}")
    board.command('emulation RunFor "0.0001"')


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
def test_rotates_match_the_manual(board, name, word, value, carry_in, result, flags):
    run(board, [word], {14: value}, sr=carry_in | V)
    assert register(board, 14) == result, name
    assert register(board, 2) & (C | Z | N | V) == flags, name  # V is always cleared
    assert int(board.command("cpu PC"), 16) == START + 2  # stopped on the JMP $


def test_back_to_back_rotates(board):
    # RRCM.W #1,R9 then RRCM.W #1,R8: the carry from R9 moves into R8.
    run(board, [0x0059, 0x0058], {9: 0x0001, 8: 0x0000})
    assert register(board, 9) == 0 and register(board, 8) == 0x8000


# Examples from TI's cycle tables (SLAU367P tables 4-8 to 4-19), hand-encoded.
@pytest.mark.parametrize(
    "name, words, cycles",
    [
        ("MOV R5,R8", [0x4508], 1),
        ("CLR R12 (MOV #0, constant generator)", [0x430C], 1),
        ("BR R9", [0x4900], 3),
        ("ADD R5,4(R6)", [0x5586, 0x0004], 4),
        ("MOV R5,&EDE (MOV to memory: one fewer)", [0x4582, 0x0200], 3),
        ("AND @R4,R5", [0xF425], 2),
        ("MOV #20,R9", [0x4039, 0x0014], 2),
        ("MOV #0300h,0(SP)", [0x40B1, 0x0300, 0x0000], 4),
        ("ADD #33,&EDE", [0x50B2, 0x0021, 0x0200], 5),
        ("MOV 2(R5),R7", [0x4517, 0x0002], 3),
        ("CMP EDE,TONI", [0x9090, 0x0010, 0x0020], 5),
        ("JMP $", [0x3FFF], 2),
        ("RETI", [0x1300], 5),
        ("SWPB R5", [0x1085], 1),
        ("PUSH R5", [0x1205], 3),
        ("CALL #LABEL", [0x12B0, 0x4400], 4),
        ("SXT &EDE", [0x1192, 0x0200], 4),
        ("CALLA #imm20", [0x13B0, 0x4400], 5),
        ("CALLA &abs20", [0x1380, 0x4400], 7),
        ("RETA", [0x0110], 5),
        ("MOVA #imm20,SP", [0x0081, 0x2C00], 2),
        ("MOVA R5,R8", [0x05C8], 1),
        ("MOVA @R5,R8", [0x0508], 3),
        ("PUSHM.A #2,R14", [0x141E], 6),
        ("POPM.W #3,R12", [0x172C], 5),
        ("RRCM.W #2,R14", [0x045E], 2),
        ("BITX.B R5,R8", [0x1840, 0xB548], 2),
        ("ANDX.A R5,4(R6)", [0x1800, 0xF5C6, 0x0004], 7),
        ("MOVX.A @R5,R8", [0x1800, 0x4568], 4),
        ("RPT #4 RRAX.A R5", [0x1803, 0x1145], 5),
    ],
)
def test_cycle_costs_match_the_manual(board, name, words, cycles):
    load(board, words)
    assert int(board.command(f"cpu CycleCost {START:#x}"), 16) == cycles, name


def test_time_follows_the_cycle_cost(board):
    # 100 us at 1 MHz is 100 cycles: 50 JMP $ at 2 cycles each.
    load(board, [JMP_SELF])
    board.command("machine Reset")  # zeroes Cycles
    board.command(f"cpu PC {START:#x}")
    before = int(board.command("cpu ExecutedInstructions"), 16)
    board.command('emulation RunFor "0.0001"')
    assert int(board.command("cpu Cycles"), 16) == 100
    assert int(board.command("cpu ExecutedInstructions"), 16) - before == 50
