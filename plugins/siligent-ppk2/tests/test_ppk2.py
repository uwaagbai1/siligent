import time

import pytest

from siligent.errors import HardwareError
from siligent_ppk2 import SAMPLE_HZ, PPK2Meter


class FakePPK2:
    """Stands in for ppk2_api.PPK2_API: each get_data() yields one chunk of samples (in uA)."""

    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.calls = []

    def __getattr__(self, name):
        def record(*args):
            self.calls.append((name, args))

        return record

    def get_data(self):
        return b"x" if self.chunks else b""

    def get_samples(self, data):
        return self.chunks.pop(0), []


def run(meter, wait=0.2):
    meter.start()
    time.sleep(wait)
    return meter.stop()


def test_ampere_mode_setup_does_not_power_the_board():
    api = FakePPK2([])
    PPK2Meter(api, mode="ampere", voltage_mv=3300)
    names = [c[0] for c in api.calls]
    assert "use_ampere_meter" in names and ("set_source_voltage", (3300,)) in api.calls
    assert "toggle_DUT_power" not in names


def test_source_mode_powers_the_board():
    api = FakePPK2([])
    PPK2Meter(api, mode="source", voltage_mv=3000)
    assert ("toggle_DUT_power", ("ON",)) in api.calls


def test_energy_from_samples():
    # 100,000 samples of 1000 uA at 3.3 V = 1 s at 1 mA = 3.3 mJ = 3300 uJ
    api = FakePPK2([[1000.0] * 50_000, [1000.0] * 50_000])
    r = run(PPK2Meter(api, voltage_mv=3300))
    assert r.samples == SAMPLE_HZ
    assert r.duration_s == pytest.approx(1.0)
    assert r.energy_uj == pytest.approx(3300)
    assert r.avg_current_a == pytest.approx(1e-3)


def test_sleep_current_is_within_resolution():
    api = FakePPK2([[1.3] * 1000])  # 1.3 uA, a typical Stop 2 figure
    r = run(PPK2Meter(api))
    assert r.avg_current_a == pytest.approx(1.3e-6)
    assert r.to_dict()["below_resolution"] is False


def test_bad_mode_refused():
    with pytest.raises(HardwareError):
        PPK2Meter(FakePPK2([]), mode="turbo")
