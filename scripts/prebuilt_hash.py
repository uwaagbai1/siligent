"""Fingerprint of the sources the prebuilt MSP430 firmware was built from.

`make -C firmware/msp430fr5994 prebuilt` writes it to prebuilt/SOURCES.sha256;
a unit test recomputes it, so a source change without a rebuild fails CI
instead of quietly testing old firmware.
"""

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "firmware"
PATTERNS = ["common/*.c", "common/*.h", "msp430fr5994/*.c", "msp430fr5994/*.cmd", "msp430fr5994/Makefile"]


def fingerprint() -> str:
    h = hashlib.sha256()
    for path in sorted({p for pat in PATTERNS for p in ROOT.glob(pat)}):
        h.update(path.relative_to(ROOT).as_posix().encode() + b"\0")
        h.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()


if __name__ == "__main__":
    out = fingerprint()
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(out + "\n")
    print(out)
