#!/usr/bin/env python3
"""
pixygarden_TREE_CDF_InPlace_NAME_JTEST_Rebuilder_v10.py

In-place TREE.CDF NAME/TIM + JTEST replacement workflow.

This fixes the previous TREE batch-rebuilder problem: the old script rebuilt
nested FAT/CDF containers and could accidentally rewrite unrelated assets such
as DETAILS.FAT/INFO.FAT text. v8/v9/v10 never rebuild archive tables. It starts from
--source-cdf bytes and overwrites only the exact mapped compressed DAT/BIN
payload slots, preserving every other byte of the source CDF.

Manual workflow mirrored per asset:

  python pixygarden_TIM_Tool_v2.py insert SOURCE.TIM EDITED.png -o PATCHED.TIM
  python pixygarden_LZ_TIM_Tool_v3_deep.py encode PATCHED.TIM -o PATCHED.BIN --level 3

Safety:
  - Patched TIM must stay exactly the same byte size as source TIM.
  - Encoded BIN/DAT stream must fit the original archive slot.
  - Smaller streams are padded back to the exact slot size.
  - Output CDF size is exactly the same as source CDF.
  - Only mapped entries are overwritten; no CDF/FAT repack happens.

Typical use:

  python pixygarden_TREE_CDF_InPlace_NAME_JTEST_Rebuilder_v10.py ^
    --source-cdf TREE_working.CDF ^
    --png-dir edited_tree_pngs ^
    --tim-dir decoded_tree_tim ^
    --out-cdf TREE_patched.CDF ^
    --clean-work

If automatic mapping is ambiguous, pass:

  --archive-map-csv tree_archive_map.csv

CSV format:
  stem,archive_path
  N01,DETAILS.FAT/NAME.FAT/N01.DAT
  N02,DETAILS.FAT/NAME.FAT/N02.DAT
  JTEST,DETAILS.FAT/TEST_G.FAT/JTEST.DAT

JTEST support:
  v10 can patch JTEST.DAT alongside N01-N29. Put JTEST_page00.png
  or JTEST.png in --png-dir / --extra-png-dir, and JTEST.TIM in --tim-dir
  / --extra-tim-dir. It maps to DETAILS.FAT/TEST_G.FAT/JTEST.DAT.
"""
from __future__ import annotations

import argparse
import csv
import re
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Iterable

try:
    from PIL import Image
except ImportError as exc:
    raise SystemExit("This script requires Pillow. Install it with: pip install pillow") from exc

SECTOR = 0x800
TOP_ENTRY_SIZE = 0x20
FAT_ENTRY_SIZE = 0x14

HELPER_TIM_NAMES = ["pixygarden_TIM_Tool_v2.py"]
HELPER_LZ_NAMES = ["pixygarden_LZ_TIM_Tool_v3_deep.py", "pixygarden_LZ_TIM_Tool_v3.py", "pixygarden_LZ_TIM_Tool.py", "pixygarden_LZ_TIM_Tool(2).py"]

CANONICAL_ARCHIVE_PATHS: dict[str, str] = {
    **{f"N{i:02d}": f"DETAILS.FAT/NAME.FAT/N{i:02d}.DAT" for i in range(1, 30)},
    "JTEST": "DETAILS.FAT/TEST_G.FAT/JTEST.DAT",
}

@dataclass(frozen=True)
class SourceEntry:
    path: str
    name: str
    abs_off: int
    size: int
    kind: str

@dataclass
class WorkItem:
    stem: str
    png: Path
    source_tim: Path
    prepared_png: Optional[Path]
    patched_tim: Path
    encoded_bin: Path
    archive_path: str
    abs_off: int
    original_size: int


# ---------------- internal PixyGarden LZ encoder ----------------

WINDOW = 0x4000
MIN_MATCH = 3
MAX_MATCH = 18


class LZBitReader:
    def __init__(self, data: bytes):
        self.data = data
        self.bitpos = 0
        self.nbits = len(data) * 8

    def getbits(self, n: int) -> int:
        v = 0
        for _ in range(n):
            if self.bitpos >= self.nbits:
                raise EOFError(f"compressed bitstream ended early at bit {self.bitpos}")
            b = self.data[self.bitpos >> 3]
            v = (v << 1) | ((b >> (7 - (self.bitpos & 7))) & 1)
            self.bitpos += 1
        return v


class LZBitWriter:
    def __init__(self):
        self.out = bytearray()
        self.cur = 0
        self.used = 0

    def putbits(self, val: int, n: int) -> None:
        for i in range(n - 1, -1, -1):
            self.cur = (self.cur << 1) | ((val >> i) & 1)
            self.used += 1
            if self.used == 8:
                self.out.append(self.cur)
                self.cur = 0
                self.used = 0

    def finish(self) -> bytes:
        if self.used:
            self.out.append(self.cur << (8 - self.used))
        return bytes(self.out)


def decode_lz_internal(src: bytes, max_out: int = 64_000_000) -> bytes:
    br = LZBitReader(src)
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
                raise ValueError(f"invalid back-reference: src_off={src_off}, cur={cur}, pos={pos}, len={length}")
            for i in range(length):
                out.append(out[src_off + i])
        if len(out) > max_out:
            raise RuntimeError(f"decoded output exceeded max_out={max_out}")
    return bytes(out)


def lz_match_len_at(data: bytes, cur: int, src: int) -> int:
    n = len(data)
    distance = cur - src
    if distance <= 0:
        return 0
    limit = min(MAX_MATCH, n - cur)
    k = 0
    while k < limit:
        ref_index = src + k
        if ref_index >= cur:
            # Decoder copies byte by byte, so overlap repeats bytes produced by this same match.
            ref_index = cur + (k - distance)
        if data[cur + k] != data[ref_index]:
            break
        k += 1
    return k


def encode_lz_internal_optimal(data: bytes, *, max_candidates: int = 4096) -> bytes:
    """Optimal parse over the matches found by a deep candidate search.

    This fixes two problems seen with older helper encoders on larger TIMs such as
    JTEST: (1) sources whose circular position would encode as 0x4000 are skipped
    because the 14-bit field cannot represent them and pos=0 is the end marker;
    (2) a DP parse is used after match discovery so the stream is smaller than a
    purely greedy parse.
    """
    n = len(data)
    history: dict[bytes, list[int]] = {}
    max_match = [0] * n
    best_src = [-1] * n
    keep_limit = max(4096, max_candidates)
    prune_to = max(2048, keep_limit // 2)

    for i in range(n):
        if i + MIN_MATCH <= n:
            key = data[i:i + MIN_MATCH]
            candidates = history.get(key)
            if candidates:
                window_start = max(0, i - WINDOW)
                checked = 0
                best_len = 0
                bsrc = -1
                for src in reversed(candidates):
                    if src < window_start:
                        break
                    encoded_pos = (src & (WINDOW - 1)) + 1
                    if encoded_pos >= (1 << 14):
                        # Would become pos=0 after 14-bit truncation, i.e. end marker.
                        continue
                    checked += 1
                    ml = lz_match_len_at(data, i, src)
                    if ml > best_len:
                        best_len = ml
                        bsrc = src
                        if ml == MAX_MATCH:
                            break
                    if checked >= max_candidates:
                        break
                max_match[i] = best_len
                best_src[i] = bsrc
            lst = history.setdefault(key, [])
            lst.append(i)
            if len(lst) > keep_limit:
                del lst[:prune_to]

    cost = [0] * (n + MAX_MATCH + 1)
    choice_type = bytearray(n + 1)  # 0 literal, 1 match
    choice_len = bytearray(n + 1)
    for i in range(n - 1, -1, -1):
        best = 9 + cost[i + 1]
        typ = 0
        ln = 1
        mm = max_match[i]
        if mm >= MIN_MATCH:
            for l in range(MIN_MATCH, mm + 1):
                c = 19 + cost[i + l]
                if c < best:
                    best = c
                    typ = 1
                    ln = l
        cost[i] = best
        choice_type[i] = typ
        choice_len[i] = ln

    bw = LZBitWriter()
    i = 0
    while i < n:
        if choice_type[i] == 0:
            bw.putbits(1, 1)
            bw.putbits(data[i], 8)
            i += 1
        else:
            src = best_src[i]
            encoded_pos = (src & (WINDOW - 1)) + 1
            if src < 0 or encoded_pos >= (1 << 14):
                raise RuntimeError(f"bad internal LZ match at 0x{i:X}: src={src}, pos={encoded_pos}")
            bw.putbits(0, 1)
            bw.putbits(encoded_pos, 14)
            bw.putbits(choice_len[i] - MIN_MATCH, 4)
            i += choice_len[i]
    bw.putbits(0, 1)
    bw.putbits(0, 14)
    return bw.finish()


def cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("ascii", errors="replace")


def is_printable_name(name: str) -> bool:
    return bool(name) and all(32 <= ord(ch) <= 126 for ch in name)


def norm_key(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", s).upper()


def strip_page_suffix(stem: str) -> str:
    for pat in (
        r"(?i)(.+?)[_-]page\d{1,3}(?:[_-].*)?$",
        r"(?i)(.+?)[_-]indexed(?:[_-].*)?$",
        r"(?i)(.+?)[_-]clut\d{1,3}(?:[_-].*)?$",
        r"(?i)(.+?)[_-]pal\d{1,3}(?:[_-].*)?$",
        r"(?i)(.+?)[_-]master(?:[_-].*)?$",
    ):
        m = re.match(pat, stem)
        if m:
            return m.group(1)
    return stem


def mkdir_clean(path: Path, clean: bool) -> None:
    if clean and path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def find_helper(explicit: Optional[Path], names: list[str], roots: list[Path], label: str) -> Path:
    if explicit:
        if explicit.is_file():
            return explicit
        raise SystemExit(f"{label} helper not found: {explicit}")
    for root in roots:
        for name in names:
            p = root / name
            if p.is_file():
                return p
    raise SystemExit(
        f"Could not find {label} helper. Tried: {', '.join(names)}\n"
        f"Search roots: {', '.join(str(r) for r in roots)}"
    )


def run(cmd: list[str], *, dry_run: bool) -> None:
    print("$ " + subprocess.list2cmdline(cmd))
    if not dry_run:
        subprocess.run(cmd, check=True)

# ---------------- archive scanner ----------------

def parse_top_entries(data: bytes) -> list[tuple[str, int, int]]:
    if len(data) < 0x10:
        return []
    data_start, count = struct.unpack_from("<II", data, 0)
    if not (0 < data_start <= len(data) + SECTOR and 0 < count <= 10000):
        return []
    candidates = []
    for table_start in (0x10, 0x0A, 0x08, 0x0C):
        if table_start + count * TOP_ENTRY_SIZE > data_start:
            continue
        entries = []
        ok = True
        prev_sector = -1
        score = 0
        for i in range(count):
            off = table_start + i * TOP_ENTRY_SIZE
            name = cstr(data[off:off + 0x14])
            if not is_printable_name(name):
                ok = False; break
            sector, sector_count, size = struct.unpack_from("<III", data, off + 0x14)
            abs_off = sector * SECTOR
            alloc = sector_count * SECTOR
            if sector_count <= 0 or size > alloc or abs_off < data_start or abs_off + size > len(data):
                ok = False; break
            if sector >= prev_sector:
                score += 2
            prev_sector = sector
            entries.append((name, abs_off, size))
        if ok and score >= count:
            candidates.append((table_start, entries))
    for pref in (0x10, 0x0A, 0x08, 0x0C):
        for ts, entries in candidates:
            if ts == pref:
                return entries
    return candidates[0][1] if candidates else []


def parse_fat_entries(seg: bytes) -> list[tuple[str, int, int]]:
    if len(seg) < FAT_ENTRY_SIZE:
        return []
    first_data = struct.unpack_from("<I", seg, 0x10)[0]
    if first_data <= 0 or first_data > len(seg) or first_data % FAT_ENTRY_SIZE != 0:
        return []
    table_count = first_data // FAT_ENTRY_SIZE
    if table_count <= 0 or table_count > 10000 or table_count * FAT_ENTRY_SIZE > len(seg):
        return []
    raw = []
    for i in range(table_count):
        off = i * FAT_ENTRY_SIZE
        name = cstr(seg[off:off + 0x10])
        rel = struct.unpack_from("<I", seg, off + 0x10)[0]
        if name and is_printable_name(name) and 0 < rel <= len(seg):
            raw.append((name, rel))
    out = []
    for j, (name, rel) in enumerate(raw):
        next_rel = len(seg)
        for _name2, rel2 in raw[j + 1:]:
            if rel2 >= rel:
                next_rel = rel2
                break
        out.append((name, rel, max(0, next_rel - rel)))
    return out


def collect_entries(data: bytes) -> tuple[dict[str, SourceEntry], dict[str, list[str]]]:
    by_path: dict[str, SourceEntry] = {}
    candidates: dict[str, list[str]] = {}

    def add_candidate(key: str, path: str):
        k = norm_key(key)
        if k:
            candidates.setdefault(k, []).append(path)

    def add_entry(path: str, name: str, abs_off: int, size: int, kind: str):
        path = path.replace("\\", "/")
        by_path[path] = SourceEntry(path, name, abs_off, size, kind)
        p = Path(path)
        add_candidate(p.stem, path)
        add_candidate(p.name, path)
        add_candidate(name, path)
        add_candidate(Path(name).stem, path)
        parts = path.split("/")
        # Extracted payloads often map from folder/stem names: N02 -> .../N02.DAT.
        if len(parts) >= 2:
            add_candidate(parts[-2], path)
            add_candidate(Path(parts[-2]).stem, path)

    def rec(seg: bytes, base_abs: int, prefix: str, level: int):
        if level > 12:
            return
        for name, rel, size in parse_fat_entries(seg):
            path = f"{prefix}/{name}" if prefix else name
            abs_off = base_abs + rel
            add_entry(path, name, abs_off, size, "FAT_ENTRY")
            if name.upper().endswith(".FAT"):
                child = seg[rel:rel+size]
                rec(child, abs_off, path, level + 1)

    for name, abs_off, size in parse_top_entries(data):
        add_entry(name, name, abs_off, size, "CDF_ENTRY")
        if name.upper().endswith(".FAT"):
            rec(data[abs_off:abs_off+size], abs_off, name, 1)

    for k in list(candidates):
        candidates[k] = sorted(set(candidates[k]))
    return by_path, candidates

# ---------------- PNG preparation ----------------

def _u16(data: bytes | bytearray, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]


def _u32(data: bytes | bytearray, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def _ps1_555_to_rgba_floor(v: int) -> tuple[int, int, int, int]:
    r5 = v & 0x1F; g5 = (v >> 5) & 0x1F; b5 = (v >> 10) & 0x1F
    return (r5 << 3, g5 << 3, b5 << 3, 0 if v == 0 else 255)


def _ps1_555_to_rgba_full(v: int) -> tuple[int, int, int, int]:
    r5 = v & 0x1F; g5 = (v >> 5) & 0x1F; b5 = (v >> 10) & 0x1F
    return (r5 * 255 // 31, g5 * 255 // 31, b5 * 255 // 31, 0 if v == 0 else 255)


def parse_tim_palette_and_size(tim_path: Path) -> tuple[int, int, int, list[int]]:
    data = tim_path.read_bytes()
    if len(data) < 20 or _u32(data, 0) != 0x10:
        raise SystemExit(f"Not a TIM file: {tim_path}")
    flags = _u32(data, 4)
    bpp = flags & 7
    if bpp not in (0, 1) or not (flags & 8):
        raise SystemExit(f"Only indexed TIMs with CLUT are supported: {tim_path}")
    clut_len = _u32(data, 8)
    clut_w = _u16(data, 16)
    clut_h = _u16(data, 18)
    colors = 16 if bpp == 0 else 256
    if clut_len < 12 or 8 + clut_len > len(data) or clut_w < colors or clut_h < 1:
        raise SystemExit(f"Bad or unsupported CLUT in TIM: {tim_path}")
    raw_pal = [_u16(data, 20 + i * 2) for i in range(colors)]
    img_pos = 8 + clut_len
    img_len = _u32(data, img_pos)
    if img_len < 12 or img_pos + img_len > len(data):
        raise SystemExit(f"Bad image block in TIM: {tim_path}")
    w_words = _u16(data, img_pos + 8)
    h = _u16(data, img_pos + 10)
    width = w_words * 4 if bpp == 0 else w_words * 2
    return bpp, width, h, raw_pal


def png_palette(raw_pal: list[int]) -> list[int]:
    out = []
    for v in raw_pal:
        r, g, b, _a = _ps1_555_to_rgba_full(v)
        out.extend([r, g, b])
    while len(out) < 768:
        out.extend([0,0,0])
    return out[:768]


def prepare_png(png_path: Path, tim_path: Path, prepared_dir: Path) -> Path:
    img = Image.open(png_path)
    if img.mode == "P":
        return png_path
    _bpp, width, height, raw_pal = parse_tim_palette_and_size(tim_path)
    rgba = img.convert("RGBA")
    if rgba.size != (width, height):
        raise SystemExit(f"PNG/TIM size mismatch for {png_path.name}: PNG {rgba.size}, TIM {width}x{height}")
    exact: dict[tuple[int,int,int,int], int] = {}
    nearest = []
    for i, v in enumerate(raw_pal):
        full = _ps1_555_to_rgba_full(v)
        floor = _ps1_555_to_rgba_floor(v)
        exact[full] = i
        exact[floor] = i
        nearest.append(full)
    indices = bytearray(width * height)
    used_nearest = 0
    for pos, (r,g,b,a) in enumerate(rgba.getdata()):
        if a == 0:
            indices[pos] = 0
            continue
        key = (r,g,b,255)
        idx = exact.get(key)
        if idx is None:
            best_i, best_d = 0, 1 << 60
            for i, (pr,pg,pb,pa) in enumerate(nearest):
                if pa == 0: continue
                d = (r-pr)*(r-pr)+(g-pg)*(g-pg)+(b-pb)*(b-pb)
                if d < best_d:
                    best_d, best_i = d, i
            idx = best_i
            used_nearest += 1
        indices[pos] = idx
    prepared_dir.mkdir(parents=True, exist_ok=True)
    out = prepared_dir / f"{strip_page_suffix(png_path.stem)}_indexed.png"
    pimg = Image.frombytes("P", (width, height), bytes(indices))
    pimg.putpalette(png_palette(raw_pal))
    pimg.info["transparency"] = 0
    pimg.save(out)
    if used_nearest:
        print(f"  Prepared indexed PNG: {out}  (nearest-mapped pixels: {used_nearest})")
    else:
        print(f"  Prepared indexed PNG: {out}")
    return out

def insert_indexed_png_into_tim(source_tim: Path, png_path: Path, out_tim: Path) -> None:
    tim = bytearray(source_tim.read_bytes())
    if len(tim) < 20 or _u32(tim, 0) != 0x10:
        raise SystemExit(f"Not a TIM file: {source_tim}")
    flags = _u32(tim, 4)
    bpp = flags & 7
    if bpp not in (0, 1):
        raise SystemExit(f"Only 4bpp/8bpp indexed TIMs are supported for internal insert: {source_tim}")
    pos = 8
    if flags & 8:
        clut_len = _u32(tim, pos)
        if clut_len < 12 or pos + clut_len > len(tim):
            raise SystemExit(f"Bad CLUT block in TIM: {source_tim}")
        pos += clut_len
    if pos + 12 > len(tim):
        raise SystemExit(f"Bad TIM image block: {source_tim}")
    img_len = _u32(tim, pos)
    if img_len < 12 or pos + img_len > len(tim):
        raise SystemExit(f"Bad TIM image length: {source_tim}")
    w_words = _u16(tim, pos + 8)
    height = _u16(tim, pos + 10)
    width = w_words * 4 if bpp == 0 else w_words * 2
    pixel_off = pos + 12
    pixel_len = img_len - 12

    img = Image.open(png_path)
    if img.mode != "P":
        raise SystemExit(f"Internal TIM insert expects indexed/P-mode PNG after preparation: {png_path} is {img.mode}")
    if img.size != (width, height):
        raise SystemExit(f"PNG/TIM size mismatch for {png_path.name}: PNG {img.size}, TIM {width}x{height}")
    if hasattr(img, "get_flattened_data"):
        pixels = list(img.get_flattened_data())
    else:
        pixels = list(img.getdata())
    if bpp == 0:
        if any(px < 0 or px > 15 for px in pixels):
            bad = sorted(set(px for px in pixels if px < 0 or px > 15))[:16]
            raise SystemExit(f"4bpp TIM PNG uses out-of-range palette indices for {png_path.name}: {bad}")
        expected = width * height // 2
        if pixel_len != expected:
            raise SystemExit(f"Unexpected 4bpp pixel length for {source_tim}: got 0x{pixel_len:X}, expected 0x{expected:X}")
        packed = bytearray(expected)
        j = 0
        for i in range(0, len(pixels), 2):
            lo = pixels[i] & 0x0F
            hi = (pixels[i + 1] & 0x0F) if i + 1 < len(pixels) else 0
            packed[j] = lo | (hi << 4)
            j += 1
    else:
        if any(px < 0 or px > 255 for px in pixels):
            raise SystemExit(f"8bpp TIM PNG uses out-of-range palette indices for {png_path.name}")
        expected = width * height
        if pixel_len != expected:
            raise SystemExit(f"Unexpected 8bpp pixel length for {source_tim}: got 0x{pixel_len:X}, expected 0x{expected:X}")
        packed = bytes(pixels)

    tim[pixel_off:pixel_off + pixel_len] = packed
    out_tim.parent.mkdir(parents=True, exist_ok=True)
    out_tim.write_bytes(tim)


# ---------------- work item setup ----------------

def read_archive_map(path: Optional[Path]) -> dict[str, str]:
    if not path:
        return {}
    out = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            stem = (row.get("stem") or row.get("name") or "").strip()
            archive_path = (row.get("archive_path") or row.get("path") or "").strip().replace("\\", "/")
            if stem and archive_path:
                out[norm_key(stem)] = archive_path
    return out


def find_tim(tim_dirs: list[Path], stem: str) -> Path:
    key = norm_key(stem)
    matches = []
    for tim_dir in tim_dirs:
        if not tim_dir.is_dir():
            raise SystemExit(f"TIM folder not found: {tim_dir}")
        for p in tim_dir.iterdir():
            if p.is_file() and p.suffix.lower() == ".tim":
                variants = {
                    norm_key(p.stem),
                    norm_key(p.stem.replace("_decoded", "")),
                    norm_key(p.stem.replace("decoded_", "")),
                }
                if key in variants:
                    matches.append(p)
    matches = sorted(set(matches))
    if not matches:
        raise SystemExit(f"No matching TIM for {stem!r} in: " + ", ".join(str(d) for d in tim_dirs))
    if len(matches) > 1:
        raise SystemExit(f"Multiple TIMs match {stem!r}: " + ", ".join(str(m) for m in matches))
    return matches[0]


def resolve_archive_path(stem: str, archive_map: dict[str, str], candidates: dict[str, list[str]], entries: dict[str, SourceEntry]) -> str:
    key = norm_key(stem)
    if key in archive_map:
        return archive_map[key]
    canonical = CANONICAL_ARCHIVE_PATHS.get(key)
    if canonical and canonical in entries:
        return canonical
    matches = sorted(set(candidates.get(key, [])))
    # NAME/TREE target payloads are usually DAT/BIN. Prefer them over container self entries.
    datbin = [m for m in matches if Path(m).suffix.upper() in (".DAT", ".BIN")]
    if len(datbin) == 1:
        return datbin[0]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SystemExit(f"Could not map {stem!r} to an archive path. Use --archive-map-csv.")
    raise SystemExit(f"Archive path for {stem!r} is ambiguous:\n  " + "\n  ".join(matches) + "\nUse --archive-map-csv.")


def _unique_paths(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[Path] = set()
    for p in paths:
        rp = p.resolve() if p.exists() else p
        if rp not in seen:
            seen.add(rp)
            out.append(p)
    return out


def discover(args, entries: dict[str, SourceEntry], candidates: dict[str, list[str]]) -> list[WorkItem]:
    png_dirs = _unique_paths([args.png_dir] + list(args.extra_png_dir or []))
    tim_dirs = _unique_paths([args.tim_dir] + list(args.extra_tim_dir or []))
    for d in png_dirs:
        if not d.is_dir(): raise SystemExit(f"PNG folder not found: {d}")
    for d in tim_dirs:
        if not d.is_dir(): raise SystemExit(f"TIM folder not found: {d}")
    amap = read_archive_map(args.archive_map_csv)
    wanted = {norm_key(x) for x in args.limit} if args.limit else None
    pngs = []
    for d in png_dirs:
        pngs.extend(p for p in d.glob("*.png") if p.is_file())
    pngs = sorted(set(pngs))
    if not pngs: raise SystemExit("No PNGs found in: " + ", ".join(str(d) for d in png_dirs))
    seen = set(); items = []
    for png in pngs:
        stem = strip_page_suffix(png.stem)
        key = norm_key(stem)
        if wanted and key not in wanted: continue
        if key in seen:
            raise SystemExit(f"Multiple PNGs map to {stem!r}. Use --limit or remove duplicates.")
        seen.add(key)
        tim = find_tim(tim_dirs, stem)
        apath = resolve_archive_path(stem, amap, candidates, entries)
        ent = entries.get(apath)
        if not ent:
            raise SystemExit(f"Resolved archive path does not exist in source CDF: {apath}")
        if ent.kind != "FAT_ENTRY":
            print(f"Warning: {apath} is {ent.kind}, not FAT_ENTRY; still patching exact slot.")
        items.append(WorkItem(
            stem=stem,
            png=png,
            source_tim=tim,
            prepared_png=None,
            patched_tim=args.patched_tim_dir / f"{stem}.TIM",
            encoded_bin=args.bin_dir / f"{stem}.BIN",
            archive_path=apath,
            abs_off=ent.abs_off,
            original_size=ent.size,
        ))
    if not items: raise SystemExit("No work items after filtering.")
    return sorted(items, key=lambda i: norm_key(i.stem))

# ---------------- codec commands and patch ----------------

def tim_insert(item: WorkItem, tim_tool: Optional[Path], prepared_dir: Path, args) -> None:
    if args.dry_run:
        if args.tim_mode == "external" and tim_tool is not None:
            run([sys.executable, str(tim_tool), "insert", str(item.source_tim), str(item.png), "-o", str(item.patched_tim)], dry_run=True)
        else:
            print(f"$ internal TIM insert {item.source_tim} {item.png} -o {item.patched_tim}")
        item.prepared_png = item.png
        return

    insert_png = prepare_png(item.png, item.source_tim, prepared_dir)
    item.prepared_png = insert_png
    if args.tim_mode == "external":
        if tim_tool is None:
            raise SystemExit("TIM mode is external but no TIM helper was found.")
        run([sys.executable, str(tim_tool), "insert", str(item.source_tim), str(insert_png), "-o", str(item.patched_tim)], dry_run=False)
    else:
        insert_indexed_png_into_tim(item.source_tim, insert_png, item.patched_tim)
        print(f"  Internal TIM insert: {item.patched_tim}")


def _pad_encoded_stream(path: Path, raw: bytes, final_size: int) -> int:
    raw_size = len(raw)
    if raw_size < final_size:
        raw = raw + bytes(final_size - raw_size)
    path.write_bytes(raw)
    return raw_size


def lz_encode(item: WorkItem, lz_tool: Optional[Path], args) -> tuple[int, str]:
    if args.dry_run:
        if args.lz_mode != "internal" and lz_tool is not None:
            run([sys.executable, str(lz_tool), "encode", str(item.patched_tim), "-o", str(item.encoded_bin), "--level", "3"], dry_run=True)
            if args.lz_mode == "auto":
                print("  dry-run note: if the external stream fails or grows, v10 will fall back to internal optimal LZ.")
            return 0, "dry-run"
        print(f"$ internal optimal LZ encode {item.patched_tim} -o {item.encoded_bin}")
        return 0, "dry-run"

    if args.lz_mode != "internal" and lz_tool is not None:
        try:
            run([sys.executable, str(lz_tool), "encode", str(item.patched_tim), "-o", str(item.encoded_bin), "--level", "3"], dry_run=False)
        except subprocess.CalledProcessError as exc:
            if args.lz_mode == "external":
                raise SystemExit(
                    f"External LZ encoder failed for {item.stem}; refusing in external-only mode.\n"
                    f"  archive path: {item.archive_path}\n"
                    f"  helper: {lz_tool}\n"
                    f"  exit code: {exc.returncode}\n"
                    f"Use --lz-mode internal or omit --lz-mode to use the internal encoder."
                ) from exc
            print(
                f"  External LZ failed for {item.stem}; falling back to internal optimal LZ.\n"
                f"  This is expected for larger TIMs like JTEST when older helpers stop at 0x8000 bytes."
            )
        else:
            raw = item.encoded_bin.read_bytes()
            if len(raw) <= item.original_size:
                return _pad_encoded_stream(item.encoded_bin, raw, item.original_size), "external"
            if args.lz_mode == "external":
                raise SystemExit(
                    f"Encoded stream grew for {item.stem}; refusing in-place patch.\n"
                    f"  archive path: {item.archive_path}\n"
                    f"  original slot: 0x{item.original_size:X}\n"
                    f"  new stream:    0x{len(raw):X}\n"
                )
            print(
                f"  External LZ grew for {item.stem}: 0x{len(raw):X} > 0x{item.original_size:X}; "
                f"trying internal optimal LZ..."
            )

    tim_bytes = item.patched_tim.read_bytes()
    candidate_passes = []
    if len(tim_bytes) <= 0x4000:
        candidate_passes.append(max(1, min(args.small_internal_lz_candidates, args.internal_lz_candidates)))
        if args.internal_lz_candidates not in candidate_passes:
            candidate_passes.append(args.internal_lz_candidates)
    else:
        candidate_passes.append(args.internal_lz_candidates)

    best_raw: Optional[bytes] = None
    best_candidates = 0
    for cand in candidate_passes:
        raw = encode_lz_internal_optimal(tim_bytes, max_candidates=cand)
        dec = decode_lz_internal(raw, max_out=max(len(tim_bytes) + 1024, 1024))
        if dec != tim_bytes:
            raise SystemExit(f"Internal LZ verification failed for {item.stem} at candidate depth {cand}")
        if best_raw is None or len(raw) < len(best_raw):
            best_raw = raw
            best_candidates = cand
        if len(raw) <= item.original_size:
            break
        if cand != candidate_passes[-1]:
            print(
                f"  Internal LZ depth {cand} grew for {item.stem}: "
                f"0x{len(raw):X} > 0x{item.original_size:X}; retrying deeper..."
            )

    assert best_raw is not None
    if len(best_raw) > item.original_size:
        raise SystemExit(
            f"Internal optimal LZ stream still grew for {item.stem}; refusing in-place patch.\n"
            f"  archive path: {item.archive_path}\n"
            f"  original slot: 0x{item.original_size:X}\n"
            f"  new stream:    0x{len(best_raw):X}\n"
            f"Try reducing edits or reusing fewer changed pixels. You can also try --internal-lz-candidates 8192."
        )
    return _pad_encoded_stream(item.encoded_bin, best_raw, item.original_size), f"internal-optimal-{best_candidates}"


def validate_tim_size(item: WorkItem):
    src_size = item.source_tim.stat().st_size
    out_size = item.patched_tim.stat().st_size
    if src_size != out_size:
        raise SystemExit(f"TIM size changed for {item.stem}: source 0x{src_size:X}, patched 0x{out_size:X}")


def write_report(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows: return
    fields = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="In-place TREE.CDF compressed NAME/JTEST TIM payload patcher. Preserves all unrelated bytes.")
    ap.add_argument("--source-cdf", type=Path, required=True)
    ap.add_argument("--png-dir", type=Path, required=True, help="Folder containing edited PNGs, e.g. N01-N29 and/or JTEST.")
    ap.add_argument("--extra-png-dir", type=Path, action="append", default=[], help="Additional edited PNG folder. May be used for JTEST if separate from N images.")
    ap.add_argument("--tim-dir", type=Path, required=True, help="Folder containing source decoded TIMs.")
    ap.add_argument("--extra-tim-dir", type=Path, action="append", default=[], help="Additional source TIM folder. May be used for JTEST.TIM if separate from N TIMs.")
    ap.add_argument("--out-cdf", type=Path, required=True)
    ap.add_argument("--patched-tim-dir", type=Path)
    ap.add_argument("--prepared-png-dir", type=Path)
    ap.add_argument("--bin-dir", type=Path)
    ap.add_argument("--archive-map-csv", type=Path)
    ap.add_argument("--tim-tool", type=Path)
    ap.add_argument("--tim-mode", choices=["internal", "external"], default="internal",
                    help="internal directly replaces indexed TIM pixel data; external calls pixygarden_TIM_Tool_v2.py.")
    ap.add_argument("--lz-tool", type=Path)
    ap.add_argument("--lz-mode", choices=["auto", "external", "internal"], default="internal",
                    help="internal is safest and handles JTEST. auto tries the helper encoder first, then falls back to internal optimal LZ if the stream fails or grows.")
    ap.add_argument("--internal-lz-candidates", type=int, default=4096,
                    help="Candidate search depth for large internal optimal LZ fallback. Higher may compress better but is slower.")
    ap.add_argument("--small-internal-lz-candidates", type=int, default=128,
                    help="Initial candidate depth for small TIMs. If it does not fit, v10 retries with --internal-lz-candidates.")
    ap.add_argument("--limit", nargs="*", default=[])
    ap.add_argument("--clean-work", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.source_cdf.is_file():
        raise SystemExit(f"Source CDF not found: {args.source_cdf}")
    args.out_cdf.parent.mkdir(parents=True, exist_ok=True)
    stem = args.out_cdf.with_suffix("")
    if args.patched_tim_dir is None: args.patched_tim_dir = stem.parent / f"{stem.name}_patched_tim"
    if args.prepared_png_dir is None: args.prepared_png_dir = stem.parent / f"{stem.name}_prepared_png"
    if args.bin_dir is None: args.bin_dir = stem.parent / f"{stem.name}_bins"

    script_dir = Path(__file__).resolve().parent
    roots = [Path.cwd(), script_dir, args.source_cdf.parent]
    tim_tool: Optional[Path] = None
    if args.tim_mode == "external":
        tim_tool = find_helper(args.tim_tool, HELPER_TIM_NAMES, roots, "TIM")
    lz_tool: Optional[Path] = None
    if args.lz_mode != "internal":
        try:
            lz_tool = find_helper(args.lz_tool, HELPER_LZ_NAMES, roots, "LZ")
        except SystemExit:
            if args.lz_mode == "external":
                raise
            print("LZ helper not found; using internal optimal LZ encoder.")

    mkdir_clean(args.patched_tim_dir, args.clean_work and not args.dry_run)
    mkdir_clean(args.prepared_png_dir, args.clean_work and not args.dry_run)
    mkdir_clean(args.bin_dir, args.clean_work and not args.dry_run)

    source = bytearray(args.source_cdf.read_bytes())
    entries, candidates = collect_entries(bytes(source))
    items = discover(args, entries, candidates)

    print("PixyGarden TREE in-place payload patcher v10 (NAME + JTEST)")
    print(f"Source CDF: {args.source_cdf}")
    print(f"Output CDF: {args.out_cdf}")
    print(f"TIM mode:  {args.tim_mode}")
    print(f"TIM tool:  {tim_tool if tim_tool is not None else 'internal indexed TIM inserter'}")
    print(f"LZ mode:   {args.lz_mode}")
    print(f"LZ tool:   {lz_tool if lz_tool is not None else 'internal optimal encoder'}")
    print(f"Items:      {len(items)}")
    print("Important: only the listed archive payload slots will be overwritten; no FAT/CDF tables are rebuilt.\n")

    report = []
    for item in items:
        print(f"== {item.stem} -> {item.archive_path} @ 0x{item.abs_off:X} size 0x{item.original_size:X} ==")
        tim_insert(item, tim_tool, args.prepared_png_dir, args)
        if not args.dry_run:
            validate_tim_size(item)
        raw_size, lz_method = lz_encode(item, lz_tool, args)
        if not args.dry_run:
            repl = item.encoded_bin.read_bytes()
            if len(repl) != item.original_size:
                raise SystemExit(f"Internal error: replacement size mismatch for {item.stem}")
            source[item.abs_off:item.abs_off + item.original_size] = repl
        report.append({
            "stem": item.stem,
            "archive_path": item.archive_path,
            "abs_off_hex": f"0x{item.abs_off:X}",
            "slot_size_hex": f"0x{item.original_size:X}",
            "raw_encoded_size_hex": "" if args.dry_run else f"0x{raw_size:X}",
            "padded_final_size_hex": f"0x{item.original_size:X}",
            "lz_method": lz_method,
            "source_tim": str(item.source_tim),
            "png": str(item.png),
            "prepared_png": str(item.prepared_png or ""),
        })

    if not args.dry_run:
        args.out_cdf.write_bytes(source)
        if args.out_cdf.stat().st_size != args.source_cdf.stat().st_size:
            raise SystemExit("Internal error: output CDF size changed")
        write_report(args.out_cdf.with_name(args.out_cdf.stem + "_inplace_report.csv"), report)
        print(f"\nWrote: {args.out_cdf}")
        print(f"Report: {args.out_cdf.with_name(args.out_cdf.stem + '_inplace_report.csv')}")
        print("Output size matches source CDF exactly.")
    else:
        print("\nDry run complete; no files written.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
