"""Generate a simple CorridorIQ ICO for the desktop shortcut."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "corridoriq.ico"


def write_png(size: int, rgba: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b""
    for y in range(size):
        raw += b"\x00" + rgba[y * size * 4 : (y + 1) * size * 4]
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def make_icon(size: int = 64) -> None:
    # Brand: navy deep + gold (matches style.css)
    pixels = bytearray()
    cx = cy = size // 2
    for y in range(size):
        for x in range(size):
            r, g, b, a = 10, 14, 26, 255  # --navy-deep
            # gold rounded square frame
            inset = 6
            on_edge = (
                (inset <= x < size - inset and inset <= y < size - inset)
                and (
                    x < inset + 3
                    or x >= size - inset - 3
                    or y < inset + 3
                    or y >= size - inset - 3
                )
            )
            if on_edge:
                r, g, b = 201, 164, 76  # --gold
            # center gold bar (corridor mark)
            if 28 <= x <= 35 and 18 <= y <= 46:
                r, g, b = 226, 193, 121  # --gold-light
            # side corridor lines
            if 18 <= x <= 22 and 24 <= y <= 40:
                r, g, b = 201, 164, 76
            if 41 <= x <= 45 and 24 <= y <= 40:
                r, g, b = 201, 164, 76
            # subtle outer ring
            d2 = (x - cx) ** 2 + (y - cy) ** 2
            if 780 < d2 < 980:
                r, g, b = 35, 39, 51
            pixels.extend((r, g, b, a))

    png = write_png(size, bytes(pixels))
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack(
        "<BBBBHHII",
        size if size < 256 else 0,
        size if size < 256 else 0,
        0,
        0,
        1,
        32,
        len(png),
        6 + 16,
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(header + entry + png)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    make_icon()
