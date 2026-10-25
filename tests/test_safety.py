import struct

import pytest

from siligent.errors import SafetyRefusal
from siligent.image import Image, Segment
from siligent.safety import BenchState, SafetyGate

from .conftest import good_image


def refused(fn, *args):
    with pytest.raises(SafetyRefusal) as e:
        fn(*args)
    return e.value.rule


# Host writes ---------------------------------------------------------------

def test_ram_and_peripheral_writes_allowed(gate):
    gate.check_host_write(0x20000000, b"\x01\x02\x03\x04")
    gate.check_host_write(0x10000000, b"\x00")
    gate.check_host_write(0x40021000, b"\x00\x00\x00\x00")  # RCC


def test_flash_registers_refused(gate):
    assert refused(gate.check_host_write, 0x40022020, b"\xaa\x00\x00\x00") == "write.forbidden_region"  # OPTR
    assert refused(gate.check_host_write, 0x4002200C, b"\x3b\x2a\x19\x08") == "write.forbidden_region"  # OPTKEYR


def test_write_straddling_into_flash_registers_refused(gate):
    assert refused(gate.check_host_write, 0x40021FFC, bytes(8)) == "write.forbidden_region"


def test_option_bytes_and_otp_refused(gate):
    assert refused(gate.check_host_write, 0x1FFF7800, b"\x00") == "write.forbidden_region"
    assert refused(gate.check_host_write, 0x1FFF7000, b"\x00") == "write.forbidden_region"  # OTP


def test_direct_flash_write_refused(gate):
    assert refused(gate.check_host_write, 0x08000000, b"\x00") == "write.outside_allowed"


def test_write_running_past_end_of_ram_refused(gate):
    assert refused(gate.check_host_write, 0x20017FFE, b"\x00" * 4) == "write.outside_allowed"


# Protected pins ---------------------------------------------------------------

GPIOA_MODER = 0x48000000
RESET_MODER_A = 0xABFFFFFF  # PA13, PA14, PA15 in alternate-function mode


def test_moder_write_keeping_swd_allowed(gate):
    value = RESET_MODER_A & ~0b11  # change PA0 only
    gate.check_host_write(GPIOA_MODER, value.to_bytes(4, "little"))


def test_moder_write_turning_swdio_into_output_refused(gate):
    value = (RESET_MODER_A & ~(0b11 << 26)) | (0b01 << 26)  # PA13 -> output
    assert refused(gate.check_host_write, GPIOA_MODER, value.to_bytes(4, "little")) == "pin.protected"


def test_partial_moder_write_refused(gate):
    assert refused(gate.check_host_write, GPIOA_MODER + 3, b"\x00") == "pin.partial_moder_write"


def test_other_port_moder_not_affected(gate):
    gate.check_host_write(0x48000400, bytes(4))  # GPIOB


# Images -------------------------------------------------------------------------

def test_good_image_passes(gate):
    gate.check_image(good_image())


def test_image_outside_flash_refused(gate):
    img = Image("x", [*good_image().segments, Segment(0x20000000, b"\x00" * 4)])
    assert refused(gate.check_image, img) == "image.outside_flash"


def test_image_past_end_of_flash_refused(gate):
    img = Image("x", [*good_image().segments, Segment(0x080FFFFC, b"\x00" * 8)])
    assert refused(gate.check_image, img) == "image.outside_flash"


def test_image_with_option_key_refused(gate):
    img = good_image(extra=struct.pack("<I", 0x08192A3B))
    assert refused(gate.check_image, img) == "image.forbidden_word"


def test_image_without_vector_table_refused(gate):
    img = Image("x", [Segment(0x08001000, b"\x00" * 16)])
    assert refused(gate.check_image, img) == "image.no_vector_table"


def test_bad_stack_pointer_refused(gate):
    img = Image("x", [Segment(0x08000000, struct.pack("<II", 0x08000000, 0x08000045))])
    assert refused(gate.check_image, img) == "image.bad_stack_pointer"


def test_even_reset_vector_refused(gate):
    img = Image("x", [Segment(0x08000000, struct.pack("<II", 0x20018000, 0x08000044))])
    assert refused(gate.check_image, img) == "image.bad_reset_vector"


# Bench and chip state --------------------------------------------------------------

def test_flash_refused_when_not_confirmed(rules):
    gate = SafetyGate(rules, BenchState(flash_allowed=None))
    assert refused(gate.check_bench_for_flash) == "bench.flash_not_allowed"


def test_flash_refused_when_set_false(rules):
    gate = SafetyGate(rules, BenchState(flash_allowed=False))
    assert refused(gate.check_bench_for_flash) == "bench.flash_not_allowed"


def test_flash_refused_for_truthy_non_boolean(rules):
    # "yes" or 1 in bench.toml must not count: only a literal true allows flashing.
    gate = SafetyGate(rules, BenchState(flash_allowed="yes"))
    assert refused(gate.check_bench_for_flash) == "bench.flash_not_allowed"


def test_old_keysight_setting_does_not_allow_flashing(tmp_path):
    from siligent.bench import BenchConfig

    p = tmp_path / "bench.toml"
    p.write_text("[bench]\nkeysight_attached = false\n")
    assert BenchConfig.load(p).flash_allowed is None


def test_read_protection_level0_allowed(gate):
    gate.check_read_protection(0xFFEFF8AA)


def test_unreadable_option_register_refused(gate):
    assert refused(gate.check_read_protection, 0) == "chip.unreadable"


def test_read_protection_level1_refused(gate):
    assert refused(gate.check_read_protection, 0xFFEFF800) == "chip.read_protection"
