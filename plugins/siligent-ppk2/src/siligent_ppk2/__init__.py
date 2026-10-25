"""Nordic Power Profiler Kit II as a Siligent power meter."""

from __future__ import annotations

import threading
import time

from siligent.errors import HardwareError
from siligent.meters.base import MeterReading

SAMPLE_HZ = 100_000  # the PPK2 streams 100k samples per second
# Lowest-range step, approximately. Nordic rates the PPK2 from about 200 nA to 1 A.
RESOLUTION_A = 1e-7


class PPK2Meter:
    """Samples a PPK2 in the background. `api` is a ppk2_api.PPK2_API (or a stand-in for tests)."""

    name = "PPK2"
    resolution_a = RESOLUTION_A

    def __init__(self, api, mode: str = "ampere", voltage_mv: int = 3300) -> None:
        if mode not in ("ampere", "source"):
            raise HardwareError(f"PPK2 mode must be 'ampere' or 'source', not '{mode}'")
        self.api = api
        self.mode = mode
        self.voltage_v = voltage_mv / 1000
        self.api.get_modifiers()  # loads the unit's calibration
        if mode == "source":
            self.api.use_source_meter()
        else:
            self.api.use_ampere_meter()
        # In ampere mode this is only the reference voltage; nothing is driven.
        self.api.set_source_voltage(voltage_mv)
        if mode == "source":
            self.api.toggle_DUT_power("ON")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._reset_totals()

    def _reset_totals(self) -> None:
        self._count = 0
        self._sum_ua = 0.0
        self._min_ua = float("inf")
        self._max_ua = float("-inf")
        self._error: Exception | None = None

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                data = self.api.get_data()
                if data:
                    samples, _digital = self.api.get_samples(data)
                    for ua in samples:  # running totals: a long window stays small in memory
                        self._count += 1
                        self._sum_ua += ua
                        self._min_ua = min(self._min_ua, ua)
                        self._max_ua = max(self._max_ua, ua)
                time.sleep(0.005)
        except Exception as e:  # report it from stop(), not from a background thread
            self._error = e

    def start(self) -> None:
        self._reset_totals()
        self._stop.clear()
        self.api.start_measuring()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> MeterReading:
        self._stop.set()
        if self._thread:
            self._thread.join()
        self.api.stop_measuring()
        if self._error:
            raise HardwareError(f"PPK2 stopped sending data: {self._error}") from self._error
        n = self._count
        if n == 0:
            return MeterReading(self.name, 0.0, 0, 0.0, 0.0, 0.0, 0.0, self.voltage_v, self.resolution_a)
        duration = n / SAMPLE_HZ  # timed by sample count, not the host clock
        avg_a = self._sum_ua / n * 1e-6
        return MeterReading(
            meter=self.name,
            duration_s=duration,
            samples=n,
            energy_uj=avg_a * self.voltage_v * duration * 1e6,
            avg_current_a=avg_a,
            min_current_a=self._min_ua * 1e-6,
            max_current_a=self._max_ua * 1e-6,
            avg_voltage_v=self.voltage_v,
            resolution_a=self.resolution_a,
        )


def make_meter(cfg: dict) -> PPK2Meter:
    """Entry point Siligent calls with the [meter] table from bench.toml."""
    from ppk2_api.ppk2_api import PPK2_API

    port = cfg.get("port")
    if not port:
        ports = PPK2_API.list_devices()
        if not ports:
            raise HardwareError("no PPK2 found on USB")
        port = ports[0]
    api = PPK2_API(port, timeout=1, write_timeout=1, exclusive=True)
    return PPK2Meter(api, mode=cfg.get("mode", "ampere"), voltage_mv=int(cfg.get("voltage_mv", 3300)))
