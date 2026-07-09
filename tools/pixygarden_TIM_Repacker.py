#!/usr/bin/env python3
"""
pixygarden_tim_roundtrip_v7_baseline_delta.py

A self-contained PlayStation TIM <-> PNG round-trip tool with palette-safe indexed PNG injection, CLUT-page layer merging, TIM-index and baseline delta merging, and forced transparency handling.

Main goals
----------
1. Extract every visible image page from a TIM as PNG.
   - For 4bpp/8bpp indexed TIMs, each CLUT page is extracted as its own PNG.
   - For direct-color TIMs, one PNG is extracted.

2. Inject edited PNGs back into the original TIM in-place.
   - The original TIM structure, headers, offsets, and block sizes are preserved.
   - For indexed TIMs, pixel indices are repacked into the original PXL block.
   - Optionally, indexed PNG palettes can be written back into the TIM CLUT.

Why indexed PNGs matter
-----------------------
A 4bpp TIM has one shared pixel-index plane. If it has 3 CLUT pages, those
are three palettes applied to the same pixel indices. Editing one page can
therefore affect the appearance of other pages. This is normal for PS1 TIMs.

For safest round-tripping:
    Extract as indexed PNG.
    Edit while preserving indexed/palette mode.
    Inject the indexed PNG back.

RGBA PNG injection is also supported by mapping colors back to the selected
TIM CLUT page. Use --nearest if your editor introduced slightly altered colors.

Basic examples
--------------

For font TIMs where CLUT pages reveal different visible layers/images, use:
    python pixygarden_tim_roundtrip_v7_baseline_delta.py inject --tim FONT11Z0.TIM --png-dir edited_FONT11Z0 --out FONT11Z0_patched.TIM --multi-page-mode merge-visible

Basic examples
--------------

Show TIM info:
    python pixygarden_tim_roundtrip_v7_baseline_delta.py info --tim 48.tim

Extract all pages as indexed PNGs:
    python pixygarden_tim_roundtrip_v7_baseline_delta.py extract --tim 48.tim --out-dir extracted_48

Inject edited PNGs back, preserving original TIM CLUTs and remapping edited PNG palette colors back to TIM indices:
    python pixygarden_tim_roundtrip_v7_baseline_delta.py inject --tim 48.tim --png-dir extracted_48 --out 48_patched.tim

Inject one edited page:
    python pixygarden_tim_roundtrip_v7_baseline_delta.py inject --tim 48.tim --png extracted_48/48_page02.png --out 48_patched.tim

Inject an RGBA PNG by nearest-color palette mapping:
    python pixygarden_tim_roundtrip_v7_baseline_delta.py inject --tim 48.tim --png edited_page02_rgba.png --page 2 --out 48_patched.tim --nearest

Embedded TIM at offset:
    python pixygarden_tim_roundtrip_v7_baseline_delta.py extract --tim archive.bin --tim-offset 0x1234 --out-dir extracted_embedded

Override interpretation values for odd files:
    python pixygarden_tim_roundtrip_v7_baseline_delta.py extract --tim weird.tim --out-dir out --force-bpp 4 --force-width 256 --force-height 253

Notes
-----
- For standard TIMs, you should not need offsets or size flags.
- The tool writes helpful metadata into extracted PNGs, so reinjection usually
  does not need --page.
- For multi-page CLUT TIMs, injecting several edited PNGs requires their pixel
  indices to agree because the TIM has only one shared pixel plane. By default,
  the tool errors if pages conflict. Use --pixel-source-page to pick one page
  as the source of pixel indices.
"""

from __future__ import annotations

import argparse
import csv
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image, PngImagePlugin


# TIM PMD values from the flags field
PMD_4BPP = 0
PMD_8BPP = 1
PMD_16BPP = 2
PMD_24BPP = 3

PMD_TO_BPP = {
    PMD_4BPP: 4,
    PMD_8BPP: 8,
    PMD_16BPP: 16,
    PMD_24BPP: 24,
}

BPP_TO_PMD = {
    4: PMD_4BPP,
    8: PMD_8BPP,
    16: PMD_16BPP,
    24: PMD_24BPP,
}


@dataclass
class CLUTBlock:
    block_offset: int
    bnum: int
    dx: int
    dy: int
    w: int
    h: int
    data_offset: int
    raw_colors: list[int]


@dataclass
class PXLBlock:
    block_offset: int
    bnum: int
    dx: int
    dy: int
    w_words: int
    h: int
    data_offset: int
    data_size: int


@dataclass
class TIMInfo:
    path: Path
    data: bytearray
    tim_offset: int
    magic: int
    flags: int
    pmd: int
    has_clut: bool
    clut: Optional[CLUTBlock]
    pxl: PXLBlock

    # Optional interpretation overrides.
    force_pmd: Optional[int] = None
    force_width: Optional[int] = None
    force_height: Optional[int] = None
    force_pixel_offset: Optional[int] = None
    force_clut_data_offset: Optional[int] = None
    force_palette_size: Optional[int] = None
    force_page_count: Optional[int] = None

    @property
    def effective_pmd(self) -> int:
        return self.force_pmd if self.force_pmd is not None else self.pmd

    @property
    def bpp(self) -> int:
        return PMD_TO_BPP.get(self.effective_pmd, -1)

    @property
    def palette_size(self) -> int:
        if self.force_palette_size:
            return self.force_palette_size
        if self.effective_pmd == PMD_4BPP:
            return 16
        if self.effective_pmd == PMD_8BPP:
            return 256
        return 0

    @property
    def page_count(self) -> int:
        if self.force_page_count:
            return self.force_page_count
        if not self.has_clut or not self.clut or self.palette_size == 0:
            return 1
        return len(self.clut.raw_colors) // self.palette_size

    @property
    def pixel_width(self) -> int:
        if self.force_width is not None:
            return self.force_width
        pmd = self.effective_pmd
        if pmd == PMD_4BPP:
            return self.pxl.w_words * 4
        if pmd == PMD_8BPP:
            return self.pxl.w_words * 2
        if pmd == PMD_16BPP:
            return self.pxl.w_words
        if pmd == PMD_24BPP:
            return (self.pxl.w_words * 2) // 3
        raise ValueError(f"Unsupported PMD {pmd}")

    @property
    def pixel_height(self) -> int:
        return self.force_height if self.force_height is not None else self.pxl.h

    @property
    def pixel_data_offset(self) -> int:
        return self.force_pixel_offset if self.force_pixel_offset is not None else self.pxl.data_offset

    @property
    def clut_data_offset(self) -> Optional[int]:
        if not self.clut:
            return None
        return self.force_clut_data_offset if self.force_clut_data_offset is not None else self.clut.data_offset


def u16(data: bytes | bytearray, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]


def u32(data: bytes | bytearray, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def w16(data: bytearray, off: int, value: int) -> None:
    struct.pack_into("<H", data, off, value & 0xFFFF)


def parse_tim(
    path: Path,
    tim_offset: int = 0,
    force_bpp: Optional[int] = None,
    force_width: Optional[int] = None,
    force_height: Optional[int] = None,
    force_pixel_offset: Optional[int] = None,
    force_clut_data_offset: Optional[int] = None,
    force_palette_size: Optional[int] = None,
    force_page_count: Optional[int] = None,
) -> TIMInfo:
    data = bytearray(path.read_bytes())
    if tim_offset < 0 or tim_offset + 8 > len(data):
        raise ValueError(f"Bad TIM offset 0x{tim_offset:X}")

    magic = u32(data, tim_offset)
    if magic != 0x10:
        raise ValueError(f"Not a TIM at offset 0x{tim_offset:X}: magic=0x{magic:X}, expected 0x10")

    flags = u32(data, tim_offset + 4)
    pmd = flags & 0x7
    has_clut = bool(flags & 0x8)
    pos = tim_offset + 8

    clut = None
    if has_clut:
        bnum = u32(data, pos)
        if bnum < 12 or pos + bnum > len(data):
            raise ValueError(f"Bad CLUT block length 0x{bnum:X} at 0x{pos:X}")
        data_offset = pos + 12
        raw_count = (bnum - 12) // 2
        clut = CLUTBlock(
            block_offset=pos,
            bnum=bnum,
            dx=u16(data, pos + 4),
            dy=u16(data, pos + 6),
            w=u16(data, pos + 8),
            h=u16(data, pos + 10),
            data_offset=data_offset,
            raw_colors=[u16(data, data_offset + i * 2) for i in range(raw_count)],
        )
        pos += bnum

    bnum = u32(data, pos)
    if bnum < 12 or pos + bnum > len(data):
        raise ValueError(f"Bad image/PXL block length 0x{bnum:X} at 0x{pos:X}")

    pxl = PXLBlock(
        block_offset=pos,
        bnum=bnum,
        dx=u16(data, pos + 4),
        dy=u16(data, pos + 6),
        w_words=u16(data, pos + 8),
        h=u16(data, pos + 10),
        data_offset=pos + 12,
        data_size=bnum - 12,
    )

    force_pmd = BPP_TO_PMD.get(force_bpp) if force_bpp is not None else None
    if force_bpp is not None and force_pmd is None:
        raise ValueError("--force-bpp must be one of 4, 8, 16, 24")

    return TIMInfo(
        path=path,
        data=data,
        tim_offset=tim_offset,
        magic=magic,
        flags=flags,
        pmd=pmd,
        has_clut=has_clut,
        clut=clut,
        pxl=pxl,
        force_pmd=force_pmd,
        force_width=force_width,
        force_height=force_height,
        force_pixel_offset=force_pixel_offset,
        force_clut_data_offset=force_clut_data_offset,
        force_palette_size=force_palette_size,
        force_page_count=force_page_count,
    )


# ---------------------------------------------------------------------------
# PS1 color conversion
# ---------------------------------------------------------------------------

def ps1_5551_to_rgba(v: int) -> tuple[int, int, int, int]:
    r5 = v & 0x1F
    g5 = (v >> 5) & 0x1F
    b5 = (v >> 10) & 0x1F

    # Editing convention: 0x0000 previews as transparent. Everything else
    # previews as opaque. This is not the full PS1 GPU blending model, but it
    # is a practical PNG-editing convention.
    a = 0 if v == 0 else 255
    return (r5 << 3, g5 << 3, b5 << 3, a)


def rgba_to_ps1_5551(
    rgba: tuple[int, int, int, int],
    old_value: int = 0,
    stp_policy: str = "preserve",
) -> int:
    r, g, b, a = rgba
    if a == 0:
        return 0x0000

    r5 = max(0, min(31, r >> 3))
    g5 = max(0, min(31, g >> 3))
    b5 = max(0, min(31, b >> 3))

    if stp_policy == "preserve":
        stp = old_value & 0x8000
    elif stp_policy == "clear":
        stp = 0
    elif stp_policy == "set":
        stp = 0x8000
    elif stp_policy == "black-opaque":
        # Keep original STP normally, but force STP on for opaque black so it
        # does not collapse into transparent 0x0000.
        stp = 0x8000 if (r5 == g5 == b5 == 0) else (old_value & 0x8000)
    else:
        raise ValueError(f"Unknown STP policy: {stp_policy}")

    return r5 | (g5 << 5) | (b5 << 10) | stp


def quantized_rgba_key(rgba: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    r, g, b, a = rgba
    if a == 0:
        return (0, 0, 0, 0)
    return ((r >> 3) << 3, (g >> 3) << 3, (b >> 3) << 3, 255)


# ---------------------------------------------------------------------------
# TIM pixel reading/writing
# ---------------------------------------------------------------------------

def indexed_expected_count(info: TIMInfo) -> int:
    return info.pixel_width * info.pixel_height


def read_indices(info: TIMInfo) -> list[int]:
    pmd = info.effective_pmd
    if pmd not in (PMD_4BPP, PMD_8BPP):
        raise ValueError("read_indices is only for 4bpp/8bpp indexed TIMs")

    off = info.pixel_data_offset
    raw = info.data[off:off + info.pxl.data_size]
    indices: list[int] = []

    if pmd == PMD_4BPP:
        for b in raw:
            indices.append(b & 0x0F)
            indices.append((b >> 4) & 0x0F)
    elif pmd == PMD_8BPP:
        indices = list(raw)

    expected = indexed_expected_count(info)
    if len(indices) < expected:
        raise ValueError(f"Pixel data too short: got {len(indices)} indices, expected {expected}")

    return indices[:expected]


def pack_indices(info: TIMInfo, indices: list[int]) -> bytes:
    expected = indexed_expected_count(info)
    if len(indices) != expected:
        raise ValueError(f"Wrong pixel index count: got {len(indices)}, expected {expected}")

    pmd = info.effective_pmd

    if pmd == PMD_4BPP:
        if info.pixel_width % 2 != 0:
            raise ValueError("4bpp TIM width must be even for packing")
        if any(i < 0 or i > 15 for i in indices):
            raise ValueError("4bpp TIM cannot store palette indices outside 0..15")
        out = bytearray()
        for i in range(0, len(indices), 2):
            out.append((indices[i] & 0x0F) | ((indices[i + 1] & 0x0F) << 4))
        return bytes(out)

    if pmd == PMD_8BPP:
        if any(i < 0 or i > 255 for i in indices):
            raise ValueError("8bpp TIM cannot store palette indices outside 0..255")
        return bytes(indices)

    raise ValueError("Unsupported indexed PMD for packing")


def get_page_raw_palette(info: TIMInfo, page: int) -> list[int]:
    if not info.clut:
        raise ValueError("TIM has no CLUT")
    if info.palette_size <= 0:
        raise ValueError("This TIM mode has no indexed CLUT pages")
    if page < 0 or page >= info.page_count:
        raise ValueError(f"Bad CLUT page {page}; valid range 0..{info.page_count - 1}")

    start = page * info.palette_size
    end = start + info.palette_size
    if end > len(info.clut.raw_colors):
        raise ValueError("CLUT page exceeds available palette colors")
    return info.clut.raw_colors[start:end]


def set_page_raw_palette(info: TIMInfo, page: int, raw_colors: list[int]) -> None:
    if not info.clut:
        raise ValueError("TIM has no CLUT")
    if len(raw_colors) != info.palette_size:
        raise ValueError(f"Palette size mismatch: got {len(raw_colors)}, expected {info.palette_size}")
    if page < 0 or page >= info.page_count:
        raise ValueError(f"Bad CLUT page {page}")

    clut_off = info.clut_data_offset
    if clut_off is None:
        raise ValueError("No CLUT data offset available")

    base_entry = page * info.palette_size
    for i, color in enumerate(raw_colors):
        entry_index = base_entry + i
        info.clut.raw_colors[entry_index] = color & 0xFFFF
        w16(info.data, clut_off + entry_index * 2, color)


def make_palette_lists(raw_pal: list[int]) -> tuple[list[int], list[int]]:
    rgb: list[int] = []
    alpha = [255] * 256
    for i, c in enumerate(raw_pal):
        r, g, b, a = ps1_5551_to_rgba(c)
        rgb.extend([r, g, b])
        alpha[i] = a
    while len(rgb) < 256 * 3:
        rgb.extend([0, 0, 0])
    return rgb, alpha


def make_indexed_png(info: TIMInfo, page: int) -> Image.Image:
    indices = read_indices(info)
    im = Image.new("P", (info.pixel_width, info.pixel_height))
    im.putdata(indices)
    rgb, alpha = make_palette_lists(get_page_raw_palette(info, page))
    im.putpalette(rgb)
    im.info["transparency"] = bytes(alpha)
    return im


def make_indexed_rgba_preview(info: TIMInfo, page: int) -> Image.Image:
    indices = read_indices(info)
    raw_pal = get_page_raw_palette(info, page)
    rgba_pal = [ps1_5551_to_rgba(c) for c in raw_pal]
    im = Image.new("RGBA", (info.pixel_width, info.pixel_height))
    im.putdata([rgba_pal[i] for i in indices])
    return im


def read_direct_rgba(info: TIMInfo, direct_order: str = "rgb") -> Image.Image:
    pmd = info.effective_pmd
    off = info.pixel_data_offset
    w = info.pixel_width
    h = info.pixel_height
    data = info.data
    im = Image.new("RGBA", (w, h))

    if pmd == PMD_16BPP:
        pixels = []
        for i in range(w * h):
            v = u16(data, off + i * 2)
            pixels.append(ps1_5551_to_rgba(v))
        im.putdata(pixels)
        return im

    if pmd == PMD_24BPP:
        row_bytes = info.pxl.w_words * 2
        pixels = []
        for y in range(h):
            row = off + y * row_bytes
            for x in range(w):
                b0 = data[row + x * 3]
                b1 = data[row + x * 3 + 1]
                b2 = data[row + x * 3 + 2]
                if direct_order == "rgb":
                    r, g, b = b0, b1, b2
                elif direct_order == "bgr":
                    b, g, r = b0, b1, b2
                else:
                    raise ValueError("--direct-order must be rgb or bgr")
                pixels.append((r, g, b, 255))
        im.putdata(pixels)
        return im

    raise ValueError("Direct extraction only supports 16bpp/24bpp TIMs")


def write_direct_rgba(info: TIMInfo, png: Path, direct_order: str = "rgb", stp_policy: str = "preserve") -> None:
    pmd = info.effective_pmd
    im = Image.open(png).convert("RGBA")
    if im.size != (info.pixel_width, info.pixel_height):
        raise ValueError(f"PNG size {im.size} does not match TIM size {(info.pixel_width, info.pixel_height)}")

    off = info.pixel_data_offset

    if pmd == PMD_16BPP:
        for i, rgba in enumerate(im.getdata()):
            old = u16(info.data, off + i * 2)
            w16(info.data, off + i * 2, rgba_to_ps1_5551(rgba, old, stp_policy))
        return

    if pmd == PMD_24BPP:
        row_bytes = info.pxl.w_words * 2
        for y in range(info.pixel_height):
            row = off + y * row_bytes
            for x in range(info.pixel_width):
                r, g, b, _a = im.getpixel((x, y))
                vals = (r, g, b) if direct_order == "rgb" else (b, g, r)
                base = row + x * 3
                info.data[base:base + 3] = bytes(vals)
        return

    raise ValueError("Direct injection only supports 16bpp/24bpp TIMs")


# ---------------------------------------------------------------------------
# PNG handling
# ---------------------------------------------------------------------------

def png_page_from_metadata_or_name(path: Path) -> Optional[int]:
    try:
        im = Image.open(path)
        meta_page = im.info.get("TIM_PAGE")
        if meta_page is not None:
            return int(str(meta_page), 0)
    except Exception:
        pass

    m = re.search(r"(?:page|clut)[_-]?(\d+)", path.stem, re.IGNORECASE)
    if m:
        return int(m.group(1), 10)
    return None


def png_palette_to_raw_colors(
    png_path: Path,
    info: TIMInfo,
    page: int,
    stp_policy: str,
) -> Optional[list[int]]:
    im = Image.open(png_path)
    if im.mode != "P":
        return None

    pal = im.getpalette()
    if not pal:
        return None

    transparency = im.info.get("transparency")
    alpha = [255] * 256
    if isinstance(transparency, bytes):
        for i, a in enumerate(transparency[:256]):
            alpha[i] = a
    elif isinstance(transparency, list):
        for i, a in enumerate(transparency[:256]):
            alpha[i] = int(a)
    elif isinstance(transparency, int):
        if 0 <= transparency < 256:
            alpha[transparency] = 0

    old_pal = get_page_raw_palette(info, page)
    raw_colors: list[int] = []
    for i in range(info.palette_size):
        r = pal[i * 3] if i * 3 < len(pal) else 0
        g = pal[i * 3 + 1] if i * 3 + 1 < len(pal) else 0
        b = pal[i * 3 + 2] if i * 3 + 2 < len(pal) else 0
        a = alpha[i]
        old = old_pal[i] if i < len(old_pal) else 0
        raw_colors.append(rgba_to_ps1_5551((r, g, b, a), old, stp_policy))
    return raw_colors



def png_index_palette_rgba(im: Image.Image) -> list[tuple[int, int, int, int]]:
    """Return a P-mode PNG palette as RGBA tuples.

    This is used to recover correct TIM palette indices even if an editor
    reorders or optimizes the PNG palette.
    """
    if im.mode != "P":
        raise ValueError("png_index_palette_rgba expects a P-mode PNG")

    pal = im.getpalette() or []
    transparency = im.info.get("transparency")
    alpha = [255] * 256

    if isinstance(transparency, bytes):
        for i, a in enumerate(transparency[:256]):
            alpha[i] = a
    elif isinstance(transparency, list):
        for i, a in enumerate(transparency[:256]):
            alpha[i] = int(a)
    elif isinstance(transparency, int):
        if 0 <= transparency < 256:
            alpha[transparency] = 0

    out: list[tuple[int, int, int, int]] = []
    for i in range(256):
        base = i * 3
        r = pal[base] if base < len(pal) else 0
        g = pal[base + 1] if base + 1 < len(pal) else 0
        b = pal[base + 2] if base + 2 < len(pal) else 0
        out.append((r, g, b, alpha[i]))
    return out


def indexed_png_palette_matches_tim_page(im: Image.Image, raw_palette: list[int], palette_size: int) -> bool:
    """Return True if PNG index N still visually matches TIM CLUT entry N."""
    png_pal = png_index_palette_rgba(im)
    tim_pal = [ps1_5551_to_rgba(c) for c in raw_palette]

    for i in range(palette_size):
        if quantized_rgba_key(png_pal[i]) != quantized_rgba_key(tim_pal[i]):
            return False
    return True


def build_png_index_to_tim_index_map(
    im: Image.Image,
    raw_palette: list[int],
    palette_size: int,
    nearest: bool,
) -> list[int]:
    """Map PNG palette indices back to original TIM palette indices by color.

    Many editors keep PNGs in indexed mode but reorder/optimize the palette.
    In that case, raw PNG index 3 may no longer mean TIM index 3. This mapping
    solves that by looking at the PNG palette color and finding the matching
    TIM CLUT entry.
    """
    png_pal = png_index_palette_rgba(im)
    mapping = [0] * 256

    for png_i, rgba in enumerate(png_pal):
        if png_i >= palette_size and rgba[3] == 0:
            mapping[png_i] = 0
            continue
        mapping[png_i] = palette_index_from_rgba(rgba, raw_palette, nearest)

    return mapping


def palette_index_from_rgba(
    rgba: tuple[int, int, int, int],
    raw_palette: list[int],
    nearest: bool,
) -> int:
    pal_rgba = [ps1_5551_to_rgba(c) for c in raw_palette]
    key = quantized_rgba_key(rgba)

    for i, p in enumerate(pal_rgba):
        if quantized_rgba_key(p) == key:
            return i

    if rgba[3] == 0:
        for i, p in enumerate(pal_rgba):
            if p[3] == 0:
                return i
        return 0

    if not nearest:
        raise ValueError(
            f"RGBA color {rgba} is not in the TIM palette after 5-bit quantization. "
            f"Use --nearest to permit closest-color mapping."
        )

    cr, cg, cb, ca = rgba
    best_i = 0
    best_dist = 10**18
    for i, (r, g, b, a) in enumerate(pal_rgba):
        dist = (cr - r) ** 2 + (cg - g) ** 2 + (cb - b) ** 2 + ((ca - a) // 8) ** 2
        if dist < best_dist:
            best_i = i
            best_dist = dist
    return best_i


def indices_from_png(
    info: TIMInfo,
    png_path: Path,
    page: int,
    nearest: bool,
    indexed_index_mode: str = "auto",
    transparent_rgb: Optional[tuple[int, int, int]] = None,
    transparent_png_index: Optional[int] = None,
) -> list[int]:
    im = Image.open(png_path)
    if im.size != (info.pixel_width, info.pixel_height):
        raise ValueError(f"{png_path}: PNG size {im.size} does not match TIM size {(info.pixel_width, info.pixel_height)}")

    raw_pal = get_page_raw_palette(info, page)

    forced_rgba = patch_rgba_to_transparent_before_mapping(png_path, transparent_rgb, transparent_png_index)

    if forced_rgba is not None:
        return [palette_index_from_rgba(c, raw_pal, nearest) for c in forced_rgba.getdata()]

    if im.mode == "P":
        png_indices = [int(i) for i in im.getdata()]
        max_png_index = max(png_indices, default=0)

        if indexed_index_mode == "raw":
            max_allowed = info.palette_size - 1
            if max_png_index > max_allowed:
                raise ValueError(f"{png_path}: indexed PNG uses palette index {max_png_index}, but this TIM page only supports 0..{max_allowed}")
            return png_indices

        if indexed_index_mode == "auto":
            if indexed_png_palette_matches_tim_page(im, raw_pal, info.palette_size):
                max_allowed = info.palette_size - 1
                if max_png_index > max_allowed:
                    raise ValueError(f"{png_path}: indexed PNG uses palette index {max_png_index}, but this TIM page only supports 0..{max_allowed}")
                print(f"{png_path}: PNG palette order matches TIM page {page}; using raw indices.")
                return png_indices
            print(f"{png_path}: PNG palette order differs from TIM page {page}; remapping indices by color.")
        elif indexed_index_mode == "match-palette":
            print(f"{png_path}: remapping indexed PNG indices by palette color.")
        else:
            raise ValueError(f"Unknown indexed index mode: {indexed_index_mode}")

        mapping = build_png_index_to_tim_index_map(im, raw_pal, info.palette_size, nearest)
        mapped = [mapping[i] for i in png_indices]
        max_allowed = info.palette_size - 1
        bad = max(mapped, default=0)
        if bad > max_allowed:
            raise ValueError(f"{png_path}: mapped TIM index {bad}, but this TIM page only supports 0..{max_allowed}")
        return mapped

    rgba = im.convert("RGBA")
    return [palette_index_from_rgba(c, raw_pal, nearest) for c in rgba.getdata()]


def png_pixel_alpha_list(png_path: Path) -> list[int]:
    """Return one alpha value per pixel for P or RGBA PNGs."""
    im = Image.open(png_path)
    if im.mode == "P":
        trans = im.info.get("transparency")
        alpha = [255] * 256
        if isinstance(trans, bytes):
            for i, a in enumerate(trans[:256]):
                alpha[i] = int(a)
        elif isinstance(trans, list):
            for i, a in enumerate(trans[:256]):
                alpha[i] = int(a)
        elif isinstance(trans, int):
            if 0 <= trans < 256:
                alpha[trans] = 0
        return [alpha[int(i)] for i in im.getdata()]

    return [a for (_r, _g, _b, a) in im.convert("RGBA").getdata()]


def common_transparent_index(info: TIMInfo) -> int:
    """Find a palette index that is transparent in every CLUT page."""
    if not info.clut:
        return 0
    for idx in range(info.palette_size):
        ok = True
        for page in range(info.page_count):
            raw = get_page_raw_palette(info, page)[idx]
            if ps1_5551_to_rgba(raw)[3] != 0:
                ok = False
                break
        if ok:
            return idx
    return 0


def merge_visible_page_pngs_to_indices(
    info: TIMInfo,
    page_pngs: list[tuple[Path, int]],
    nearest: bool,
    indexed_index_mode: str,
    alpha_threshold: int,
    transparent_index: Optional[int],
    overlap_policy: str,
    transparent_rgb: Optional[tuple[int, int, int]] = None,
    transparent_png_index: Optional[int] = None,
) -> list[int]:
    """Merge several CLUT-page PNG views into one shared TIM pixel-index plane.

    This is for TIMs where each CLUT page reveals different index layers. For
    example, page 0 may show one font sheet while page 1 shows another, because
    each CLUT makes different palette indices visible/transparent.

    Algorithm:
      - Start every pixel as a transparent/common background index.
      - For each page PNG, map its visible pixels back to that page's TIM indices.
      - If only one page contributes at a pixel, use that index.
      - If multiple pages contribute different indices, either error, keep first,
        keep last, or prefer nonzero depending on --overlap-policy.
    """
    expected_size = (info.pixel_width, info.pixel_height)
    pixel_count = info.pixel_width * info.pixel_height
    bg = common_transparent_index(info) if transparent_index is None else transparent_index

    merged = [bg] * pixel_count
    owner: list[Optional[Path]] = [None] * pixel_count

    for png, page in page_pngs:
        im = Image.open(png)
        if im.size != expected_size:
            raise ValueError(f"{png}: PNG size {im.size} does not match TIM size {expected_size}")

        indices = indices_from_png(info, png, page, nearest, indexed_index_mode, transparent_rgb=transparent_rgb, transparent_png_index=transparent_png_index)
        alphas = png_pixel_alpha_list(png)

        visible_mask = forced_alpha_mask_from_png(
            png,
            alpha_threshold,
            transparent_rgb,
            transparent_png_index,
        )

        if len(indices) != pixel_count or len(visible_mask) != pixel_count:
            raise ValueError(f"{png}: internal pixel count mismatch")

        for i, (idx, is_visible) in enumerate(zip(indices, visible_mask)):
            if not is_visible:
                continue

            if owner[i] is None:
                merged[i] = idx
                owner[i] = png
                continue

            if merged[i] == idx:
                continue

            if overlap_policy == "error":
                x = i % info.pixel_width
                y = i // info.pixel_width
                raise ValueError(
                    f"Visible-page overlap conflict at ({x},{y}): "
                    f"{owner[i]} wants TIM index {merged[i]}, {png} wants TIM index {idx}. "
                    f"Use --overlap-policy first/last/nonzero if intentional."
                )
            if overlap_policy == "first":
                continue
            if overlap_policy == "last":
                merged[i] = idx
                owner[i] = png
                continue
            if overlap_policy == "nonzero":
                if merged[i] == bg or merged[i] == 0:
                    merged[i] = idx
                    owner[i] = png
                continue
            raise ValueError(f"Unknown overlap policy: {overlap_policy}")

    return merged


def edited_png_rgba_for_compare(
    png_path: Path,
    transparent_rgb: Optional[tuple[int, int, int]],
    transparent_png_index: Optional[int],
) -> list[tuple[int, int, int, int]]:
    """Return edited PNG pixels as RGBA, with forced transparency applied."""
    patched = patch_rgba_to_transparent_before_mapping(png_path, transparent_rgb, transparent_png_index)
    if patched is not None:
        return list(patched.getdata())
    return list(Image.open(png_path).convert("RGBA").getdata())


def pixels_differ_for_tim_compare(
    a: tuple[int, int, int, int],
    b: tuple[int, int, int, int],
    alpha_threshold: int,
) -> bool:
    """Compare two RGBA pixels using TIM/PS1-ish quantization.

    This avoids tiny PNG/editor color differences from creating unnecessary
    writes, while still detecting real transparency/shape/color edits.
    """
    a_visible = a[3] > alpha_threshold
    b_visible = b[3] > alpha_threshold
    if a_visible != b_visible:
        return True
    if not a_visible and not b_visible:
        return False
    return quantized_rgba_key(a) != quantized_rgba_key(b)


def merge_changed_page_pngs_to_indices(
    info: TIMInfo,
    page_pngs: list[tuple[Path, int]],
    nearest: bool,
    indexed_index_mode: str,
    alpha_threshold: int,
    transparent_index: Optional[int],
    overlap_policy: str,
    transparent_rgb: Optional[tuple[int, int, int]] = None,
    transparent_png_index: Optional[int] = None,
) -> list[int]:
    """Merge only CHANGED pixels from page PNGs into the shared TIM PXL plane.

    This is usually the safest workflow for font TIMs.

    Why:
      Multi-CLUT font TIMs often show overlapping glyph shapes on every page.
      Whole-page merging will conflict because page 0 and page 1 both have
      visible pixels at the same coordinates, even though most of those pixels
      are unchanged. Delta merge starts from the original PXL indices and only
      applies pixels that differ from the original render of that same page.

    Behavior:
      - Start with the original TIM indices.
      - Render each original page from the TIM.
      - Compare edited page PNG against that original page.
      - Only changed pixels are converted back to TIM indices and written.
      - If multiple edited pages changed the same coordinate differently, use
        --overlap-policy to decide what happens.
    """
    expected_size = (info.pixel_width, info.pixel_height)
    pixel_count = info.pixel_width * info.pixel_height
    bg = common_transparent_index(info) if transparent_index is None else transparent_index

    merged = read_indices(info)
    changed_by: list[Optional[Path]] = [None] * pixel_count

    for png, page in page_pngs:
        im = Image.open(png)
        if im.size != expected_size:
            raise ValueError(f"{png}: PNG size {im.size} does not match TIM size {expected_size}")

        original_rgba = list(make_indexed_rgba_preview(info, page).getdata())
        edited_rgba = edited_png_rgba_for_compare(png, transparent_rgb, transparent_png_index)

        if len(original_rgba) != pixel_count or len(edited_rgba) != pixel_count:
            raise ValueError(f"{png}: internal pixel count mismatch")

        raw_pal = get_page_raw_palette(info, page)

        changed_count = 0
        for i, (old_px, new_px) in enumerate(zip(original_rgba, edited_rgba)):
            if not pixels_differ_for_tim_compare(old_px, new_px, alpha_threshold):
                continue

            changed_count += 1

            if new_px[3] <= alpha_threshold:
                new_idx = bg
            else:
                new_idx = palette_index_from_rgba(new_px, raw_pal, nearest)

            if changed_by[i] is None:
                merged[i] = new_idx
                changed_by[i] = png
                continue

            if merged[i] == new_idx:
                continue

            if overlap_policy == "error":
                x = i % info.pixel_width
                y = i // info.pixel_width
                raise ValueError(
                    f"Changed-page overlap conflict at ({x},{y}): "
                    f"{changed_by[i]} wants TIM index {merged[i]}, {png} wants TIM index {new_idx}. "
                    f"Use --overlap-policy first/last/nonzero if intentional."
                )
            if overlap_policy == "first":
                continue
            if overlap_policy == "last":
                merged[i] = new_idx
                changed_by[i] = png
                continue
            if overlap_policy == "nonzero":
                if merged[i] == bg or merged[i] == 0:
                    merged[i] = new_idx
                    changed_by[i] = png
                continue
            raise ValueError(f"Unknown overlap policy: {overlap_policy}")

        print(f"{png}: applied {changed_count} changed pixel(s) from page {page}.")

    return merged


def alpha_visible_mask_for_page_png(
    png_path: Path,
    alpha_threshold: int,
    transparent_rgb: Optional[tuple[int, int, int]],
    transparent_png_index: Optional[int],
) -> list[bool]:
    """Return True for pixels the edited PNG considers visible."""
    return forced_alpha_mask_from_png(
        png_path,
        alpha_threshold,
        transparent_rgb,
        transparent_png_index,
    )


def merge_changed_page_pngs_to_indices_by_indexdiff(
    info: TIMInfo,
    page_pngs: list[tuple[Path, int]],
    nearest: bool,
    indexed_index_mode: str,
    alpha_threshold: int,
    transparent_index: Optional[int],
    overlap_policy: str,
    transparent_rgb: Optional[tuple[int, int, int]] = None,
    transparent_png_index: Optional[int] = None,
) -> list[int]:
    """Merge changed pixels using TIM index differences, not RGBA differences.

    This is safer for edited indexed PNGs from GIMP/Aseprite/etc.
    """
    expected_size = (info.pixel_width, info.pixel_height)
    pixel_count = info.pixel_width * info.pixel_height
    bg = common_transparent_index(info) if transparent_index is None else transparent_index

    original_indices = read_indices(info)
    merged = list(original_indices)
    changed_by: list[Optional[Path]] = [None] * pixel_count

    for png, page in page_pngs:
        im = Image.open(png)
        if im.size != expected_size:
            raise ValueError(f"{png}: PNG size {im.size} does not match TIM size {expected_size}")

        mapped_indices = indices_from_png(
            info,
            png,
            page,
            nearest,
            indexed_index_mode,
            transparent_rgb=transparent_rgb,
            transparent_png_index=transparent_png_index,
        )
        visible_mask = alpha_visible_mask_for_page_png(
            png,
            alpha_threshold,
            transparent_rgb,
            transparent_png_index,
        )

        if len(mapped_indices) != pixel_count or len(visible_mask) != pixel_count:
            raise ValueError(f"{png}: internal pixel count mismatch")

        changed_count = 0
        skipped_same_count = 0

        for i, (mapped_idx, is_visible) in enumerate(zip(mapped_indices, visible_mask)):
            new_idx = mapped_idx if is_visible else bg

            if new_idx == original_indices[i]:
                skipped_same_count += 1
                continue

            changed_count += 1

            if changed_by[i] is None:
                merged[i] = new_idx
                changed_by[i] = png
                continue

            if merged[i] == new_idx:
                continue

            if overlap_policy == "error":
                x = i % info.pixel_width
                y = i // info.pixel_width
                raise ValueError(
                    f"Changed-page overlap conflict at ({x},{y}): "
                    f"{changed_by[i]} wants TIM index {merged[i]}, {png} wants TIM index {new_idx}. "
                    f"Use --overlap-policy first/last/nonzero if intentional."
                )
            if overlap_policy == "first":
                continue
            if overlap_policy == "last":
                merged[i] = new_idx
                changed_by[i] = png
                continue
            if overlap_policy == "nonzero":
                if merged[i] == bg or merged[i] == 0:
                    merged[i] = new_idx
                    changed_by[i] = png
                continue
            raise ValueError(f"Unknown overlap policy: {overlap_policy}")

        print(
            f"{png}: applied {changed_count} changed TIM-index pixel(s) from page {page}; "
            f"{skipped_same_count} pixel(s) matched original index."
        )

    return merged


def find_baseline_png_for_edit(edited_png: Path, baseline_dir: Path) -> Path:
    """Find the matching baseline/original PNG for an edited PNG."""
    candidate = baseline_dir / edited_png.name
    if candidate.is_file():
        return candidate

    # Fall back to page number matching if the directory/prefix changed.
    page = png_page_from_metadata_or_name(edited_png)
    if page is not None:
        matches = sorted(baseline_dir.glob(f"*page{page:02d}*.png"))
        if matches:
            return matches[0]
        matches = sorted(baseline_dir.glob(f"*page{page}*.png"))
        if matches:
            return matches[0]

    raise FileNotFoundError(f"Could not find baseline PNG for {edited_png} in {baseline_dir}")


def page_png_to_tim_indices_and_visibility(
    info: TIMInfo,
    png_path: Path,
    page: int,
    nearest: bool,
    indexed_index_mode: str,
    alpha_threshold: int,
    transparent_rgb: Optional[tuple[int, int, int]],
    transparent_png_index: Optional[int],
) -> tuple[list[int], list[bool]]:
    indices = indices_from_png(
        info,
        png_path,
        page,
        nearest,
        indexed_index_mode,
        transparent_rgb=transparent_rgb,
        transparent_png_index=transparent_png_index,
    )
    visible = forced_alpha_mask_from_png(
        png_path,
        alpha_threshold,
        transparent_rgb,
        transparent_png_index,
    )
    return indices, visible


def merge_baseline_changed_page_pngs_to_indices(
    info: TIMInfo,
    page_pngs: list[tuple[Path, int]],
    baseline_dir: Path,
    nearest: bool,
    indexed_index_mode: str,
    alpha_threshold: int,
    transparent_index: Optional[int],
    overlap_policy: str,
    transparent_rgb: Optional[tuple[int, int, int]] = None,
    transparent_png_index: Optional[int] = None,
) -> list[int]:
    """Merge only pixels that changed versus a clean baseline PNG extraction.

    This is the safest mode for font TIM editing.

    Why this exists:
      Some image editors alter indexed PNG palette metadata, transparency, or
      presentation even for untouched pixels. Comparing edited PNGs directly to
      the TIM render can produce false deltas. Comparing edited PNGs to a clean
      baseline extraction of the same page avoids touching untouched glyphs.

    Required workflow:
      1. Extract original TIM to a baseline folder.
      2. Copy that folder and edit the copy in GIMP.
      3. Inject with:
           --multi-page-mode merge-baseline-changes
           --baseline-dir original_extracted_folder

    For each page:
      - Convert baseline PNG to TIM indices/visibility.
      - Convert edited PNG to TIM indices/visibility.
      - Only if edited differs from baseline at a pixel do we update the shared
        TIM PXL index at that coordinate.
    """
    expected_size = (info.pixel_width, info.pixel_height)
    pixel_count = info.pixel_width * info.pixel_height
    bg = common_transparent_index(info) if transparent_index is None else transparent_index

    merged = read_indices(info)
    changed_by: list[Optional[Path]] = [None] * pixel_count

    for edited_png, page in page_pngs:
        baseline_png = find_baseline_png_for_edit(edited_png, baseline_dir)

        if Image.open(edited_png).size != expected_size:
            raise ValueError(f"{edited_png}: PNG size does not match TIM size {expected_size}")
        if Image.open(baseline_png).size != expected_size:
            raise ValueError(f"{baseline_png}: baseline PNG size does not match TIM size {expected_size}")

        base_indices, base_visible = page_png_to_tim_indices_and_visibility(
            info,
            baseline_png,
            page,
            nearest,
            indexed_index_mode,
            alpha_threshold,
            transparent_rgb,
            transparent_png_index,
        )
        edit_indices, edit_visible = page_png_to_tim_indices_and_visibility(
            info,
            edited_png,
            page,
            nearest,
            indexed_index_mode,
            alpha_threshold,
            transparent_rgb,
            transparent_png_index,
        )

        if (
            len(base_indices) != pixel_count or len(edit_indices) != pixel_count
            or len(base_visible) != pixel_count or len(edit_visible) != pixel_count
        ):
            raise ValueError(f"{edited_png}: internal pixel count mismatch")

        changed_count = 0

        for i in range(pixel_count):
            # No actual page-level change, so do not touch shared TIM pixel data.
            if base_visible[i] == edit_visible[i] and base_indices[i] == edit_indices[i]:
                continue

            changed_count += 1
            new_idx = edit_indices[i] if edit_visible[i] else bg

            if changed_by[i] is None:
                merged[i] = new_idx
                changed_by[i] = edited_png
                continue

            if merged[i] == new_idx:
                continue

            if overlap_policy == "error":
                x = i % info.pixel_width
                y = i // info.pixel_width
                raise ValueError(
                    f"Baseline-change overlap conflict at ({x},{y}): "
                    f"{changed_by[i]} wants TIM index {merged[i]}, {edited_png} wants TIM index {new_idx}. "
                    f"Use --overlap-policy first/last/nonzero only if intentional."
                )
            if overlap_policy == "first":
                continue
            if overlap_policy == "last":
                merged[i] = new_idx
                changed_by[i] = edited_png
                continue
            if overlap_policy == "nonzero":
                if merged[i] == bg or merged[i] == 0:
                    merged[i] = new_idx
                    changed_by[i] = edited_png
                continue
            raise ValueError(f"Unknown overlap policy: {overlap_policy}")

        print(f"{edited_png}: applied {changed_count} baseline-detected changed pixel(s) from page {page} using {baseline_png}.")

    return merged

def make_png_metadata(info: TIMInfo, page: int) -> PngImagePlugin.PngInfo:
    meta = PngImagePlugin.PngInfo()
    meta.add_text("TIM_TOOL", "pixygarden_tim_roundtrip")
    meta.add_text("TIM_SOURCE", str(info.path))
    meta.add_text("TIM_OFFSET", f"0x{info.tim_offset:X}")
    meta.add_text("TIM_PMD", str(info.effective_pmd))
    meta.add_text("TIM_BPP", str(info.bpp))
    meta.add_text("TIM_PAGE", str(page))
    meta.add_text("TIM_PAGE_COUNT", str(info.page_count))
    meta.add_text("TIM_PALETTE_SIZE", str(info.palette_size))
    meta.add_text("TIM_WIDTH", str(info.pixel_width))
    meta.add_text("TIM_HEIGHT", str(info.pixel_height))
    meta.add_text("TIM_PIXEL_DATA_OFFSET", f"0x{info.pixel_data_offset:X}")
    if info.clut_data_offset is not None:
        meta.add_text("TIM_CLUT_DATA_OFFSET", f"0x{info.clut_data_offset:X}")
    return meta


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def apply_common_overrides(args) -> dict:
    return {
        "tim_offset": args.tim_offset,
        "force_bpp": args.force_bpp,
        "force_width": args.force_width,
        "force_height": args.force_height,
        "force_pixel_offset": args.force_pixel_offset,
        "force_clut_data_offset": args.force_clut_data_offset,
        "force_palette_size": args.force_palette_size,
        "force_page_count": args.force_page_count,
    }


def cmd_info(args) -> None:
    info = parse_tim(Path(args.tim), **apply_common_overrides(args))

    print(f"TIM file:             {info.path}")
    print(f"TIM offset:           0x{info.tim_offset:X}")
    print(f"Magic:                0x{info.magic:08X}")
    print(f"Flags:                0x{info.flags:08X}")
    print(f"PMD/BPP:              {info.effective_pmd} / {info.bpp}bpp")
    print(f"Has CLUT:             {info.has_clut}")
    print(f"Image block offset:   0x{info.pxl.block_offset:X}")
    print(f"Pixel data offset:    0x{info.pixel_data_offset:X}")
    print(f"Image block bnum:     0x{info.pxl.bnum:X}")
    print(f"Stored W words x H:   0x{info.pxl.w_words:X} x 0x{info.pxl.h:X}")
    print(f"Normal W x H:         {info.pixel_width} x {info.pixel_height}")

    if info.clut:
        print(f"CLUT block offset:    0x{info.clut.block_offset:X}")
        print(f"CLUT data offset:     0x{info.clut_data_offset:X}")
        print(f"CLUT block bnum:      0x{info.clut.bnum:X}")
        print(f"CLUT W x H:           0x{info.clut.w:X} x 0x{info.clut.h:X}")
        print(f"Total CLUT colors:    {len(info.clut.raw_colors)}")
        print(f"Palette size/page:    {info.palette_size}")
        print(f"Page count:           {info.page_count}")

    if args.palette_csv and info.clut:
        out = Path(args.palette_csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        fields = ["page", "entry", "raw_hex", "r", "g", "b", "a", "file_offset"]
        with out.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for page in range(info.page_count):
                for entry, raw in enumerate(get_page_raw_palette(info, page)):
                    r, g, b, a = ps1_5551_to_rgba(raw)
                    file_off = (info.clut_data_offset or 0) + (page * info.palette_size + entry) * 2
                    w.writerow({
                        "page": page,
                        "entry": entry,
                        "raw_hex": f"0x{raw:04X}",
                        "r": r,
                        "g": g,
                        "b": b,
                        "a": a,
                        "file_offset": f"0x{file_off:X}",
                    })
        print(f"Wrote palette CSV:    {out}")


def cmd_extract(args) -> None:
    info = parse_tim(Path(args.tim), **apply_common_overrides(args))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    prefix = args.prefix if args.prefix else Path(args.tim).stem

    if info.effective_pmd in (PMD_4BPP, PMD_8BPP):
        if not info.clut:
            raise ValueError("Indexed TIM has no CLUT block")
        for page in range(info.page_count):
            im = make_indexed_png(info, page) if args.png_mode == "indexed" else make_indexed_rgba_preview(info, page)
            out = out_dir / f"{prefix}_page{page:02d}.png"
            im.save(out, pnginfo=make_png_metadata(info, page))
            print(f"Wrote {out}")
    elif info.effective_pmd in (PMD_16BPP, PMD_24BPP):
        im = read_direct_rgba(info, args.direct_order)
        out = out_dir / f"{prefix}_direct.png"
        im.save(out, pnginfo=make_png_metadata(info, 0))
        print(f"Wrote {out}")
    else:
        raise ValueError(f"Unsupported PMD {info.effective_pmd}")


def collect_pngs(args) -> list[tuple[Path, Optional[int]]]:
    pngs: list[tuple[Path, Optional[int]]] = []

    if args.png:
        p = Path(args.png)
        page = args.page if args.page is not None else png_page_from_metadata_or_name(p)
        pngs.append((p, page))

    if args.png_dir:
        root = Path(args.png_dir)
        found = sorted(root.glob(args.glob))
        if not found:
            raise ValueError(f"No PNGs matched {root / args.glob}")
        for p in found:
            page = png_page_from_metadata_or_name(p)
            pngs.append((p, page))

    if not pngs:
        raise ValueError("Provide --png or --png-dir")

    return pngs



def parse_rgb_triplet(value: Optional[str]) -> Optional[tuple[int, int, int]]:
    if value is None:
        return None
    parts = [p.strip() for p in value.split(",")]
    if len(parts) != 3:
        raise ValueError("--transparent-rgb must be R,G,B, e.g. 0,0,0")
    vals = tuple(int(p, 0) for p in parts)
    if any(v < 0 or v > 255 for v in vals):
        raise ValueError("--transparent-rgb values must be in 0..255")
    return vals  # type: ignore[return-value]


def png_pixel_rgba_list(png_path: Path) -> list[tuple[int, int, int, int]]:
    return list(Image.open(png_path).convert("RGBA").getdata())


def forced_alpha_mask_from_png(
    png_path: Path,
    alpha_threshold: int,
    transparent_rgb: Optional[tuple[int, int, int]],
    transparent_png_index: Optional[int],
) -> list[bool]:
    """Return True for visible pixels after user-forced transparency rules."""
    im = Image.open(png_path)
    visible: list[bool] = []

    if im.mode == "P" and transparent_png_index is not None:
        trans_index = int(transparent_png_index)
        # Use the original indexed data so we can ignore a palette index even
        # if the PNG's tRNS alpha was lost or changed by the editor.
        for idx in im.getdata():
            if int(idx) == trans_index:
                visible.append(False)
            else:
                # Fall through to alpha/RGB check via converted pixel below is
                # slower to mix here, so handle alpha separately if possible.
                visible.append(True)
        # If no RGB override is requested, this mask is enough except for alpha.
        if transparent_rgb is None:
            # Apply PNG alpha/tRNS if present too.
            alphas = png_pixel_alpha_list(png_path)
            return [v and (a > alpha_threshold) for v, a in zip(visible, alphas)]

    rgba_pixels = png_pixel_rgba_list(png_path)
    visible = []
    for r, g, b, a in rgba_pixels:
        if a <= alpha_threshold:
            visible.append(False)
            continue
        if transparent_rgb is not None and (r, g, b) == transparent_rgb:
            visible.append(False)
            continue
        visible.append(True)
    return visible


def patch_rgba_to_transparent_before_mapping(
    png_path: Path,
    transparent_rgb: Optional[tuple[int, int, int]],
    transparent_png_index: Optional[int],
) -> Optional[Image.Image]:
    """Create a temporary RGBA image with forced transparent pixels.

    This is used only for color-to-index mapping. It does not modify the PNG.
    """
    if transparent_rgb is None and transparent_png_index is None:
        return None

    im = Image.open(png_path)
    rgba = im.convert("RGBA")
    pix = list(rgba.getdata())

    if transparent_png_index is not None and im.mode == "P":
        raw_idx = list(im.getdata())
        for i, idx in enumerate(raw_idx):
            if int(idx) == int(transparent_png_index):
                r, g, b, _a = pix[i]
                pix[i] = (r, g, b, 0)

    if transparent_rgb is not None:
        tr, tg, tb = transparent_rgb
        for i, (r, g, b, a) in enumerate(pix):
            if (r, g, b) == (tr, tg, tb):
                pix[i] = (r, g, b, 0)

    rgba.putdata(pix)
    return rgba


def cmd_inject(args) -> None:
    info = parse_tim(Path(args.tim), **apply_common_overrides(args))
    pngs = collect_pngs(args)
    transparent_rgb = parse_rgb_triplet(args.transparent_rgb)

    if info.effective_pmd in (PMD_4BPP, PMD_8BPP):
        # Resolve page numbers first.
        resolved_pngs: list[tuple[Path, int]] = []
        for png, page in pngs:
            if page is None:
                if len(pngs) == 1:
                    page = 0
                else:
                    raise ValueError(f"Could not determine CLUT page for {png}; use --page for single PNG or keep pageNN in filename")
            if page < 0 or page >= info.page_count:
                raise ValueError(f"{png}: page {page} outside valid range 0..{info.page_count - 1}")
            resolved_pngs.append((png, page))

        # Optionally update CLUT pages from matching indexed PNG palettes.
        if args.update_clut_from_png_palette:
            for png, page in resolved_pngs:
                raw_pal = png_palette_to_raw_colors(png, info, page, args.stp_policy)
                if raw_pal is not None:
                    set_page_raw_palette(info, page, raw_pal)
                    print(f"Updated CLUT page {page} from {png}")

        if args.multi_page_mode == "merge-visible":
            transparent_index = args.transparent_index
            chosen_indices = merge_visible_page_pngs_to_indices(
                info,
                resolved_pngs,
                nearest=args.nearest,
                indexed_index_mode=args.indexed_index_mode,
                alpha_threshold=args.alpha_threshold,
                transparent_index=transparent_index,
                overlap_policy=args.overlap_policy,
                transparent_rgb=transparent_rgb,
                transparent_png_index=args.transparent_png_index,
            )
            print(f"Merged visible pixels from {len(resolved_pngs)} page PNG(s) into one shared PXL plane.")

        elif args.multi_page_mode == "merge-changes":
            transparent_index = args.transparent_index
            if args.delta_compare == "index":
                chosen_indices = merge_changed_page_pngs_to_indices_by_indexdiff(
                    info,
                    resolved_pngs,
                    nearest=args.nearest,
                    indexed_index_mode=args.indexed_index_mode,
                    alpha_threshold=args.alpha_threshold,
                    transparent_index=transparent_index,
                    overlap_policy=args.overlap_policy,
                    transparent_rgb=transparent_rgb,
                    transparent_png_index=args.transparent_png_index,
                )
                print(f"Merged changed TIM-index pixels from {len(resolved_pngs)} page PNG(s) into one shared PXL plane.")
            else:
                chosen_indices = merge_changed_page_pngs_to_indices(
                    info,
                    resolved_pngs,
                    nearest=args.nearest,
                    indexed_index_mode=args.indexed_index_mode,
                    alpha_threshold=args.alpha_threshold,
                    transparent_index=transparent_index,
                    overlap_policy=args.overlap_policy,
                    transparent_rgb=transparent_rgb,
                    transparent_png_index=args.transparent_png_index,
                )
                print(f"Merged changed RGBA-render pixels from {len(resolved_pngs)} page PNG(s) into one shared PXL plane.")

        elif args.multi_page_mode == "merge-baseline-changes":
            if not args.baseline_dir:
                raise ValueError("--baseline-dir is required with --multi-page-mode merge-baseline-changes")
            transparent_index = args.transparent_index
            chosen_indices = merge_baseline_changed_page_pngs_to_indices(
                info,
                resolved_pngs,
                baseline_dir=Path(args.baseline_dir),
                nearest=args.nearest,
                indexed_index_mode=args.indexed_index_mode,
                alpha_threshold=args.alpha_threshold,
                transparent_index=transparent_index,
                overlap_policy=args.overlap_policy,
                transparent_rgb=transparent_rgb,
                transparent_png_index=args.transparent_png_index,
            )
            print(f"Merged baseline-detected changed pixels from {len(resolved_pngs)} page PNG(s) into one shared PXL plane.")

        else:
            chosen_indices: Optional[list[int]] = None
            chosen_source: Optional[Path] = None

            for png, page in resolved_pngs:
                indices = indices_from_png(info, png, page, args.nearest, args.indexed_index_mode, transparent_rgb=transparent_rgb, transparent_png_index=args.transparent_png_index)

                use_as_pixel_source = (
                    args.pixel_source_page is None
                    or page == args.pixel_source_page
                )

                if not use_as_pixel_source:
                    continue

                if chosen_indices is None:
                    chosen_indices = indices
                    chosen_source = png
                else:
                    if indices != chosen_indices:
                        if args.pixel_conflict == "error":
                            raise ValueError(
                                f"Pixel-index conflict: {png} differs from {chosen_source}. "
                                f"Indexed TIM pages share one pixel plane. Use --multi-page-mode merge-visible "
                                f"for CLUT pages that represent separate visible layers, or use --pixel-source-page N "
                                f"to choose one page as the source."
                            )
                        elif args.pixel_conflict == "last":
                            chosen_indices = indices
                            chosen_source = png
                        elif args.pixel_conflict == "first":
                            pass
                        else:
                            raise ValueError(args.pixel_conflict)

            if chosen_indices is None:
                raise ValueError("No PNG selected as pixel source. Check --pixel-source-page.")

            print(f"Updated PXL indices from {chosen_source}")

        packed = pack_indices(info, chosen_indices)
        if len(packed) > info.pxl.data_size:
            raise ValueError(f"Packed pixel data 0x{len(packed):X} exceeds original PXL data size 0x{info.pxl.data_size:X}")

        off = info.pixel_data_offset
        info.data[off:off + len(packed)] = packed
    elif info.effective_pmd in (PMD_16BPP, PMD_24BPP):
        if len(pngs) != 1:
            raise ValueError("Direct-color TIM injection expects exactly one PNG")
        write_direct_rgba(info, pngs[0][0], args.direct_order, args.stp_policy)
        print(f"Updated direct-color pixels from {pngs[0][0]}")

    else:
        raise ValueError(f"Unsupported PMD {info.effective_pmd}")

    out = Path(args.out)
    out.write_bytes(info.data)
    print(f"Wrote patched TIM/file: {out}")


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tim", required=True, help="TIM file, or container file with TIM at --tim-offset")
    parser.add_argument("--tim-offset", type=lambda x: int(x, 0), default=0, help="Offset of TIM inside file. Default 0.")
    parser.add_argument("--force-bpp", type=int, choices=[4, 8, 16, 24], help="Override TIM BPP interpretation.")
    parser.add_argument("--force-width", type=int, help="Override normal pixel width.")
    parser.add_argument("--force-height", type=int, help="Override normal pixel height.")
    parser.add_argument("--force-pixel-offset", type=lambda x: int(x, 0), help="Override absolute pixel-data file offset.")
    parser.add_argument("--force-clut-data-offset", type=lambda x: int(x, 0), help="Override absolute CLUT color-data file offset.")
    parser.add_argument("--force-palette-size", type=int, help="Override colors per CLUT page. Usually 16 for 4bpp, 256 for 8bpp.")
    parser.add_argument("--force-page-count", type=int, help="Override number of CLUT pages.")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Extract and inject PlayStation TIM image/CLUT pages as PNGs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_info = sub.add_parser("info", help="Print TIM structure information")
    add_common_args(p_info)
    p_info.add_argument("--palette-csv", help="Optional CSV dump of CLUT colors.")
    p_info.set_defaults(func=cmd_info)

    p_extract = sub.add_parser("extract", help="Extract all TIM image pages as PNG")
    add_common_args(p_extract)
    p_extract.add_argument("--out-dir", required=True)
    p_extract.add_argument("--prefix", help="Output PNG prefix. Default: TIM filename stem.")
    p_extract.add_argument("--png-mode", choices=["indexed", "rgba"], default="indexed",
                           help="indexed preserves palette indices; rgba is easier to preview/edit. Default indexed.")
    p_extract.add_argument("--direct-order", choices=["rgb", "bgr"], default="rgb",
                           help="Byte order for 24bpp direct TIMs. Default rgb.")
    p_extract.set_defaults(func=cmd_extract)

    p_inject = sub.add_parser("inject", help="Inject edited PNGs back into a TIM")
    add_common_args(p_inject)
    p_inject.add_argument("--out", required=True, help="Output patched TIM/file")
    src = p_inject.add_mutually_exclusive_group(required=True)
    src.add_argument("--png", help="Single PNG to inject")
    src.add_argument("--png-dir", help="Directory of PNGs to inject")
    p_inject.add_argument("--glob", default="*.png", help="PNG glob for --png-dir. Default *.png")
    p_inject.add_argument("--page", type=int, help="CLUT page for a single PNG if metadata/filename does not identify it.")
    p_inject.add_argument("--nearest", action="store_true", help="For RGBA/changed-palette PNGs, map non-exact colors to nearest CLUT color.")
    p_inject.add_argument("--indexed-index-mode", choices=["auto", "raw", "match-palette"], default="auto",
                          help="How to interpret P-mode indexed PNGs. auto remaps by palette color if the PNG palette order changed; raw trusts PNG index numbers exactly; match-palette always maps PNG palette colors back to TIM indices. Default auto.")
    p_inject.add_argument("--update-clut-from-png-palette", action=argparse.BooleanOptionalAction, default=False,
                          help="For indexed PNGs, update the TIM CLUT page from the PNG palette. Default disabled/preserve original CLUTs.")
    p_inject.add_argument("--stp-policy", choices=["preserve", "clear", "set", "black-opaque"], default="preserve",
                          help="How to set PS1 STP bit when writing CLUT/direct 16bpp colors. Default preserve.")
    p_inject.add_argument("--pixel-source-page", type=int,
                          help="For multi-page injection, only use this page's PNG as the shared pixel-index source.")
    p_inject.add_argument("--pixel-conflict", choices=["error", "first", "last"], default="error",
                          help="What to do if multiple injected pages have different pixel indices. Default error.")
    p_inject.add_argument("--multi-page-mode", choices=["single-source", "merge-visible", "merge-changes", "merge-baseline-changes"], default="single-source",
                          help="single-source uses one shared pixel source. merge-visible merges every visible pixel from each CLUT page. merge-changes compares against the original TIM. merge-baseline-changes compares edited PNGs against a clean baseline extraction and is safest for font edits.")
    p_inject.add_argument("--baseline-dir",
                          help="Clean original PNG extraction folder used by --multi-page-mode merge-baseline-changes.")
    p_inject.add_argument("--delta-compare", choices=["index", "rgba"], default="index",
                          help="For --multi-page-mode merge-changes, choose how changes are detected. index compares mapped TIM indices to the original shared PXL indices and is safest for indexed font PNGs. rgba compares rendered colors and is older behavior. Default index.")
    p_inject.add_argument("--alpha-threshold", type=int, default=0,
                          help="For --multi-page-mode merge-visible, pixels with alpha <= this are treated as transparent. Default 0.")
    p_inject.add_argument("--transparent-index", type=int,
                          help="For merge-visible, background TIM index to use where no page has a visible pixel. Default: first index transparent in all CLUT pages, usually 0.")
    p_inject.add_argument("--transparent-rgb",
                          help="Treat this RGB color as transparent during injection/merge, even if PNG alpha is opaque. Format R,G,B; common value: 0,0,0.")
    p_inject.add_argument("--transparent-png-index", type=int,
                          help="Treat this PNG palette index as transparent during injection/merge, even if PNG alpha/tRNS was lost.")
    p_inject.add_argument("--overlap-policy", choices=["error", "first", "last", "nonzero"], default="error",
                          help="For merge-visible, what to do if multiple page PNGs have visible pixels at the same coordinate but want different TIM indices. Default error.")
    p_inject.add_argument("--direct-order", choices=["rgb", "bgr"], default="rgb",
                          help="Byte order for 24bpp direct TIMs. Default rgb.")
    p_inject.set_defaults(func=cmd_inject)

    args = ap.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
