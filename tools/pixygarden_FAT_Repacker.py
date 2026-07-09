#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

FAT_ENTRY_SIZE = 0x14

def align(value: int, n: int) -> int:
    if n <= 1:
        return value
    return ((value + n - 1) // n) * n

def cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("ascii", errors="replace")

def enc_name(name: str, size: int) -> bytes:
    b = name.encode("ascii", errors="replace")
    if len(b) > size:
        raise ValueError(f"Name {name!r} too long for {size}-byte field")
    return b + b"\0" * (size - len(b))

def is_printable_name(name: str) -> bool:
    return bool(name) and all(32 <= ord(ch) <= 126 for ch in name)

def sanitize_component(name: str) -> str:
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name)
    return name or "_unnamed"

def path_parts(path: str) -> list[str]:
    return [sanitize_component(p) for p in path.replace("\\", "/").split("/") if p not in ("", ".", "..")]

def rel_path(base: Path, path: str) -> Path:
    if not path:
        return base
    return base.joinpath(*path_parts(path))

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

@dataclass
class ReportRow:
    path: str
    kind: str
    source: str
    source_size: int
    new_size: int
    delta: int
    old_rel: str = ""
    new_rel: str = ""
    notes: str = ""

@dataclass
class Context:
    root: Path
    fat_align: int
    pad_byte: int
    preserve_fat_table_count: bool
    pad_replacements_to_original: bool
    txt_terminator: int
    normalize_txt_tail: bool
    report: list[ReportRow] = field(default_factory=list)

def parse_fat_segment(data: bytes, path: str) -> Optional[FATSegment]:
    if len(data) < FAT_ENTRY_SIZE:
        return None

    first_data = struct.unpack_from("<I", data, 0x10)[0]
    if first_data <= 0 or first_data > len(data):
        return None
    if first_data % FAT_ENTRY_SIZE != 0:
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

    # Sizes are inferred from the next valid entry in table order.
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

def read_replacement(ctx: Context, archive_path: str) -> Optional[bytes]:
    p = rel_path(ctx.root, archive_path)
    if p.is_file():
        return p.read_bytes()
    return None

def replacement_dir_exists(ctx: Context, archive_path: str) -> bool:
    return rel_path(ctx.root, archive_path).is_dir()

def normalize_txt_tail(ctx: Context, archive_path: str, repl: bytes) -> tuple[bytes, str]:
    if not ctx.normalize_txt_tail or not archive_path.upper().endswith(".TXT"):
        return repl, ""

    term = ctx.txt_terminator & 0xFF
    pos = repl.find(bytes([term]))
    if pos < 0:
        return repl, f"txt_tail_not_normalized_no_terminator_0x{term:02X}"
    if pos + 1 >= len(repl):
        return repl, "txt_tail_no_existing_padding"

    tail = repl[pos + 1:]
    if all(b == ctx.pad_byte for b in tail):
        return repl, "txt_tail_already_pad_byte"

    out = repl[:pos + 1] + bytes([ctx.pad_byte]) * len(tail)
    return out, f"txt_tail_normalized_after_terminator_0x{term:02X};tail_len=0x{len(tail):X}"

def maybe_pad_replacement(ctx: Context, archive_path: str, original: bytes, repl: bytes) -> tuple[bytes, str]:
    repl, norm_note = normalize_txt_tail(ctx, archive_path, repl)
    notes = []
    if norm_note:
        notes.append(norm_note)

    if ctx.pad_replacements_to_original and len(repl) <= len(original):
        if len(repl) < len(original):
            notes.append(f"padded_to_original_slot;raw_replacement_size=0x{len(repl):X}")
            return repl + bytes([ctx.pad_byte]) * (len(original) - len(repl)), ";".join(notes)
        notes.append("same_size_replacement")
        return repl, ";".join(notes)

    notes.append("compact_or_growth_replacement")
    return repl, ";".join(notes)

def build_leaf_or_original(ctx: Context, archive_path: str, original: bytes, kind: str) -> tuple[bytes, str]:
    repl = read_replacement(ctx, archive_path)
    if repl is not None:
        out, notes = maybe_pad_replacement(ctx, archive_path, original, repl)
        ctx.report.append(ReportRow(
            archive_path, kind, "replacement_file",
            len(original), len(out), len(out) - len(original),
            notes=notes
        ))
        return out, "replacement_file"

    ctx.report.append(ReportRow(
        archive_path, kind, "original",
        len(original), len(original), 0
    ))
    return original, "original"

def build_fat_recursive(ctx: Context, archive_path: str, original: bytes, depth: int = 0) -> tuple[bytes, str]:
    # If the user supplied a raw replacement file for this FAT and no directory
    # with child replacements exists, treat it as an explicit raw override.
    dir_exists = replacement_dir_exists(ctx, archive_path)
    raw_repl = read_replacement(ctx, archive_path)
    if raw_repl is not None and not dir_exists:
        out, notes = maybe_pad_replacement(ctx, archive_path, original, raw_repl)
        ctx.report.append(ReportRow(
            archive_path or "__root__.FAT", "fat_raw_override", "replacement_file",
            len(original), len(out), len(out) - len(original),
            notes=notes
        ))
        return out, "replacement_file"

    fat = parse_fat_segment(original, archive_path)
    if fat is None:
        return build_leaf_or_original(ctx, archive_path, original, "fat_unparsed_leaf")

    valid_entries = [e for e in fat.raw_entries if e.valid]
    table_count = fat.table_count if ctx.preserve_fat_table_count else len(valid_entries)
    first_data = table_count * FAT_ENTRY_SIZE

    table = bytearray(b"\0" * first_data)
    data_out = bytearray()
    compact_idx = 0

    iterable = fat.raw_entries if ctx.preserve_fat_table_count else valid_entries

    for e in iterable:
        if not e.valid:
            continue

        child_path = f"{archive_path}/{e.name}" if archive_path else e.name
        child_orig = original[e.rel_off:e.rel_off + e.size]

        if e.name.upper().endswith(".FAT"):
            # This is the bug fix: pass child_orig into the recursive call.
            child_bytes, child_source = build_fat_recursive(ctx, child_path, child_orig, depth + 1)
            kind = "fat_entry_rebuilt_fat"
        else:
            child_bytes, child_source = build_leaf_or_original(ctx, child_path, child_orig, "fat_entry")
            kind = "fat_entry"

        cur_rel = first_data + len(data_out)
        aligned_rel = align(cur_rel, ctx.fat_align)
        if aligned_rel > cur_rel:
            data_out.extend(bytes([ctx.pad_byte]) * (aligned_rel - cur_rel))
        new_rel = first_data + len(data_out)

        table_index = e.index if ctx.preserve_fat_table_count else compact_idx
        compact_idx += 1

        ent_off = table_index * FAT_ENTRY_SIZE
        table[ent_off:ent_off + 0x10] = enc_name(e.name, 0x10)
        struct.pack_into("<I", table, ent_off + 0x10, new_rel)
        data_out.extend(child_bytes)

        if e.name.upper().endswith(".FAT") or len(child_bytes) != len(child_orig):
            ctx.report.append(ReportRow(
                child_path, kind, child_source,
                len(child_orig), len(child_bytes), len(child_bytes) - len(child_orig),
                old_rel=f"0x{e.rel_off:X}",
                new_rel=f"0x{new_rel:X}"
            ))

    rebuilt = bytes(table + data_out)
    ctx.report.append(ReportRow(
        archive_path or "__root__.FAT", "fat_container", "rebuilt_fat",
        len(original), len(rebuilt), len(rebuilt) - len(original),
        notes=f"entries={len(valid_entries)};table_count={table_count};first_data=0x{first_data:X}"
    ))
    return rebuilt, "rebuilt_fat"

def write_report(path: Path, rows: list[ReportRow]) -> None:
    fields = ["path", "kind", "source", "source_size", "new_size", "delta", "old_rel", "new_rel", "notes"]
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
                "old_rel": r.old_rel,
                "new_rel": r.new_rel,
                "notes": r.notes,
            })

def main() -> int:
    ap = argparse.ArgumentParser(description="Recursive PixyGarden standalone FAT repacker. Useful for DATA/STAGE/HELP.FAT.")
    ap.add_argument("--source-fat", required=True, help="Original standalone .FAT file")
    ap.add_argument("--replacement-dir", required=True, help="Directory containing replacement files in FAT-relative paths")
    ap.add_argument("--out-fat", required=True, help="Output rebuilt .FAT")
    ap.add_argument("--report", required=True, help="CSV report")
    ap.add_argument("--fat-align", type=lambda x: int(x, 0), default=4)
    ap.add_argument("--pad-byte", type=lambda x: int(x, 0), default=0xFF)
    ap.add_argument("--txt-terminator", type=lambda x: int(x, 0), default=0x00)
    ap.add_argument("--no-normalize-txt-tail", dest="normalize_txt_tail", action="store_false")
    ap.set_defaults(normalize_txt_tail=True)
    ap.add_argument("--compact-fat-table", action="store_true")
    ap.add_argument("--compact-smaller-replacements", action="store_true")
    ap.add_argument("--preserve-source-size", action="store_true", default=True, help="Pad output back to source size if it would shrink. Default enabled.")
    ap.add_argument("--allow-shrink", dest="preserve_source_size", action="store_false")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = Path(args.source_fat)
    repl = Path(args.replacement_dir)
    if not repl.is_dir():
        raise SystemExit(f"Replacement directory not found: {repl}")

    data = src.read_bytes()
    ctx = Context(
        root=repl,
        fat_align=args.fat_align,
        pad_byte=args.pad_byte & 0xFF,
        preserve_fat_table_count=not args.compact_fat_table,
        pad_replacements_to_original=not args.compact_smaller_replacements,
        txt_terminator=args.txt_terminator & 0xFF,
        normalize_txt_tail=args.normalize_txt_tail,
    )

    rebuilt, _ = build_fat_recursive(ctx, "", data, 0)

    if args.preserve_source_size and len(rebuilt) < len(data):
        rebuilt = rebuilt + bytes([ctx.pad_byte]) * (len(data) - len(rebuilt))
        ctx.report.append(ReportRow(
            "__final_output__", "final_pad", "pad_to_source_size",
            len(data), len(rebuilt), len(rebuilt) - len(data),
            notes=f"pad_byte=0x{ctx.pad_byte:02X}"
        ))

    if len(rebuilt) > len(data):
        ctx.report.append(ReportRow(
            "__final_output__", "growth", "rebuilt_fat",
            len(data), len(rebuilt), len(rebuilt) - len(data),
            notes="output_grew"
        ))

    write_report(Path(args.report), ctx.report)

    if not args.dry_run:
        Path(args.out_fat).write_bytes(rebuilt)

    print("PixyGarden standalone FAT repack")
    print("--------------------------------")
    print(f"Source:      {src}")
    print(f"Output:      {args.out_fat}")
    print(f"Report:      {args.report}")
    print(f"Old size:    0x{len(data):X}")
    print(f"New size:    0x{len(rebuilt):X}")
    print(f"Delta:       {len(rebuilt)-len(data)}")
    print(f"Dry run:     {args.dry_run}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
