"""MSP430 boards through TI's debug library (libmsp430, the MSP Debug Stack).

It drives the eZ-FET and MSP-FET debuggers on TI LaunchPads. One connection
does memory access, programming, run control and EnergyTrace energy
measurement, since only one program can own the debugger at a time.

Function signatures and constants follow TI's headers (MSP430.h,
MSP430_Debug.h, MSP430_EnergyTrace.h from the MSP Debug Stack).

This backend never updates the debugger's firmware. If the library and the
debugger firmware do not match, it stops and says how to update it by hand.
"""

from __future__ import annotations

import ctypes as C
import glob
import os
import struct
import threading
import time
from pathlib import Path

from ..errors import HardwareError
from ..image import Segment
from ..meters.base import MeterReading

TI_VENDOR = "2047"

# MSP430.h / MSP430_Debug.h
READ, WRITE = 1, 0
PUC_RESET, RST_RESET = 1, 2
FREE_RUN = 1
DEBUG_LPM_X = 14  # CONFIG_MODE: ultra-low-power debug; EnergyTrace needs it
STATUS_OK = 0

# MSP430_EnergyTrace.h
ET_PROFILING_ANALOG = 0
ET_PROFILING_ANALOG_DSTATE = 2
ET_PROFILING_10K = 4
ET_ALL = 2
ET_EVENT_WINDOW_100 = 2
ET_CALLBACKS_CONTINUOUS = 0
ET_CALLBACKS_ONLY_DURING_RUN = 1
ET_EVENT_STATE_VOLT_CURR = 7  # 8-byte header, 8-byte state, I (nA), V (mV), E (0.1 uJ)
ET_EVENT_CURR_VOLT_ENERGY = 8  # 8-byte header, I (nA), V (mV), E (0.1 uJ)

_i32 = C.c_int32
_PUSH = C.CFUNCTYPE(None, C.c_void_p, C.POINTER(C.c_uint8), C.c_uint32)
_ERR = C.CFUNCTYPE(None, C.c_void_p, C.c_char_p)


class _ETSetup(C.Structure):
    _fields_ = [("mode", _i32), ("freq", _i32), ("fmt", _i32), ("window", _i32), ("callback", _i32)]


class _ETCallbacks(C.Structure):
    _fields_ = [("context", C.c_void_p), ("push", _PUSH), ("error", _ERR)]


_SIGNATURES = {
    "MSP430_Initialize": [C.c_char_p, C.POINTER(_i32)],
    "MSP430_Close": [_i32],
    "MSP430_VCC": [_i32],
    "MSP430_Configure": [_i32, _i32],
    "MSP430_OpenDevice": [C.c_char_p, C.c_char_p, _i32, _i32, _i32],
    "MSP430_GetFoundDevice": [C.c_char_p, _i32],
    "MSP430_Memory": [_i32, C.c_char_p, _i32, _i32],
    "MSP430_VerifyMem": [_i32, _i32, C.c_char_p],
    "MSP430_Reset": [_i32, _i32, _i32],
    "MSP430_Run": [_i32, _i32],
    "MSP430_State": [C.POINTER(_i32), _i32, C.POINTER(_i32)],
    "MSP430_Error_Number": [],
    "MSP430_LoadDeviceDb": [C.c_char_p],
    "MSP430_GetCurVCCT": [C.POINTER(_i32)],
    "MSP430_EnableEnergyTrace": [C.POINTER(_ETSetup), C.POINTER(_ETCallbacks), C.POINTER(C.c_void_p)],
    "MSP430_DisableEnergyTrace": [C.c_void_p],
    "MSP430_ResetEnergyTrace": [C.c_void_p],
}

# The library holds one debugger connection per process.
_OPEN_LOCK = threading.Lock()


def find_libmsp430(explicit: str | None = None) -> str:
    candidates = [explicit, os.environ.get("SILIGENT_LIBMSP430")]
    for pattern in (
        "~/ti/ccs*/ccs/ccs_base/DebugServer/drivers/libmsp430.so",
        "/opt/ti/ccs*/ccs/ccs_base/DebugServer/drivers/libmsp430.so",
    ):
        candidates += sorted(glob.glob(os.path.expanduser(pattern)), reverse=True)
    candidates += ["/usr/local/lib/libmsp430.so", "/usr/lib/libmsp430.so"]
    for c in candidates:
        if c and Path(c).is_file():
            return c
    raise HardwareError(
        "TI's debug library (libmsp430.so) was not found. Install Code Composer Studio or the "
        "MSP Debug Stack, or set SILIGENT_LIBMSP430=/path/to/libmsp430.so."
    )


def tty_for_serial(serial: str | None, sysfs_root: str | Path = "/sys/bus/usb/devices") -> tuple[str, str]:
    """Find the debugger's serial port (its first interface). Returns (tty path, serial)."""
    found = []
    root = Path(sysfs_root)
    for dev in sorted(root.iterdir()) if root.is_dir() else []:
        try:
            if (dev / "idVendor").read_text().strip() != TI_VENDOR:
                continue
            dev_serial = (dev / "serial").read_text().strip()
        except OSError:
            continue
        ttys = sorted(glob.glob(str(dev / f"{dev.name}:1.0" / "tty" / "tty*")))
        if ttys:
            found.append(("/dev/" + Path(ttys[0]).name, dev_serial))
    if serial:
        for tty, s in found:
            if s == serial:
                return tty, s
        raise HardwareError(f"no TI debugger with serial {serial} is connected")
    if len(found) == 1:
        return found[0]
    if not found:
        raise HardwareError("no TI debugger (eZ-FET or MSP-FET) is connected")
    raise HardwareError("more than one TI debugger is connected; choose one with --probe <serial>")


def decode_energytrace(data: bytes) -> list[tuple[float, float, float, float]]:
    """EnergyTrace records -> (time_s, current_a, voltage_v, energy_j cumulative)."""
    out = []
    i = 0
    while i < len(data):
        eid = data[i]
        ts = int.from_bytes(data[i + 1 : i + 8], "little")
        if eid == ET_EVENT_STATE_VOLT_CURR and i + 26 <= len(data):
            _state, cur, volt, energy = struct.unpack_from("<QIHI", data, i + 8)
            i += 26
        elif eid == ET_EVENT_CURR_VOLT_ENERGY and i + 18 <= len(data):
            cur, volt, energy = struct.unpack_from("<IHI", data, i + 8)
            i += 18
        else:
            break  # unknown or truncated record: stop rather than misread the rest
        out.append((ts / 1e6, cur / 1e9, volt / 1e3, energy / 1e7))
    return out


class MSP430Backend:
    persistent = True
    erases = False  # FRAM is written in place; a restore must write every byte back

    def __init__(self, unique_id: str | None = None, lib: str | None = None, vcc_mv: int = 3000) -> None:
        self.lib_path = find_libmsp430(lib)
        self.vcc_mv = vcc_mv
        self.unique_id = unique_id or ""
        self.description = "TI MSP430 debugger"
        self.chip = ""
        self.library_version = 0
        self.measured_vcc_mv: int | None = None
        self._lib = None
        self._running = False
        self._held = False

    # Connection -------------------------------------------------------------------

    def _load(self):
        lib = C.CDLL(self.lib_path)
        for name, args in _SIGNATURES.items():
            f = getattr(lib, name)
            f.argtypes = args
            f.restype = _i32
        lib.MSP430_Error_String.argtypes = [_i32]
        lib.MSP430_Error_String.restype = C.c_char_p
        return lib

    def _check(self, status: int, what: str) -> None:
        if status != STATUS_OK:
            n = self._lib.MSP430_Error_Number()
            msg = (self._lib.MSP430_Error_String(n) or b"").decode(errors="replace").strip()
            raise HardwareError(f"{what} failed: {msg} (error {n})")

    def open(self) -> None:
        tty, serial = tty_for_serial(self.unique_id or None)
        if not _OPEN_LOCK.acquire(blocking=False):
            raise HardwareError("another MSP430 connection is already open in this process")
        self._held = True
        try:
            self._lib = self._load()
            version = _i32(0)
            self._check(self._lib.MSP430_Initialize(tty.encode(), C.byref(version)), "connecting to the debugger")
            self.library_version = version.value
            if version.value < 0:
                raise HardwareError(
                    "The debugger's firmware does not match this TI library. Siligent never updates "
                    "debugger firmware itself. Update it once with TI's tools (for example, start a debug "
                    "session in Code Composer Studio), then try again."
                )
            self._lib.MSP430_LoadDeviceDb(None)
            self._check(self._lib.MSP430_VCC(self.vcc_mv), f"setting the target supply to {self.vcc_mv} mV")
            self._check(self._lib.MSP430_OpenDevice(b"DEVICE_UNKNOWN", b"", 0, 0, 0), "opening the chip")
            self._check(self._lib.MSP430_Configure(DEBUG_LPM_X, 1), "turning on low-power debug")
            info = C.create_string_buffer(110)
            self._check(self._lib.MSP430_GetFoundDevice(info, 110), "identifying the chip")
            self.chip = info.raw[4:36].split(b"\0", 1)[0].decode(errors="replace")
            vcc = _i32(0)
            if self._lib.MSP430_GetCurVCCT(C.byref(vcc)) == STATUS_OK and vcc.value > 0:
                self.measured_vcc_mv = vcc.value  # the eZ-FET may not supply exactly what was asked
            self.unique_id = serial
            self.description = f"TI debugger {serial} ({self.chip})"
            self._running = False  # opening the chip halts it
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self._lib is not None:
            try:
                self._lib.MSP430_Run(FREE_RUN, 1)  # let the program run on without the debugger
                self._lib.MSP430_Close(0)  # 0: keep the board powered
            finally:
                self._lib = None
        if self._held:
            self._held = False
            _OPEN_LOCK.release()

    def _need(self):
        if self._lib is None:
            raise HardwareError("MSP430 debugger is not open")
        return self._lib

    # Memory ------------------------------------------------------------------------

    def _halted(self):
        """Context: memory access needs the CPU stopped; restart it afterwards if it was running."""
        backend = self

        class _Ctx:
            def __enter__(self):
                self.was_running = backend._running
                backend.halt()  # cheap, and safe even if the cached state is wrong

            def __exit__(self, *exc):
                if self.was_running:
                    backend.resume()

        return _Ctx()

    def read_memory(self, address: int, length: int) -> bytes:
        lib = self._need()
        buf = C.create_string_buffer(length)
        with self._halted():
            self._check(lib.MSP430_Memory(address, buf, length, READ), f"reading {hex(address)}")
        return buf.raw[:length]

    def read32(self, address: int) -> int:
        return int.from_bytes(self.read_memory(address, 4), "little")

    def write_memory(self, address: int, data: bytes) -> None:
        lib = self._need()
        with self._halted():
            self._check(lib.MSP430_Memory(address, C.create_string_buffer(data, len(data)), len(data), WRITE),
                        f"writing {hex(address)}")

    def program(self, segments: list[Segment]) -> None:
        """FRAM is written in place: no erase step, so nothing outside the segments changes."""
        lib = self._need()
        self.reset(halt=True)
        for seg in segments:
            buf = C.create_string_buffer(seg.data, len(seg.data))
            self._check(lib.MSP430_Memory(seg.address, buf, len(seg.data), WRITE), f"programming {hex(seg.address)}")
            self._check(lib.MSP430_VerifyMem(seg.address, len(seg.data), buf), f"verifying {hex(seg.address)}")
        self.reset(halt=True)

    # Run control -------------------------------------------------------------------

    def reset(self, halt: bool = False) -> None:
        lib = self._need()
        self._check(lib.MSP430_Reset(RST_RESET | PUC_RESET, 0, 0), "resetting the chip")
        self._running = True  # do not trust the reset alone: make sure it is stopped
        self.halt()
        if not halt:
            self.resume()

    def halt(self) -> None:
        """Always asks the chip to stop and checks it did; never trusts the cached state."""
        lib = self._need()
        state, cycles = _i32(), _i32()
        self._check(lib.MSP430_State(C.byref(state), 1, C.byref(cycles)), "halting the chip")
        if state.value != 0:  # STOPPED
            raise HardwareError(f"the chip did not stop (state {state.value})")
        self._running = False

    def resume(self) -> None:
        if self._running:
            return
        self._check(self._need().MSP430_Run(FREE_RUN, 0), "starting the chip")
        self._running = True

    def state(self) -> str:
        return "running" if self._running else "halted"

    # Energy ------------------------------------------------------------------------

    def energy_meter(self) -> EnergyTraceMeter:
        return EnergyTraceMeter(self)


class EnergyTraceMeter:
    """Energy measured by the debugger itself (TI EnergyTrace), on the LaunchPad's
    target supply rail: the whole board side of the debugger, not just the chip.

    The chip runs with the debugger released. Measured on an MSP-EXP430FR5994:
    while the debugger stays attached it keeps the chip's clocks running, so low
    power modes never show up (a flat ~255 uA). Released, they do. In this mode
    EnergyTrace delivers data only from about 120 ms after the run starts, so
    the test firmware waits in low power first.
    """

    name = "TI EnergyTrace (debugger-measured, whole target rail)"
    resolution_a = 1e-7  # approximate; TI quotes about 100 nA at the low end
    no_polling = True  # debug reads during the run would add to the reading
    window_s = 2.0  # covers the harness's settle wait (up to ~1.4 s) plus the tests

    def __init__(self, backend: MSP430Backend) -> None:
        self.backend = backend
        self._chunks: list[bytes] = []
        self._errors: list[str] = []
        self._handle = C.c_void_p()
        # Keep the callback objects alive for as long as the library may call them.
        self._push = _PUSH(self._on_push)
        self._err = _ERR(self._on_error)
        self._cbs = _ETCallbacks(None, self._push, self._err)
        self._setup = _ETSetup(
            ET_PROFILING_ANALOG, ET_PROFILING_10K, ET_ALL, ET_EVENT_WINDOW_100, ET_CALLBACKS_CONTINUOUS
        )
        self.records: list[tuple[float, float, float, float]] = []

    def _on_push(self, _ctx, buf, n) -> None:
        self._chunks.append(C.string_at(buf, n))

    def _on_error(self, _ctx, msg) -> None:
        self._errors.append((msg or b"").decode(errors="replace"))

    def start(self) -> None:
        lib = self.backend._need()
        self._chunks.clear()
        self._errors.clear()
        b = self.backend
        b._check(lib.MSP430_EnableEnergyTrace(C.byref(self._setup), C.byref(self._cbs), C.byref(self._handle)),
                 "starting EnergyTrace")
        b._check(lib.MSP430_ResetEnergyTrace(self._handle), "resetting EnergyTrace")

    def run_target(self) -> None:
        """Start the chip with the debugger released, so low-power modes are real."""
        b = self.backend
        b._check(b._need().MSP430_Run(FREE_RUN, 1), "starting the chip")
        b._running = True

    def stop(self) -> MeterReading:
        lib = self.backend._need()
        # Stop recording before taking the chip back: re-attaching the debugger draws
        # several hundred uA for ~200 ms, which is not part of the run.
        lib.MSP430_DisableEnergyTrace(self._handle)
        time.sleep(0.05)
        self.backend.halt()
        if self._errors:
            raise HardwareError(f"EnergyTrace reported: {'; '.join(self._errors)}")
        self.records = decode_energytrace(b"".join(self._chunks))
        return reading_from_records(self.name, self.resolution_a, self.records)


def reading_from_records(name: str, resolution_a: float, records, end: int | None = None) -> MeterReading:
    """Totals over records[0:end]. Energy comes from the debugger's own counter."""
    recs = records[:end] if end else records
    if len(recs) < 2:
        return MeterReading(name, 0.0, len(recs), 0.0, 0.0, 0.0, 0.0, 0.0, resolution_a)
    currents = [r[1] for r in recs]
    return MeterReading(
        meter=name,
        duration_s=recs[-1][0] - recs[0][0],
        samples=len(recs),
        energy_uj=(recs[-1][3] - recs[0][3]) * 1e6,
        avg_current_a=sum(currents) / len(currents),
        min_current_a=min(currents),
        max_current_a=max(currents),
        avg_voltage_v=sum(r[2] for r in recs) / len(recs),
        resolution_a=resolution_a,
    )
