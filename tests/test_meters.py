import pytest

from siligent.bench import make_meter
from siligent.errors import SiligentError, SafetyRefusal


def test_no_meter():
    assert make_meter({"type": "none"}, None) is None


def test_source_voltage_above_chip_limit_refused(gate):
    with pytest.raises(SafetyRefusal) as e:
        make_meter({"type": "ppk2", "mode": "source", "voltage_mv": 5000}, gate)
    assert e.value.rule == "power.voltage_out_of_range"


def test_source_voltage_below_chip_limit_refused(gate):
    with pytest.raises(SafetyRefusal):
        make_meter({"type": "ppk2", "mode": "source", "voltage_mv": 1200}, gate)


def test_source_mode_needs_board_rules():
    with pytest.raises(SiligentError):
        make_meter({"type": "ppk2", "mode": "source", "voltage_mv": 3300}, None)


def test_source_mode_needs_a_voltage(gate):
    with pytest.raises(SiligentError):
        make_meter({"type": "ppk2", "mode": "source"}, gate)


def test_unknown_meter_lists_what_is_available():
    with pytest.raises(SiligentError) as e:
        make_meter({"type": "nope"}, None)
    assert "ina219" in str(e.value)
