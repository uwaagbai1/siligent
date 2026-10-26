"""End-to-end tests on the Renode virtual L476. Skipped when Renode or the firmware is missing."""

import os

import pytest

from siligent import api
from siligent.bench import BenchConfig
from siligent.errors import HardwareError
from siligent.probes.renode_backend import find_renode

from .conftest import FIRMWARE

try:
    find_renode()
    HAVE_RENODE = True
except HardwareError:
    HAVE_RENODE = False

READY = HAVE_RENODE and (FIRMWARE / "tests.elf").exists()
REASON = "needs Renode and built firmware (make -C firmware/l476)"

if not READY and os.environ.get("SILIGENT_REQUIRE_RENODE") == "1":
    raise RuntimeError(f"SILIGENT_REQUIRE_RENODE is set but the virtual board is not ready: {REASON}")

pytestmark = pytest.mark.skipif(not READY, reason=REASON)


@pytest.fixture
def cfg(tmp_path):
    return BenchConfig(virtual=True, log_path=tmp_path / "log.jsonl", snapshot_dir=tmp_path / "snaps")


def test_virtual_run_passes(cfg):
    r = api.run_tests(str(FIRMWARE / "tests.elf"), config=cfg)
    assert r["ok"] and r["virtual"] and r["passed"] == 4
    assert all(t["cycles"] > 0 for t in r["tests"])


def test_virtual_run_reports_failure_line(cfg):
    r = api.run_tests(str(FIRMWARE / "tests_fail.elf"), config=cfg)
    failed = [t for t in r["tests"] if t["status"] == "fail"]
    assert r["ok"] is False and [t["name"] for t in failed] == ["deliberate_failure"]
    assert failed[0]["fail_line"] > 0


def test_virtual_board_applies_the_same_rules(cfg):
    assert api.flash(str(FIRMWARE / "demo_refused.elf"), config=cfg)["rule"] == "image.forbidden_word"
    assert api.write_memory(0x40022020, "00000000", config=cfg)["rule"] == "write.forbidden_region"


# Virtual MSP430FR5994 ----------------------------------------------------------------
# Runs the firmware kept in git (firmware/msp430fr5994/prebuilt), so it needs no TI
# compiler. The board is picked from the image, not from bench.toml.

MSP430 = FIRMWARE.parent.parent / "msp430fr5994" / "prebuilt"
msp430 = pytest.mark.skipif(not HAVE_RENODE, reason="needs Renode")


@msp430
def test_virtual_msp430_runs_the_tests(cfg):
    r = api.run_tests(str(MSP430 / "tests.elf"), config=cfg)
    assert r["ok"] and r["virtual"] and r["passed"] == 4, r
    assert {t["name"] for t in r["tests"]} >= {"float_multiply"}  # needs the rotate fix
    assert all(t["cycles"] > 0 for t in r["tests"])


@msp430
def test_virtual_msp430_reports_failure_line(cfg):
    r = api.run_tests(str(MSP430 / "tests_fail.elf"), config=cfg)
    failed = [t for t in r["tests"] if t["status"] == "fail"]
    assert [t["name"] for t in failed] == ["deliberate_failure"] and failed[0]["fail_line"] > 0


@msp430
def test_virtual_msp430_refuses_the_debug_lock(cfg):
    r = api.flash(str(MSP430 / "demo_refused.elf"), config=cfg)
    assert r["rule"] == "image.forbidden_word"


@msp430
def test_virtual_msp430_blank_chip_reads_erased(cfg):
    from dataclasses import replace

    cfg = replace(cfg, board="msp430fr5994")
    assert api.read_memory(0xFF80, 4, config=cfg)["hex"] == "ffffffff"
    assert api.write_memory(0xFF80, "5555", config=cfg)["rule"] == "write.forbidden_region"

