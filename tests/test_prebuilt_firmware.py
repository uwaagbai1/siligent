"""CI runs firmware/msp430fr5994/prebuilt on the virtual board without rebuilding it
(it has no TI compiler), so a source change without a rebuild must fail here."""

import importlib.util
from pathlib import Path

from siligent.image import load_image

ROOT = Path(__file__).resolve().parent.parent
PREBUILT = ROOT / "firmware" / "msp430fr5994" / "prebuilt"


def test_prebuilt_msp430_firmware_matches_its_sources():
    spec = importlib.util.spec_from_file_location("prebuilt_hash", ROOT / "scripts" / "prebuilt_hash.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    recorded = (PREBUILT / "SOURCES.sha256").read_text().strip()
    assert recorded == mod.fingerprint(), (
        "MSP430 firmware sources changed since the prebuilt copies were made: "
        "run `make -C firmware/msp430fr5994 prebuilt` and commit firmware/msp430fr5994/prebuilt"
    )


def test_prebuilt_msp430_firmware_is_for_the_msp430():
    for name in ("tests.elf", "tests_fail.elf", "demo_refused.elf"):
        image = load_image(str(PREBUILT / name))
        assert image.arch == "msp430" and "bp_results" in image.symbols
