"""The power-meter interface and the energy maths shared by every meter."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol


@dataclass(frozen=True)
class MeterReading:
    meter: str
    duration_s: float
    samples: int
    energy_uj: float
    avg_current_a: float
    min_current_a: float
    max_current_a: float
    avg_voltage_v: float
    resolution_a: float  # smallest current step the meter can see

    def to_dict(self) -> dict:
        d = asdict(self)
        # Say plainly when the meter is too coarse for what it measured.
        d["below_resolution"] = self.avg_current_a < 10 * self.resolution_a
        return d


class PowerMeter(Protocol):
    name: str
    resolution_a: float

    def start(self) -> None:
        """Begin sampling in the background."""

    def stop(self) -> MeterReading:
        """Stop sampling and return the totals for the window."""


def integrate(meter: str, resolution_a: float, samples: list[tuple[float, float, float]]) -> MeterReading:
    """Totals from (time_s, current_a, voltage_v) samples, by the trapezoid rule."""
    if not samples:
        return MeterReading(meter, 0.0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, resolution_a)
    energy_j = 0.0
    for (t0, i0, v0), (t1, i1, v1) in zip(samples, samples[1:]):
        energy_j += (i0 * v0 + i1 * v1) / 2 * (t1 - t0)
    duration = samples[-1][0] - samples[0][0]
    currents = [s[1] for s in samples]
    return MeterReading(
        meter=meter,
        duration_s=duration,
        samples=len(samples),
        energy_uj=energy_j * 1e6,
        avg_current_a=sum(currents) / len(currents),
        min_current_a=min(currents),
        max_current_a=max(currents),
        avg_voltage_v=sum(s[2] for s in samples) / len(samples),
        resolution_a=resolution_a,
    )
