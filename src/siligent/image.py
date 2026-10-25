"""Read firmware images (ELF or raw binary) into loadable segments and symbols."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ImageError

PT_LOAD = 1
SHT_SYMTAB = 2


@dataclass(frozen=True)
class Segment:
    address: int
    data: bytes

    @property
    def end(self) -> int:
        return self.address + len(self.data)


@dataclass
class Image:
    path: str
    segments: list[Segment]
    symbols: dict[str, tuple[int, int]] = field(default_factory=dict)  # name -> (address, size)

    @property
    def sha256(self) -> str:
        h = hashlib.sha256()
        for s in sorted(self.segments, key=lambda s: s.address):
            h.update(s.address.to_bytes(4, "little"))
            h.update(s.data)
        return h.hexdigest()

    @property
    def size(self) -> int:
        return sum(len(s.data) for s in self.segments)

    def word_at(self, address: int) -> int | None:
        for s in self.segments:
            if s.address <= address and address + 4 <= s.end:
                off = address - s.address
                return int.from_bytes(s.data[off : off + 4], "little")
        return None


def load_image(path: str | Path, base_address: int | None = None) -> Image:
    """Load an .elf, or a raw .bin placed at `base_address`."""
    path = Path(path)
    if not path.is_file():
        raise ImageError(f"no such file: {path}")
    raw = path.read_bytes()
    if raw[:4] == b"\x7fELF":
        return _load_elf(raw, str(path))
    if base_address is None:
        raise ImageError("a raw binary needs a base address (for example the start of flash)")
    return Image(str(path), [Segment(base_address, raw)])


def _load_elf(raw: bytes, path: str) -> Image:
    if raw[4] != 1 or raw[5] != 1:
        raise ImageError("only 32-bit little-endian ELF files are supported")
    (e_phoff, e_shoff) = struct.unpack_from("<II", raw, 0x1C)
    (e_phentsize, e_phnum, e_shentsize, e_shnum) = struct.unpack_from("<HHHH", raw, 0x2A)

    segments = []
    for i in range(e_phnum):
        p_type, p_offset, _vaddr, p_paddr, p_filesz = struct.unpack_from("<IIIII", raw, e_phoff + i * e_phentsize)
        if p_type == PT_LOAD and p_filesz:
            # The physical (load) address is where the bytes live in flash.
            segments.append(Segment(p_paddr, raw[p_offset : p_offset + p_filesz]))

    symbols: dict[str, tuple[int, int]] = {}
    sections = [struct.unpack_from("<IIIIIIIIII", raw, e_shoff + i * e_shentsize) for i in range(e_shnum)]
    for sh in sections:
        if sh[1] != SHT_SYMTAB:
            continue
        sym_off, sym_size, link, entsize = sh[4], sh[5], sh[6], sh[9] or 16
        str_off = sections[link][4]
        for j in range(sym_size // entsize):
            st_name, st_value, st_size = struct.unpack_from("<III", raw, sym_off + j * entsize)
            if st_name:
                end = raw.index(b"\0", str_off + st_name)
                symbols[raw[str_off + st_name : end].decode(errors="replace")] = (st_value, st_size)

    if not segments:
        raise ImageError("the ELF file has no loadable segments")
    return Image(path, segments, symbols)
