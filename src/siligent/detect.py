"""Find debug probes from their USB IDs, without opening them."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

# (vendor ID, product ID) -> probe kind. A product ID of None matches any product.
KNOWN_PROBES: dict[tuple[int, int | None], str] = {
    (0x0483, 0x3748): "ST-LINK/V2",
    (0x0483, 0x374B): "ST-LINK/V2-1",
    (0x0483, 0x374E): "STLINK-V3",
    (0x0483, 0x374F): "STLINK-V3",
    (0x0483, 0x3753): "STLINK-V3",
    (0x0483, 0x3754): "STLINK-V3",
    (0x1366, None): "SEGGER J-Link",
    (0x0D28, 0x0204): "CMSIS-DAP (DAPLink)",
    (0x2E8A, 0x000C): "Raspberry Pi Debug Probe (CMSIS-DAP)",
    (0x2047, 0x0013): "TI eZ-FET lite",
}

# Probe kinds Siligent can drive today, and the backend that drives each.
# Others are reported but not opened.
BACKEND_FOR_KIND = {
    "ST-LINK/V2": "pyocd",
    "ST-LINK/V2-1": "pyocd",
    "STLINK-V3": "pyocd",
    "TI eZ-FET lite": "msp430",
}
SUPPORTED_KINDS = set(BACKEND_FOR_KIND)


@dataclass(frozen=True)
class UsbProbe:
    kind: str
    vendor_id: str
    product_id: str
    serial: str
    product: str
    supported: bool

    @property
    def backend(self) -> str | None:
        return BACKEND_FOR_KIND.get(self.kind)

    def to_dict(self) -> dict:
        return asdict(self)


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def scan_meters(sysfs_root: str | Path = "/sys/bus/usb/devices") -> list[dict]:
    """List USB power meters Siligent knows, by their USB product name."""
    root = Path(sysfs_root)
    if not root.is_dir():
        return []
    known = {"PPK2": "Nordic Power Profiler Kit II (plugin: siligent-ppk2)"}
    return [
        {"kind": known[product], "serial": _read(dev / "serial")}
        for dev in sorted(root.iterdir())
        if (product := _read(dev / "product")) in known
    ]


def probe_kind(vid: int, pid: int) -> str | None:
    return KNOWN_PROBES.get((vid, pid)) or KNOWN_PROBES.get((vid, None))


def scan_usb(sysfs_root: str | Path = "/sys/bus/usb/devices") -> list[UsbProbe]:
    """List known debug probes by reading Linux sysfs. Opens no device."""
    found = []
    root = Path(sysfs_root)
    if not root.is_dir():
        return found
    for dev in sorted(root.iterdir()):
        vid_s, pid_s = _read(dev / "idVendor"), _read(dev / "idProduct")
        if not vid_s or not pid_s:
            continue
        kind = probe_kind(int(vid_s, 16), int(pid_s, 16))
        if kind:
            found.append(
                UsbProbe(
                    kind=kind,
                    vendor_id=vid_s,
                    product_id=pid_s,
                    serial=_read(dev / "serial"),
                    product=_read(dev / "product"),
                    supported=kind in SUPPORTED_KINDS,
                )
            )
    return found
