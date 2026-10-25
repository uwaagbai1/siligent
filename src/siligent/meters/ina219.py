"""TI INA219 current/voltage sensor over I2C.

Good for active current. With a 0.1 ohm shunt it reads in 100 uA steps, so it
cannot see microcontroller sleep currents (around 1 uA). Use a uA-capable
meter for those.
"""

from __future__ import annotations

import threading
import time
from typing import Protocol

from .base import MeterReading, integrate

REG_CONFIG = 0x00
REG_SHUNT = 0x01
REG_BUS = 0x02
SHUNT_LSB_V = 10e-6
BUS_LSB_V = 4e-3
# 32 V range, PGA /8 (+-320 mV), 12-bit, shunt and bus continuous: the reset default.
CONFIG_DEFAULT = 0x399F


class I2CBus(Protocol):
    def read_word(self, address: int, register: int) -> int:
        """Read a 16-bit register, most significant byte first (as the INA219 sends it)."""

    def write_word(self, address: int, register: int, value: int) -> None:
        """Write a 16-bit register, most significant byte first."""


class LinuxI2CBus:
    """A Linux /dev/i2c-N bus via smbus2 (pip install siligent[ina219])."""

    def __init__(self, bus: int) -> None:
        from smbus2 import SMBus  # imported here so the dependency stays optional

        self._bus = SMBus(bus)

    def read_word(self, address: int, register: int) -> int:
        w = self._bus.read_word_data(address, register)
        return ((w & 0xFF) << 8) | (w >> 8)  # SMBus words arrive low byte first

    def write_word(self, address: int, register: int, value: int) -> None:
        self._bus.write_word_data(address, register, ((value & 0xFF) << 8) | (value >> 8))


class INA219:
    name = "INA219"

    def __init__(self, bus: I2CBus, address: int = 0x40, shunt_ohms: float = 0.1, sample_hz: float = 500) -> None:
        self.bus = bus
        self.address = address
        self.shunt_ohms = shunt_ohms
        self.period = 1.0 / sample_hz
        self.resolution_a = SHUNT_LSB_V / shunt_ohms
        self._samples: list[tuple[float, float, float]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.bus.write_word(self.address, REG_CONFIG, CONFIG_DEFAULT)

    def read_once(self) -> tuple[float, float]:
        """One (current_a, bus_voltage_v) reading."""
        raw = self.bus.read_word(self.address, REG_SHUNT)
        if raw & 0x8000:
            raw -= 0x10000
        bus = self.bus.read_word(self.address, REG_BUS)
        return raw * SHUNT_LSB_V / self.shunt_ohms, (bus >> 3) * BUS_LSB_V

    def _run(self) -> None:
        t0 = time.monotonic()
        while not self._stop.is_set():
            i, v = self.read_once()
            self._samples.append((time.monotonic() - t0, i, v))
            time.sleep(self.period)

    def start(self) -> None:
        self._samples = []
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> MeterReading:
        self._stop.set()
        if self._thread:
            self._thread.join()
        return integrate(self.name, self.resolution_a, self._samples)
