#!/usr/bin/env python3
"""
pixygarden_lz_tim_tool.py

General PixyGarden LZ compressor/decompressor for compressed TIM/BIN payloads.

Deep-search v3: level 3 uses 2048 candidates instead of 1024, which matches/fits the original NAME/TREE-style streams more reliably.

Originally discovered in PARM.CDF, but the compression format is not PARM-specific.
MAIN.EXE contains the runtime decompressor at file offset 0x508B8 / RAM 0x800700B8.

Commands:
  decode       Decompress one compressed .TIM/.BIN payload directly.
  encode       Compress any decoded file/TIM/BIN directly.
  test         Test whether a file is a valid PixyGarden LZ stream.
  extract-cdf  Parse a simple PixyGarden CDF table and decode members that use this LZ format.
  reinsert-cdf Compress edited decoded files and reinsert them into a simple PixyGarden CDF.

Simple CDF layout supported:
  0x00 uint32 first_data_offset
  0x04 uint32 entry_count
  0x10 repeated 0x20-byte entries:
       0x00..0x0F ASCII name
       0x10 uint32 unknown
       0x14 uint32 start sector
       0x18 uint32 sector count
       0x1C uint32 real member size

If a CDF uses another layout, use direct decode/encode on extracted members.
"""
from __future__ import annotations

import argparse
import csv
import math
import struct
from dataclasses import dataclass
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:  # PNG rendering is optional
    Image = None
    ImageDraw = None

SECTOR = 0x800
CDF_ENTRY_SIZE = 0x20
CDF_TABLE_START = 0x10
WINDOW = 0x4000
MIN_MATCH = 3
MAX_MATCH = 18


class BitReader:
    def __init__(self, data: bytes):
        self.data = data
        self.bitpos = 0
        self.nbits = len(data) * 8

    def getbits(self, n: int) -> int:
        val = 0
        for _ in range(n):
            if self.bitpos >= self.nbits:
                raise EOFError(f"compressed bitstream ended early at bit {self.bitpos}")
            b = self.data[self.bitpos >> 3]
            val = (val << 1) | ((b >> (7 - (self.bitpos & 7))) & 1)
            self.bitpos += 1
        return val


class BitWriter:
    def __init__(self):
        self.out = bytearray()
        self.cur = 0
        self.used = 0

    def putbits(self, val: int, n: int) -> None:
        for shift in range(n - 1, -1, -1):
            bit = (val >> shift) & 1
            self.cur = (self.cur << 1) | bit
            self.used += 1
            if self.used == 8:
                self.out.append(self.cur)
                self.cur = 0
                self.used = 0

    def finish(self, pad_bit: int = 0) -> bytes:
        if self.used:
            while self.used != 8:
                self.cur = (self.cur << 1) | (pad_bit & 1)
                self.used += 1
            self.out.append(self.cur)
            self.cur = 0
            self.used = 0
        return bytes(self.out)


def decode_lz(src: bytes, max_out: int = 64_000_000) -> tuple[bytes, int]:
    """Decode PixyGarden's LZ bitstream.

    Bits are read MSB-first.

    flag bit 1:
      literal byte = next 8 bits

    flag bit 0:
      pos = next 14 bits
      if pos == 0: end stream
      length = next 4 bits + 3
      copy from a 0x4000-byte circular history position (pos - 1)
    """
    br = BitReader(src)
    out = bytearray()

    while True:
        flag = br.getbits(1)
        if flag:
            out.append(br.getbits(8))
        else:
            pos = br.getbits(14)
            if pos == 0:
                break
            length = br.getbits(4) + MIN_MATCH
            idx = pos - 1
            cur = len(out)
            base = cur & ~(WINDOW - 1)
            curmod = cur & (WINDOW - 1)
            src_off = base + idx if idx < curmod else base - WINDOW + idx
            if src_off < 0:
                raise ValueError(
                    f"invalid back-reference: src_off={src_off}, cur={cur}, pos={pos}, length={length}"
                )
            for i in range(length):
                out.append(out[src_off + i])

        if len(out) > max_out:
            raise RuntimeError(f"decoded output exceeded max_out={max_out}")

    return bytes(out), br.bitpos


def match_len_at(data: bytes, cur: int, src: int, max_len: int) -> int:
    """Overlap-safe match length matching decoder byte-at-a-time copy semantics."""
    n = len(data)
    distance = cur - src
    if distance <= 0:
        return 0
    limit = min(MAX_MATCH, max_len, n - cur)
    k = 0
    while k < limit:
        ref_index = src + k
        if ref_index >= cur:
            # Overlap repeats from bytes just produced by the same backref.
            ref_index = cur + (k - distance)
        if data[cur + k] != data[ref_index]:
            break
        k += 1
    return k


def encode_lz(data: bytes, *, level: int = 2) -> bytes:
    """Encode bytes into the PixyGarden LZ bitstream.

    level 1: faster, fewer candidates
    level 2: default balance
    level 3: slower, deeper search (2048 candidates)
    """
    if level not in (1, 2, 3):
        raise ValueError("level must be 1, 2, or 3")
    max_candidates = {1: 64, 2: 256, 3: 2048}[level]

    history: dict[bytes, list[int]] = {}
    bw = BitWriter()
    i = 0
    n = len(data)

    def add_pos(pos: int) -> None:
        if pos + MIN_MATCH > n:
            return
        key = data[pos:pos + MIN_MATCH]
        lst = history.setdefault(key, [])
        lst.append(pos)
        if len(lst) > 4096:
            del lst[:2048]

    while i < n:
        best_len = 0
        best_src = -1

        if i + MIN_MATCH <= n:
            key = data[i:i + MIN_MATCH]
            candidates = history.get(key, [])
            if candidates:
                window_start = max(0, i - WINDOW)
                checked = 0
                for src in reversed(candidates):
                    if src < window_start:
                        break
                    checked += 1
                    ml = match_len_at(data, i, src, MAX_MATCH)
                    if ml > best_len:
                        best_len = ml
                        best_src = src
                        if best_len == MAX_MATCH:
                            break
                    if checked >= max_candidates:
                        break

        if best_len >= MIN_MATCH:
            encoded_pos = (best_src & (WINDOW - 1)) + 1
            bw.putbits(0, 1)
            bw.putbits(encoded_pos, 14)
            bw.putbits(best_len - MIN_MATCH, 4)
            for p in range(i, i + best_len):
                add_pos(p)
            i += best_len
        else:
            bw.putbits(1, 1)
            bw.putbits(data[i], 8)
            add_pos(i)
            i += 1

    # End marker: flag 0 + position 0. No length field follows.
    bw.putbits(0, 1)
    bw.putbits(0, 14)
    return bw.finish(0)


def verify_compressed(comp: bytes, expected: bytes) -> tuple[int, int]:
    dec, bits = decode_lz(comp, max_out=max(len(expected) + 1024, 1024))
    if dec != expected:
        raise RuntimeError(f"compression verification failed: decoded 0x{len(dec):X}, expected 0x{len(expected):X}")
    return len(dec), bits


def is_tim(data: bytes) -> bool:
    return len(data) >= 20 and data[:4] == b"\x10\x00\x00\x00"


def bgr555_to_rgba(c: int) -> tuple[int, int, int, int]:
    r = c & 0x1F
    g = (c >> 5) & 0x1F
    b = (c >> 10) & 0x1F
    a = 0 if c == 0 else 255
    return (r * 255 // 31, g * 255 // 31, b * 255 // 31, a)


def parse_tim(tim: bytes) -> dict:
    if not is_tim(tim):
        raise ValueError("not a standard PlayStation TIM")

    flags = struct.unpack_from("<I", tim, 4)[0]
    bpp = flags & 0x07
    has_clut = bool(flags & 0x08)
    pos = 8
    clut_rows = []
    clut_info = None

    if has_clut:
        if pos + 12 > len(tim):
            raise ValueError("truncated TIM CLUT block")
        block_len, cx, cy, cw, ch = struct.unpack_from("<IHHHH", tim, pos)
        if block_len < 12 or pos + block_len > len(tim):
            raise ValueError("invalid TIM CLUT block length")
        raw = tim[pos + 12:pos + block_len]
        vals = struct.unpack("<" + "H" * (len(raw) // 2), raw)
        for row in range(ch):
            clut_rows.append([bgr555_to_rgba(vals[row * cw + i]) for i in range(cw)])
        clut_info = {"block_len": block_len, "x": cx, "y": cy, "colors_per_row": cw, "rows": ch}
        pos += block_len

    if pos + 12 > len(tim):
        raise ValueError("truncated TIM image block")
    image_block_len, ix, iy, iw_words, ih = struct.unpack_from("<IHHHH", tim, pos)
    if image_block_len < 12 or pos + image_block_len > len(tim):
        raise ValueError("invalid TIM image block length")
    pixel_data = tim[pos + 12:pos + image_block_len]

    return {
        "flags": flags,
        "bpp_code": bpp,
        "has_clut": has_clut,
        "clut_info": clut_info,
        "image_info": {"block_len": image_block_len, "x": ix, "y": iy, "w_words": iw_words, "h": ih},
        "pixel_data": pixel_data,
        "clut_rows": clut_rows,
    }


def tim_to_png(tim: bytes, clut_row: int = 0):
    if Image is None:
        raise RuntimeError("Pillow is required for PNG output. Install with: pip install pillow")

    info = parse_tim(tim)
    bpp = info["bpp_code"]
    pix = info["pixel_data"]
    ii = info["image_info"]
    iw_words = ii["w_words"]
    h = ii["h"]

    if bpp == 0:  # 4bpp
        width = iw_words * 4
        palette = info["clut_rows"][clut_row] if info["clut_rows"] else [(i * 17, i * 17, i * 17, 255) for i in range(16)]
        indices = []
        for byte in pix[:width * h // 2]:
            indices.append(byte & 0x0F)
            indices.append(byte >> 4)
        rgba = [palette[i] if i < len(palette) else (0, 0, 0, 255) for i in indices[:width * h]]
        img = Image.new("RGBA", (width, h))
        img.putdata(rgba)
        return img

    if bpp == 1:  # 8bpp
        width = iw_words * 2
        palette = info["clut_rows"][clut_row] if info["clut_rows"] else [(i, i, i, 255) for i in range(256)]
        rgba = [palette[b] if b < len(palette) else (0, 0, 0, 255) for b in pix[:width * h]]
        img = Image.new("RGBA", (width, h))
        img.putdata(rgba)
        return img

    if bpp == 2:  # 16bpp
        width = iw_words
        count = min(len(pix) // 2, width * h)
        vals = struct.unpack("<" + "H" * count, pix[:count * 2])
        img = Image.new("RGBA", (width, h))
        img.putdata([bgr555_to_rgba(v) for v in vals])
        return img

    raise ValueError(f"unsupported TIM bpp code: {bpp}")


def tim_summary(data: bytes) -> dict:
    info = parse_tim(data)
    ci = info["clut_info"] or {}
    ii = info["image_info"]
    bpp = info["bpp_code"]
    width = ii["w_words"] * 4 if bpp == 0 else ii["w_words"] * 2 if bpp == 1 else ii["w_words"] if bpp == 2 else ""
    return {
        "tim_flags": f"0x{info['flags']:X}",
        "bpp_code": bpp,
        "pixel_width": width,
        "pixel_height": ii["h"],
        "clut_x": ci.get("x", ""),
        "clut_y": ci.get("y", ""),
        "clut_colors": ci.get("colors_per_row", ""),
        "clut_rows": ci.get("rows", ""),
        "image_x": ii["x"],
        "image_y": ii["y"],
        "image_w_words": ii["w_words"],
        "image_h": ii["h"],
    }


@dataclass
class CDFEntry:
    index: int
    name: str
    table_off: int
    unknown: int
    sector: int
    sector_count: int
    file_off: int
    capacity: int
    size: int


def parse_simple_cdf(data: bytes) -> list[CDFEntry]:
    if len(data) < CDF_TABLE_START + CDF_ENTRY_SIZE:
        raise ValueError("file too small for simple CDF table")
    first_data = struct.unpack_from("<I", data, 0x00)[0]
    count = struct.unpack_from("<I", data, 0x04)[0]
    if first_data <= 0 or count <= 0 or count > 10000:
        raise ValueError("does not look like simple PixyGarden CDF table")
    if CDF_TABLE_START + count * CDF_ENTRY_SIZE > len(data):
        raise ValueError("CDF table exceeds file size")

    entries = []
    for i in range(count):
        off = CDF_TABLE_START + i * CDF_ENTRY_SIZE
        raw_name = data[off:off + 0x10]
        name = raw_name.split(b"\0", 1)[0].decode("ascii", errors="replace")
        unk, sector, sector_count, size = struct.unpack_from("<IIII", data, off + 0x10)
        file_off = sector * SECTOR
        capacity = sector_count * SECTOR
        if not name or not all(32 <= ord(ch) <= 126 for ch in name):
            raise ValueError(f"invalid CDF entry name at index {i}: {raw_name!r}")
        if file_off < first_data or file_off + size > len(data):
            raise ValueError(f"CDF entry {name} points outside file")
        if size > capacity:
            raise ValueError(f"CDF entry {name} size exceeds sector capacity")
        entries.append(CDFEntry(i + 1, name, off, unk, sector, sector_count, file_off, capacity, size))
    return entries


def find_entry(entries: list[CDFEntry], selector: str) -> CDFEntry:
    s = str(selector)
    if s.isdigit():
        idx = int(s)
        for e in entries:
            if e.index == idx:
                return e
    upper = s.upper()
    for e in entries:
        if e.name.upper() == upper:
            return e
    raise ValueError(f"CDF entry not found: {selector!r}")


def decode_command(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    raw = input_path.read_bytes()
    decoded, bits = decode_lz(raw, max_out=args.max_out)
    out_path = Path(args.out) if args.out else input_path.with_suffix(input_path.suffix + ".decoded")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(decoded)

    png_path = None
    if args.png or (args.auto_png and is_tim(decoded)):
        png_path = Path(args.png) if args.png else out_path.with_suffix(out_path.suffix + ".png")
        tim_to_png(decoded, args.clut_row).save(png_path)

    print("PixyGarden LZ decode")
    print("--------------------")
    print(f"Input:       {input_path} / 0x{len(raw):X}")
    print(f"Output:      {out_path} / 0x{len(decoded):X}")
    print(f"Bits used:   {bits}")
    print(f"Looks TIM:   {is_tim(decoded)}")
    if is_tim(decoded):
        print(f"TIM summary: {tim_summary(decoded)}")
    if png_path:
        print(f"PNG:         {png_path}")
    return 0


def encode_command(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    src = input_path.read_bytes()
    comp = encode_lz(src, level=args.level)
    verify_compressed(comp, src)
    out_path = Path(args.out) if args.out else input_path.with_suffix(input_path.suffix + ".lz")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(comp)
    print("PixyGarden LZ encode")
    print("--------------------")
    print(f"Input:       {input_path} / 0x{len(src):X}")
    print(f"Output:      {out_path} / 0x{len(comp):X}")
    print(f"Ratio:       {len(comp) / len(src):.3f}" if src else "Ratio:       n/a")
    print("Verified:    decoded compressed output matches input")
    return 0


def test_command(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    raw = input_path.read_bytes()
    try:
        decoded, bits = decode_lz(raw, max_out=args.max_out)
        ok, err = True, ""
    except Exception as exc:
        decoded, bits, ok, err = b"", 0, False, str(exc)

    print("PixyGarden LZ test")
    print("------------------")
    print(f"Input:       {input_path} / 0x{len(raw):X}")
    print(f"Decodable:   {ok}")
    if ok:
        print(f"Decoded:     0x{len(decoded):X}")
        print(f"Bits used:   {bits}")
        print(f"Looks TIM:   {is_tim(decoded)}")
        if is_tim(decoded):
            print(f"TIM summary: {tim_summary(decoded)}")
    else:
        print(f"Error:       {err}")
    return 0 if ok else 1


def extract_cdf_command(args: argparse.Namespace) -> int:
    cdf_path = Path(args.cdf)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = cdf_path.read_bytes()
    entries = parse_simple_cdf(data)
    rows = []
    thumbs = []

    for e in entries:
        raw = data[e.file_off:e.file_off + e.size]
        stem = f"{e.index:02d}_{e.name}"
        raw_path = out_dir / f"{stem}_compressed.bin"
        dec_path = out_dir / f"{stem}_decoded.bin"
        tim_path = out_dir / f"{stem}_decoded.TIM"
        png_path = out_dir / f"{stem}_decoded.png"
        row = {
            "index": e.index,
            "name": e.name,
            "cdf_file_offset": f"0x{e.file_off:X}",
            "compressed_size": f"0x{e.size:X}",
            "sector_capacity": f"0x{e.capacity:X}",
            "status": "",
            "decoded_size": "",
            "bits_used": "",
            "looks_tim": "",
            "decoded_file": "",
            "png": "",
            "notes": "",
        }
        if args.save_compressed:
            raw_path.write_bytes(raw)
        try:
            decoded, bits = decode_lz(raw, max_out=args.max_out)
            row["status"] = "decoded"
            row["decoded_size"] = f"0x{len(decoded):X}"
            row["bits_used"] = bits
            row["looks_tim"] = is_tim(decoded)
            if is_tim(decoded):
                tim_path.write_bytes(decoded)
                row["decoded_file"] = tim_path.name
                if args.png:
                    img = tim_to_png(decoded, args.clut_row)
                    img.save(png_path)
                    row["png"] = png_path.name
                    thumb = img.copy()
                    thumb.thumbnail((144, 144))
                    thumbs.append((e.name, thumb))
            else:
                dec_path.write_bytes(decoded)
                row["decoded_file"] = dec_path.name
        except Exception as exc:
            row["status"] = "not_decoded"
            row["notes"] = str(exc)
            if args.extract_raw_on_fail:
                raw_path.write_bytes(raw)
                row["decoded_file"] = raw_path.name
        rows.append(row)

    manifest_path = Path(args.manifest) if args.manifest else out_dir / "_lz_cdf_extract_manifest.csv"
    with manifest_path.open("w", encoding="utf-8-sig", newline="") as f:
        fields = list(rows[0].keys()) if rows else []
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    if thumbs and args.png and Image is not None:
        cols = args.sheet_cols
        cellw, cellh = 170, 170
        sheet_rows = math.ceil(len(thumbs) / cols)
        sheet = Image.new("RGBA", (cols * cellw, sheet_rows * cellh), (240, 240, 240, 255))
        d = ImageDraw.Draw(sheet)
        for i, (name, thumb) in enumerate(thumbs):
            x = (i % cols) * cellw
            y = (i // cols) * cellh
            sheet.alpha_composite(thumb, (x + (cellw - thumb.width) // 2, y + 22 + (130 - thumb.height) // 2))
            d.text((x + 4, y + 4), name, fill=(0, 0, 0, 255))
        sheet.save(out_dir / "_decoded_tim_contact_sheet.png")

    print("PixyGarden LZ CDF extract")
    print("-------------------------")
    print(f"CDF:       {cdf_path}")
    print(f"Output:    {out_dir}")
    print(f"Entries:   {len(entries)}")
    print(f"Decoded:   {sum(1 for r in rows if r['status'] == 'decoded')}")
    print(f"TIMs:      {sum(1 for r in rows if r['looks_tim'] is True)}")
    print(f"Manifest:  {manifest_path}")
    return 0


def locate_replacement_for_entry(repl_dir: Path, e: CDFEntry) -> Path | None:
    stem = Path(e.name).stem
    candidates = [
        repl_dir / f"{e.index:02d}_{e.name}_decoded.TIM",
        repl_dir / f"{e.index:02d}_{e.name}_decoded.bin",
        repl_dir / f"{e.index:02d}_{e.name}",
        repl_dir / e.name,
        repl_dir / f"{stem}_decoded.TIM",
        repl_dir / f"{stem}_decoded.bin",
        repl_dir / f"{stem}.TIM",
        repl_dir / f"{stem}.BIN",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def reinsert_cdf_command(args: argparse.Namespace) -> int:
    cdf_path = Path(args.cdf)
    out_path = Path(args.out_cdf)
    data = bytearray(cdf_path.read_bytes())
    entries = parse_simple_cdf(data)
    replacements: dict[str, Path] = {}

    if args.replacement_dir:
        repl_dir = Path(args.replacement_dir)
        if not repl_dir.is_dir():
            raise SystemExit(f"replacement directory not found: {repl_dir}")
        for e in entries:
            p = locate_replacement_for_entry(repl_dir, e)
            if p:
                replacements[e.name.upper()] = p

    for item in args.entry or []:
        if "=" not in item:
            raise SystemExit("--entry must be ENTRY=decoded_file, e.g. I01.TIM=edited.TIM or 1=edited.bin")
        key, value = item.split("=", 1)
        e = find_entry(entries, key)
        replacements[e.name.upper()] = Path(value)

    if not replacements:
        raise SystemExit("No replacements found. Use --replacement-dir and/or --entry ENTRY=decoded_file")

    rows = []
    for e in entries:
        repl = replacements.get(e.name.upper())
        if not repl:
            continue
        decoded = repl.read_bytes()
        comp = encode_lz(decoded, level=args.level)
        verify_compressed(comp, decoded)
        if len(comp) > e.capacity:
            raise SystemExit(
                f"ERROR: {e.name} replacement does not fit original sector allocation.\n"
                f"  compressed: 0x{len(comp):X}\n"
                f"  capacity:   0x{e.capacity:X}\n"
                "This tool preserves CDF layout and does not rebuild sectors."
            )
        if not args.dry_run:
            data[e.file_off:e.file_off + len(comp)] = comp
            pad_start = e.file_off + len(comp)
            pad_end = e.file_off + e.capacity
            data[pad_start:pad_end] = bytes([args.pad_byte & 0xFF]) * (pad_end - pad_start)
            struct.pack_into("<I", data, e.table_off + 0x1C, len(comp))
        rows.append({
            "entry": e.name,
            "replacement": str(repl),
            "decoded_size": f"0x{len(decoded):X}",
            "old_compressed_size": f"0x{e.size:X}",
            "new_compressed_size": f"0x{len(comp):X}",
            "capacity": f"0x{e.capacity:X}",
            "delta": len(comp) - e.size,
            "looks_tim": is_tim(decoded),
            "status": "would_patch" if args.dry_run else "patched",
        })

    report_path = Path(args.report) if args.report else out_path.with_suffix(out_path.suffix + ".lz_reinsert_report.csv")
    with report_path.open("w", encoding="utf-8-sig", newline="") as f:
        fields = ["entry", "replacement", "decoded_size", "old_compressed_size", "new_compressed_size", "capacity", "delta", "looks_tim", "status"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    if not args.dry_run:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)

    print("PixyGarden LZ CDF reinsert")
    print("--------------------------")
    print(f"CDF:       {cdf_path}")
    print(f"Output:    {out_path}")
    print(f"Report:    {report_path}")
    print(f"Entries:   {len(rows)}")
    print(f"Dry run:   {args.dry_run}")
    for r in rows:
        print(f"  {r['entry']}: {r['old_compressed_size']} -> {r['new_compressed_size']} / cap {r['capacity']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="General PixyGarden LZ decompression/compression tool for compressed TIM/BIN payloads and simple CDF archives.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    dec = sub.add_parser("decode", help="Decode one compressed TIM/BIN payload directly")
    dec.add_argument("input")
    dec.add_argument("-o", "--out")
    dec.add_argument("--png", help="Also render decoded output as PNG if it is a TIM")
    dec.add_argument("--auto-png", action="store_true", help="Automatically write PNG beside decoded output if decoded output is a TIM")
    dec.add_argument("--clut-row", type=int, default=0)
    dec.add_argument("--max-out", type=int, default=64_000_000)
    dec.set_defaults(func=decode_command)

    enc = sub.add_parser("encode", help="Compress one decoded file/TIM/BIN directly")
    enc.add_argument("input")
    enc.add_argument("-o", "--out")
    enc.add_argument("--level", type=int, default=2, choices=[1, 2, 3])
    enc.set_defaults(func=encode_command)

    tst = sub.add_parser("test", help="Test whether a file is a valid PixyGarden LZ stream")
    tst.add_argument("input")
    tst.add_argument("--max-out", type=int, default=64_000_000)
    tst.set_defaults(func=test_command)

    ex = sub.add_parser("extract-cdf", help="Extract/decode compressed members from a simple PixyGarden CDF")
    ex.add_argument("cdf")
    ex.add_argument("out_dir")
    ex.add_argument("--save-compressed", action="store_true")
    ex.add_argument("--extract-raw-on-fail", action="store_true", help="Save raw member if it does not decode")
    ex.add_argument("--png", action="store_true", default=True, help="Render decoded TIMs to PNG. Default enabled.")
    ex.add_argument("--no-png", dest="png", action="store_false")
    ex.add_argument("--clut-row", type=int, default=0)
    ex.add_argument("--sheet-cols", type=int, default=5)
    ex.add_argument("--manifest")
    ex.add_argument("--max-out", type=int, default=64_000_000)
    ex.set_defaults(func=extract_cdf_command)

    ins = sub.add_parser("reinsert-cdf", help="Compress decoded replacements and reinsert into a simple PixyGarden CDF")
    ins.add_argument("cdf")
    ins.add_argument("out_cdf")
    ins.add_argument("--replacement-dir")
    ins.add_argument("--entry", action="append", default=[], help="ENTRY=decoded_file, e.g. I01.TIM=edited.TIM or 1=edited.bin. Repeatable.")
    ins.add_argument("--level", type=int, default=2, choices=[1, 2, 3])
    ins.add_argument("--pad-byte", type=lambda x: int(x, 0), default=0x00)
    ins.add_argument("--report")
    ins.add_argument("--dry-run", action="store_true")
    ins.set_defaults(func=reinsert_cdf_command)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
