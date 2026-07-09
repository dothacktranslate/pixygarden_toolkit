#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import math
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Iterable

SECTOR = 0x800
TOP_ENTRY_SIZE = 0x20
FAT_ENTRY_SIZE = 0x14

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def align(value: int, n: int) -> int:
    if n <= 1:
        return value
    return ((value + n - 1) // n) * n


def cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("ascii", errors="replace")


def enc_name(name: str, size: int) -> bytes:
    b = name.encode("ascii", errors="replace")
    if len(b) > size:
        raise ValueError(f"Name {name!r} is too long for {size}-byte field")
    return b + b"\0" * (size - len(b))


def sanitize_component(name: str) -> str:
    # Match the extractor's safe-ish naming but keep normal game names intact.
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name)
    return name or "_unnamed"


def path_parts(path: str) -> list[str]:
    return [sanitize_component(p) for p in path.replace("\\", "/").split("/") if p not in ("", ".", "..")]


def rel_path(base: Path, path: str) -> Path:
    return base.joinpath(*path_parts(path))


def is_printable_name(name: str) -> bool:
    if not name:
        return False
    return all(32 <= ord(ch) <= 126 for ch in name)

# ---------------------------------------------------------------------------
# Top-level CDF parsing
# ---------------------------------------------------------------------------

@dataclass
class TopCDFEntry:
    index: int
    name: str
    table_off: int
    sector: int
    sector_count: int
    size: int
    abs_off: int
    alloc: int

@dataclass
class TopCDF:
    data_start: int
    count: int
    table_start: int
    header_prefix: bytes
    entries: list[TopCDFEntry]
    unknown1: Optional[int] = None
    unknown2: Optional[int] = None


def _try_parse_top(data: bytes, table_start: int) -> Optional[TopCDF]:
    if len(data) < 8:
        return None
    data_start, count = struct.unpack_from("<II", data, 0)
    if data_start <= 0 or data_start > len(data) + SECTOR:
        return None
    if count <= 0 or count > 10000:
        return None
    if table_start + count * TOP_ENTRY_SIZE > data_start:
        return None

    entries: list[TopCDFEntry] = []
    score = 0
    prev_sector = -1
    for i in range(count):
        off = table_start + i * TOP_ENTRY_SIZE
        name = cstr(data[off:off + 0x14])
        if not is_printable_name(name):
            return None
        sector, sector_count, size = struct.unpack_from("<III", data, off + 0x14)
        if sector_count <= 0 or sector_count > 0x100000:
            return None
        abs_off = sector * SECTOR
        alloc = sector_count * SECTOR
        # Some CDFs may have trailing space beyond file length when copied, but
        # normal entries should point at/after data_start and mostly fit.
        if abs_off < data_start:
            return None
        if size > alloc:
            return None
        if abs_off + size > len(data):
            # suspicious but not always fatal for damaged/truncated dumps
            score -= 5
        if sector >= prev_sector:
            score += 2
        prev_sector = sector
        if "." in name or name.upper() in {"TREE"}:
            score += 1
        entries.append(TopCDFEntry(i, name, off, sector, sector_count, size, abs_off, alloc))
    if score < count:
        return None
    unk1 = unk2 = None
    if table_start >= 0x10 and len(data) >= 0x10:
        unk1, unk2 = struct.unpack_from("<II", data, 8)
    return TopCDF(data_start, count, table_start, data[:data_start], entries, unk1, unk2)


def parse_top_cdf(data: bytes) -> TopCDF:
    # TREE-style CDF uses table_start 0x10; NAME-style CDF appears to use 0x0A.
    candidates = [0x10, 0x0A, 0x08, 0x0C]
    parsed: list[TopCDF] = []
    for ts in candidates:
        p = _try_parse_top(data, ts)
        if p:
            parsed.append(p)
    if not parsed:
        raise ValueError("Could not parse top-level CDF. Tried table starts 0x10, 0x0A, 0x08, 0x0C.")
    # Prefer common formats in order.
    for pref in (0x10, 0x0A):
        for p in parsed:
            if p.table_start == pref:
                return p
    return parsed[0]

# ---------------------------------------------------------------------------
# Nested FAT parsing/rebuilding
# ---------------------------------------------------------------------------

@dataclass
class FATRawEntry:
    index: int
    name: str
    table_off: int
    rel_off: int
    valid: bool
    size: int = 0

@dataclass
class FATSegment:
    path: str
    data: bytes
    table_count: int
    first_data: int
    raw_entries: list[FATRawEntry]


def parse_fat_segment(data: bytes, path: str) -> Optional[FATSegment]:
    if len(data) < FAT_ENTRY_SIZE:
        return None
    first_data = struct.unpack_from("<I", data, 0x10)[0]
    if first_data <= 0 or first_data > len(data):
        return None
    if first_data % FAT_ENTRY_SIZE != 0:
        # Most PixyGarden FATs use table_count * 0x14 exactly. If this fails,
        # it probably is not one of the mini-archives.
        return None
    table_count = first_data // FAT_ENTRY_SIZE
    if table_count <= 0 or table_count > 10000:
        return None
    if table_count * FAT_ENTRY_SIZE > len(data):
        return None

    raw: list[FATRawEntry] = []
    valid_positions: list[int] = []
    for i in range(table_count):
        off = i * FAT_ENTRY_SIZE
        name = cstr(data[off:off + 0x10])
        rel = struct.unpack_from("<I", data, off + 0x10)[0]
        valid = bool(name) and is_printable_name(name) and 0 < rel <= len(data)
        raw.append(FATRawEntry(i, name, off, rel, valid))
        if valid:
            valid_positions.append(i)

    if not valid_positions:
        return None

    # Infer sizes from the next valid entry in table order.
    for pos_idx, raw_idx in enumerate(valid_positions):
        e = raw[raw_idx]
        next_rel = len(data)
        for next_raw_idx in valid_positions[pos_idx + 1:]:
            n = raw[next_raw_idx]
            if n.rel_off >= e.rel_off:
                next_rel = n.rel_off
                break
        e.size = max(0, next_rel - e.rel_off)
    return FATSegment(path, data, table_count, first_data, raw)

@dataclass
class BuildReport:
    path: str
    kind: str
    source_size: int
    new_size: int
    delta: int
    source: str
    notes: str = ""
    top_sector: str = ""
    top_sector_count: str = ""

@dataclass
class RepackContext:
    root: Path
    fat_align: int
    top_align: int
    pad_byte: int
    preserve_fat_table_count: bool
    pad_replacements_to_original: bool
    preserve_source_size: bool
    txt_terminator: int
    normalize_txt_tail: bool
    report: list[BuildReport] = field(default_factory=list)


def read_replacement_file(ctx: RepackContext, archive_path: str) -> Optional[bytes]:
    p = rel_path(ctx.root, archive_path)
    if p.is_file():
        return p.read_bytes()
    return None


def replacement_dir_exists(ctx: RepackContext, archive_path: str) -> bool:
    return rel_path(ctx.root, archive_path).is_dir()


def normalize_txt_tail(ctx: RepackContext, archive_path: str, repl: bytes) -> tuple[bytes, str]:
    """For PixyGarden INFO-style TXT files, bytes after the final terminator
    are padding. The originals use 0xFF padding; replacement builders that
    wrote 0x00 here can freeze the game. Normalize that tail before slot
    padding.
    """
    if not ctx.normalize_txt_tail or not archive_path.upper().endswith(".TXT"):
        return repl, ""
    term = ctx.txt_terminator & 0xFF
    pos = repl.rfind(bytes([term]))
    if pos < 0:
        return repl, f"txt_tail_not_normalized_no_terminator_0x{term:02X}"
    if pos + 1 >= len(repl):
        return repl, "txt_tail_no_existing_padding"
    tail = repl[pos+1:]
    if all(b == ctx.pad_byte for b in tail):
        return repl, "txt_tail_already_pad_byte"
    out = repl[:pos+1] + bytes([ctx.pad_byte]) * len(tail)
    return out, f"txt_tail_normalized_after_terminator_0x{term:02X};tail_len=0x{len(tail):X}"


def maybe_pad_replacement(ctx: RepackContext, archive_path: str, original: bytes, repl: bytes) -> tuple[bytes, str]:
    """Return replacement bytes and a notes string.

    The default behavior is deliberately conservative for PixyGarden: if a
    replacement is smaller than its original slot, pad it back to the original
    size instead of compacting the containing FAT. This keeps offsets, inferred
    sizes, and parent CDF sector usage stable whenever possible. If a replacement
    is larger, it is left larger so the normal recursive repacker can grow the
    relevant FAT/CDF layers.
    """
    repl, norm_note = normalize_txt_tail(ctx, archive_path, repl)
    if ctx.pad_replacements_to_original and len(repl) <= len(original):
        if len(repl) < len(original):
            notes = f"padded_to_original_slot;raw_replacement_size=0x{len(repl):X}"
            if norm_note:
                notes += ";" + norm_note
            return repl + bytes([ctx.pad_byte]) * (len(original) - len(repl)), notes
        notes = "same_size_replacement"
        if norm_note:
            notes += ";" + norm_note
        return repl, notes
    notes = "compact_or_growth_replacement"
    if norm_note:
        notes += ";" + norm_note
    return repl, notes


def build_leaf_or_original(ctx: RepackContext, archive_path: str, original: bytes, kind: str = "file") -> tuple[bytes, str]:
    repl = read_replacement_file(ctx, archive_path)
    if repl is not None:
        out, notes = maybe_pad_replacement(ctx, archive_path, original, repl)
        ctx.report.append(BuildReport(archive_path, kind, len(original), len(out), len(out) - len(original), "replacement_file", notes=notes))
        return out, "replacement_file"
    ctx.report.append(BuildReport(archive_path, kind, len(original), len(original), 0, "original"))
    return original, "original"


def build_fat_recursive(ctx: RepackContext, archive_path: str, original: bytes, depth: int = 0) -> tuple[bytes, str]:
    # If user supplied a raw replacement file for this FAT and no directory with
    # child replacements exists, use it as an explicit override.
    dir_exists = replacement_dir_exists(ctx, archive_path)
    raw_repl = read_replacement_file(ctx, archive_path)
    if raw_repl is not None and not dir_exists:
        out, notes = maybe_pad_replacement(ctx, archive_path, original, raw_repl)
        ctx.report.append(BuildReport(archive_path, "fat_raw_override", len(original), len(out), len(out) - len(original), "replacement_file", notes=notes))
        return out, "replacement_file"

    fat = parse_fat_segment(original, archive_path)
    if fat is None:
        # Some files end with .FAT but are not parseable mini-archives. Treat as leaf.
        return build_leaf_or_original(ctx, archive_path, original, kind="fat_unparsed_leaf")

    valid_entries = [e for e in fat.raw_entries if e.valid]
    table_count = fat.table_count if ctx.preserve_fat_table_count else len(valid_entries)
    first_data = table_count * FAT_ENTRY_SIZE
    table = bytearray(b"\0" * first_data)
    data_out = bytearray()

    # Rebuild entries in original table/index order.
    for out_index, e in enumerate(valid_entries if not ctx.preserve_fat_table_count else fat.raw_entries):
        if not e.valid:
            continue
        child_path = f"{archive_path}/{e.name}"
        child_orig = original[e.rel_off:e.rel_off + e.size]
        if e.name.upper().endswith(".FAT"):
            child_bytes, child_source = build_fat_recursive(ctx, child_path, child_orig, depth + 1)
            kind = "fat_entry_rebuilt_fat"
        else:
            child_bytes, child_source = build_leaf_or_original(ctx, child_path, child_orig, kind="fat_entry")
            kind = "fat_entry"

        # Align child start within the FAT data area.
        cur_rel = first_data + len(data_out)
        aligned_rel = align(cur_rel, ctx.fat_align)
        if aligned_rel > cur_rel:
            data_out.extend(bytes([ctx.pad_byte]) * (aligned_rel - cur_rel))
        new_rel = first_data + len(data_out)

        # Write table entry at original index when preserving count; otherwise compact.
        table_index = e.index if ctx.preserve_fat_table_count else out_index
        ent_off = table_index * FAT_ENTRY_SIZE
        table[ent_off:ent_off + 0x10] = enc_name(e.name, 0x10)
        struct.pack_into("<I", table, ent_off + 0x10, new_rel)
        data_out.extend(child_bytes)

        # The recursive child build already logged leaf/FAT override rows. Add a
        # concise container movement row only for rebuilt nested FATs or size changes.
        if e.name.upper().endswith(".FAT") or len(child_bytes) != len(child_orig):
            ctx.report.append(BuildReport(child_path, kind, len(child_orig), len(child_bytes), len(child_bytes) - len(child_orig), child_source, notes=f"old_rel=0x{e.rel_off:X};new_rel=0x{new_rel:X}"))

    rebuilt = bytes(table + data_out)
    ctx.report.append(BuildReport(archive_path, "fat_container", len(original), len(rebuilt), len(rebuilt) - len(original), "rebuilt_fat", notes=f"entries={len(valid_entries)};table_count={table_count};first_data=0x{first_data:X}"))
    return rebuilt, "rebuilt_fat"

# ---------------------------------------------------------------------------
# Top-level CDF rebuild
# ---------------------------------------------------------------------------

def build_cdf(ctx: RepackContext, source_data: bytes) -> tuple[bytes, TopCDF]:
    top = parse_top_cdf(source_data)
    header = bytearray(top.header_prefix)
    if len(header) < top.data_start:
        header.extend(b"\0" * (top.data_start - len(header)))

    data_out = bytearray()
    for e in top.entries:
        original = source_data[e.abs_off:e.abs_off + e.size]
        archive_path = e.name
        if e.name.upper().endswith(".FAT"):
            payload, source = build_fat_recursive(ctx, archive_path, original, 0)
            kind = "cdf_entry_rebuilt_fat"
        else:
            payload, source = build_leaf_or_original(ctx, archive_path, original, kind="cdf_entry")
            kind = "cdf_entry"

        # Top-level CDF entries are sector-addressed. Each entry starts on a sector.
        cur_abs = top.data_start + len(data_out)
        aligned_abs = align(cur_abs, ctx.top_align)
        if aligned_abs > cur_abs:
            data_out.extend(bytes([ctx.pad_byte]) * (aligned_abs - cur_abs))
        new_abs = top.data_start + len(data_out)
        if new_abs % SECTOR:
            raise AssertionError("Top-level entry did not land on a sector boundary")
        new_sector = new_abs // SECTOR
        new_sector_count = max(1, align(len(payload), SECTOR) // SECTOR)
        new_alloc = new_sector_count * SECTOR

        # Patch table entry in header.
        header[e.table_off:e.table_off + 0x14] = enc_name(e.name, 0x14)
        struct.pack_into("<III", header, e.table_off + 0x14, new_sector, new_sector_count, len(payload))

        data_out.extend(payload)
        if len(payload) < new_alloc:
            data_out.extend(bytes([ctx.pad_byte]) * (new_alloc - len(payload)))

        ctx.report.append(BuildReport(
            archive_path, kind, e.size, len(payload), len(payload) - e.size, source,
            notes=f"old_sector={e.sector};old_sectors={e.sector_count};old_alloc=0x{e.alloc:X}",
            top_sector=str(new_sector), top_sector_count=str(new_sector_count)
        ))

    return bytes(header + data_out), top

# ---------------------------------------------------------------------------
# Reports / CLI
# ---------------------------------------------------------------------------

def write_report(path: Path, rows: list[BuildReport]) -> None:
    fields = ["path", "kind", "source", "source_size", "new_size", "delta", "top_sector", "top_sector_count", "notes"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({
                "path": r.path,
                "kind": r.kind,
                "source": r.source,
                "source_size": f"0x{r.source_size:X}",
                "new_size": f"0x{r.new_size:X}",
                "delta": r.delta,
                "top_sector": r.top_sector,
                "top_sector_count": r.top_sector_count,
                "notes": r.notes,
            })


def main() -> int:
    ap = argparse.ArgumentParser(description="Generic recursive PixyGarden CDF/FAT repacker v3. Conservative by default: smaller replacements are padded back to original slots and final CDF shrinkage is prevented. Uses an original CDF as a template, applies replacement files from a directory, rebuilds nested .FAT mini-archives, and updates top-level CDF sectors/sizes.")
    ap.add_argument("--source-cdf", required=True, help="Original .CDF file used as template/source")
    ap.add_argument("--replacement-dir", required=True, help="Directory containing replacement files in archive-relative paths. Directories named *.FAT are treated as nested archive replacement trees.")
    ap.add_argument("--out-cdf", required=True, help="Output rebuilt .CDF")
    ap.add_argument("--report", required=True, help="CSV rebuild report")
    ap.add_argument("--fat-align", type=lambda x: int(x, 0), default=4, help="Alignment for files inside nested FATs; default 4")
    ap.add_argument("--top-align", type=lambda x: int(x, 0), default=SECTOR, help="Alignment for top-level CDF entries; default 0x800")
    ap.add_argument("--pad-byte", type=lambda x: int(x, 0), default=0xFF, help="Padding byte for unused FAT/CDF slot space; default 0xFF, matching TREE INFO TXT padding")
    ap.add_argument("--txt-terminator", type=lambda x: int(x, 0), default=0x39, help="TXT terminator byte used when normalizing TXT tails; default 0x39")
    ap.add_argument("--no-normalize-txt-tail", dest="normalize_txt_tail", action="store_false", help="Do not rewrite bytes after the final TXT terminator to the pad byte")
    ap.set_defaults(normalize_txt_tail=True)
    ap.add_argument("--compact-fat-table", action="store_true", help="Compact FAT tables to only valid entries. Default preserves original FAT table_count/blank entries, which is safer.")
    ap.add_argument("--compact-smaller-replacements", action="store_true", help="Allow smaller replacement files to shrink their original FAT slots. Default pads smaller replacements back to the original size, which is safer.")
    ap.add_argument("--allow-cdf-shrink", action="store_true", help="Allow the final rebuilt CDF to be smaller than the source. Default pads the final output back to the source size if it would shrink.")
    ap.add_argument("--dry-run", action="store_true", help="Build in memory and report only; do not write output CDF")
    args = ap.parse_args()

    src = Path(args.source_cdf)
    repl = Path(args.replacement_dir)
    if not repl.is_dir():
        raise SystemExit(f"Replacement directory not found: {repl}")
    data = src.read_bytes()
    ctx = RepackContext(
        root=repl,
        fat_align=args.fat_align,
        top_align=args.top_align,
        pad_byte=args.pad_byte & 0xFF,
        preserve_fat_table_count=not args.compact_fat_table,
        pad_replacements_to_original=not args.compact_smaller_replacements,
        preserve_source_size=not args.allow_cdf_shrink,
        txt_terminator=args.txt_terminator & 0xFF,
        normalize_txt_tail=args.normalize_txt_tail,
    )
    rebuilt, top = build_cdf(ctx, data)
    if ctx.preserve_source_size and len(rebuilt) < len(data):
        ctx.report.append(BuildReport("<CDF EOF padding>", "cdf_eof_padding", len(rebuilt), len(data), len(data) - len(rebuilt), "preserve_source_size", notes="final output padded back to original CDF byte size"))
        rebuilt = rebuilt + bytes([ctx.pad_byte]) * (len(data) - len(rebuilt))
    write_report(Path(args.report), ctx.report)

    if not args.dry_run:
        out = Path(args.out_cdf)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(rebuilt)

    print("PixyGarden recursive CDF repacker")
    print("---------------------------------")
    print(f"Source CDF:        {src}")
    print(f"Replacement dir:   {repl}")
    print(f"Output CDF:        {args.out_cdf}")
    print(f"Report:            {args.report}")
    print(f"Top table start:   0x{top.table_start:X}")
    print(f"Top entries:       {top.count}")
    print(f"Original size:     0x{len(data):X} ({len(data)} bytes)")
    print(f"Rebuilt size:      0x{len(rebuilt):X} ({len(rebuilt)} bytes)")
    print(f"Delta:             {len(rebuilt) - len(data)} bytes")
    repl_count = sum(1 for r in ctx.report if r.source == "replacement_file")
    fat_count = sum(1 for r in ctx.report if r.source == "rebuilt_fat")
    print(f"Replacement files: {repl_count}")
    print(f"Rebuilt FATs:      {fat_count}")
    print(f"Pad smaller repl.: {ctx.pad_replacements_to_original}")
    print(f"Preserve CDF size: {ctx.preserve_source_size}")
    if args.dry_run:
        print("Dry run: output CDF not written.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
