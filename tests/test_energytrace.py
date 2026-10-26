"""EnergyTrace decoding and finding the tests in a recording. No hardware needed."""

import struct

from siligent.probes.msp430_backend import decode_energytrace
from siligent.runner import find_test_burst

V = 3.3


def trace(phases, step_s=0.0002):
    """Records (t, I, V, cumulative E) for phases of (duration_s, current_a)."""
    out, t, e = [], 0.0, 0.0
    for duration, current in phases:
        end = t + duration
        while t < end:
            out.append((t, current, V, e))
            t += step_s
            e += current * V * step_s
    return out


# The shape measured on an MSP-EXP430FR5994: warm-up, LPM0 wait, tests, LPM4.
REAL_SHAPE = [(0.14, 265e-6), (0.30, 150e-6), (0.017, 205e-6), (1.5, 0.0)]


def test_finds_the_tests_and_their_energy():
    r = find_test_burst(trace(REAL_SHAPE), cpu_s=0.0173)
    assert r is not None
    assert 15 <= r["duration_ms"] <= 20
    expected_uj = 205e-6 * V * 0.017 * 1e6  # about 11.5 uJ
    assert abs(r["energy_uj"] - expected_uj) / expected_uj < 0.15
    assert 140 <= r["current_before_tests_ua"] <= 160
    assert r["sleep_current_after_ua"] < 1


def test_ignores_the_debugger_taking_the_chip_back():
    shape = REAL_SHAPE + [(0.15, 560e-6)]  # re-attach at the end of the window
    records = trace(shape)
    cut = sum(d for d, _ in REAL_SHAPE)
    r = find_test_burst(records, until_s=cut - 0.05, cpu_s=0.0173)
    assert r is not None and r["duration_ms"] < 25


def test_no_claim_when_the_tests_do_not_stand_out():
    flat = [(0.2, 250e-6), (0.3, 250e-6), (0.017, 250e-6), (1.0, 0.0)]
    assert find_test_burst(trace(flat), cpu_s=0.0173) is None


def test_no_claim_without_sleep_after():
    busy = [(0.2, 265e-6), (0.3, 150e-6), (1.0, 205e-6)]
    assert find_test_burst(trace(busy), cpu_s=0.0173) is None


def test_decodes_both_record_kinds():
    analog = bytes([8]) + (1500).to_bytes(7, "little") + struct.pack("<IHI", 205000, 3290, 42)
    state = bytes([7]) + (1700).to_bytes(7, "little") + struct.pack("<QIHI", 0, 150000, 3289, 50)
    recs = decode_energytrace(analog + state)
    assert recs[0] == (0.0015, 205e-6, 3.29, 42e-7)
    assert recs[1][1] == 150e-6 and recs[1][3] == 50e-7


def test_stops_at_unknown_records():
    good = bytes([8]) + bytes(7) + struct.pack("<IHI", 1, 2, 3)
    assert len(decode_energytrace(good + bytes([99]) + bytes(17))) == 1
