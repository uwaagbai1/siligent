# siligent-ppk2

Nordic Power Profiler Kit II (PPK2) as a Siligent power meter. It measures
down to sub-microamp currents, so it can see sleep current, which the INA219
cannot.

Licensed GPL-2.0 because it builds on [ppk2-api](https://github.com/IRNAS/ppk2-api-python)
(GPL-2.0). It is a separate package so Siligent itself stays Apache-2.0.

## Install

```sh
.venv/bin/pip install -e plugins/siligent-ppk2
```

## Wiring on a NUCLEO-L476RG (ampere mode, recommended)

1. Remove jumper JP6 (IDD). The chip's supply now runs only through those two pins.
2. Connect the PPK2 in series across JP6: the board's 3.3 V side to the PPK2
   VIN, the chip side to the PPK2 VOUT. Connect GND to the board's GND.
3. The board still powers itself over USB; the PPK2 only measures the chip's current.

Source mode (the PPK2 supplies the chip) also works, but the board's other
3.3 V parts must not be fed at the same time. Siligent refuses any source
voltage outside the chip's range (1.71-3.6 V on the L476).

## bench.toml

```toml
[meter]
type = "ppk2"
mode = "ampere"        # or "source"
voltage_mv = 3300      # the chip's supply voltage; used to turn current into energy
# port = "/dev/ttyACM1"  # optional; found automatically by default
```
