#!/usr/bin/env python3
"""
pixygarden_FAT_tool.py

Two-way PixyGarden standalone .FAT utility.

Features:
  extract  - recursively extract a .FAT archive into a folder
  list     - print/list .FAT contents without extracting
  repack   - rebuild a .FAT from an extracted/replacement folder

The repack path is based on the same 0x14-byte entry structure used by the
existing pixygarden_FAT_Repacker_v2 script:
  entry = 0x10-byte ASCII name + uint32 little-endian relative data offset

Default behavior is preservation-first:
  - extraction writes exact inferred file slots unless --trim-txt-tail is used
  - repack pads smaller replacements back to their original slot size by default
  - output is padded back to source size if it would otherwise shrink
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import struct
import sys
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


def sha1_hex(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def maybe_trim_txt_tail(data: bytes, archive_path: str, terminator: int) -> tuple[bytes, str]:
    if not archive_path.upper().endswith(".TXT"):
        return data, ""
    term = terminator & 0xFF
    pos = data.find(bytes([term]))
    if pos < 0:
        return data, f"txt_tail_not_trimmed_no_terminator_0x{term:02X}"
    trimmed = data[:pos + 1]
    if len(trimmed) == len(data):
        return data, "txt_tail_no_extra_bytes"
    return trimmed, f"txt_tail_trimmed_from_0x{len(data):X}_to_0x{len(trimmed):X}"


@dataclass
class FATRawEntry:
    index: int
    name: str
    table_off: int
    rel_off: int
    valid: bool
    size: int = 0
    notes: str = ""


@dataclass
class FATSegment:
    path: str
    data: bytes
    table_count: int
    first_data: int
    raw_entries: list[FATRawEntry]


@dataclass
class ExtractRow:
    path: str
    kind: str
    index: str = ""
    table_off: str = ""
    rel_off: str = ""
    size: str = ""
    extracted_size: str = ""
    sha1: str = ""
    output: str = ""
    notes: str = ""


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


def parse_fat_segment(data: bytes, path: str = "") -> Optional[FATSegment]:
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
        notes = ""
        if name and not is_printable_name(name):
            notes = "non_printable_name"
        elif name and not (0 < rel <= len(data)):
            notes = "offset_out_of_range"
        elif not name:
            notes = "blank_entry"
        raw.append(FATRawEntry(i, name, off, rel, valid, notes=notes))
        if valid:
            valid_positions.append(i)

    if not valid_positions:
        return None

    # Sizes are inferred from the next valid entry in table order, matching the
    # behavior of the existing standalone repacker.
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


def iter_valid_entries(fat: FATSegment) -> list[FATRawEntry]:
    return [e for e in fat.raw_entries if e.valid]


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow(row)


def prepare_output_dir(out_dir: Path, overwrite: bool) -> None:
    if out_dir.exists():
        if not out_dir.is_dir():
            raise SystemExit(f"ERROR: output path exists but is not a directory: {out_dir}")
        if any(out_dir.iterdir()) and not overwrite:
            raise SystemExit(f"ERROR: output directory is not empty: {out_dir}\nUse --overwrite to allow writing into it.")
    out_dir.mkdir(parents=True, exist_ok=True)


def extract_fat_recursive(
    data: bytes,
    out_root: Path,
    archive_path: str,
    rows: list[ExtractRow],
    *,
    trim_txt_tail: bool,
    txt_terminator: int,
    save_raw_fat: bool,
    depth: int = 0,
) -> None:
    fat = parse_fat_segment(data, archive_path)
    if fat is None:
        raise RuntimeError(f"Not a valid FAT segment: {archive_path or '<root>'}")

    container_label = archive_path or "__root__.FAT"
    rows.append(ExtractRow(
        path=container_label,
        kind="fat_container",
        size=f"0x{len(data):X}",
        extracted_size="",
        notes=f"table_count={fat.table_count};first_data=0x{fat.first_data:X};valid_entries={len(iter_valid_entries(fat))}",
    ))

    if save_raw_fat:
        raw_name = "__raw_root.FAT" if not archive_path else "__raw.FAT"
        raw_out = rel_path(out_root, f"{archive_path}/{raw_name}" if archive_path else raw_name)
        raw_out.parent.mkdir(parents=True, exist_ok=True)
        raw_out.write_bytes(data)

    for e in fat.raw_entries:
        child_path = f"{archive_path}/{e.name}" if archive_path else e.name
        if not e.valid:
            rows.append(ExtractRow(
                path=child_path or f"__invalid_entry_{e.index}",
                kind="invalid_entry",
                index=str(e.index),
                table_off=f"0x{e.table_off:X}",
                rel_off=f"0x{e.rel_off:X}",
                notes=e.notes,
            ))
            continue

        slot = data[e.rel_off:e.rel_off + e.size]
        is_nested = e.name.upper().endswith(".FAT") and parse_fat_segment(slot, child_path) is not None

        if is_nested:
            # Recurse into a directory named after the nested .FAT file.
            rows.append(ExtractRow(
                path=child_path,
                kind="fat_entry_nested_fat",
                index=str(e.index),
                table_off=f"0x{e.table_off:X}",
                rel_off=f"0x{e.rel_off:X}",
                size=f"0x{e.size:X}",
                sha1=sha1_hex(slot),
                output=str(rel_path(out_root, child_path)),
            ))
            extract_fat_recursive(
                slot,
                out_root,
                child_path,
                rows,
                trim_txt_tail=trim_txt_tail,
                txt_terminator=txt_terminator,
                save_raw_fat=save_raw_fat,
                depth=depth + 1,
            )
        else:
            out_data = slot
            notes = "exact_slot"
            if trim_txt_tail:
                out_data, trim_note = maybe_trim_txt_tail(out_data, child_path, txt_terminator)
                if trim_note:
                    notes += ";" + trim_note

            out_path = rel_path(out_root, child_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(out_data)
            rows.append(ExtractRow(
                path=child_path,
                kind="fat_entry_file",
                index=str(e.index),
                table_off=f"0x{e.table_off:X}",
                rel_off=f"0x{e.rel_off:X}",
                size=f"0x{e.size:X}",
                extracted_size=f"0x{len(out_data):X}",
                sha1=sha1_hex(slot),
                output=str(out_path),
                notes=notes,
            ))


def write_extract_manifest(path: Path, rows: list[ExtractRow]) -> None:
    fields = ["path", "kind", "index", "table_off", "rel_off", "size", "extracted_size", "sha1", "output", "notes"]
    write_csv(path, [r.__dict__ for r in rows], fields)


def list_fat_recursive(data: bytes, archive_path: str, rows: list[ExtractRow], depth: int = 0) -> None:
    fat = parse_fat_segment(data, archive_path)
    if fat is None:
        raise RuntimeError(f"Not a valid FAT segment: {archive_path or '<root>'}")

    rows.append(ExtractRow(
        path=archive_path or "__root__.FAT",
        kind="fat_container",
        size=f"0x{len(data):X}",
        notes=f"table_count={fat.table_count};first_data=0x{fat.first_data:X};valid_entries={len(iter_valid_entries(fat))}",
    ))

    for e in fat.raw_entries:
        child_path = f"{archive_path}/{e.name}" if archive_path else e.name
        if not e.valid:
            rows.append(ExtractRow(
                path=child_path or f"__invalid_entry_{e.index}",
                kind="invalid_entry",
                index=str(e.index),
                table_off=f"0x{e.table_off:X}",
                rel_off=f"0x{e.rel_off:X}",
                notes=e.notes,
            ))
            continue
        slot = data[e.rel_off:e.rel_off + e.size]
        is_nested = e.name.upper().endswith(".FAT") and parse_fat_segment(slot, child_path) is not None
        rows.append(ExtractRow(
            path=child_path,
            kind="fat_entry_nested_fat" if is_nested else "fat_entry_file",
            index=str(e.index),
            table_off=f"0x{e.table_off:X}",
            rel_off=f"0x{e.rel_off:X}",
            size=f"0x{e.size:X}",
            sha1=sha1_hex(slot),
        ))
        if is_nested:
            list_fat_recursive(slot, child_path, rows, depth + 1)


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
            notes=notes,
        ))
        return out, "replacement_file"

    ctx.report.append(ReportRow(
        archive_path, kind, "original",
        len(original), len(original), 0,
    ))
    return original, "original"


def build_fat_recursive(ctx: Context, archive_path: str, original: bytes, depth: int = 0) -> tuple[bytes, str]:
    dir_exists = replacement_dir_exists(ctx, archive_path)
    raw_repl = read_replacement(ctx, archive_path)
    if raw_repl is not None and not dir_exists:
        out, notes = maybe_pad_replacement(ctx, archive_path, original, raw_repl)
        ctx.report.append(ReportRow(
            archive_path or "__root__.FAT", "fat_raw_override", "replacement_file",
            len(original), len(out), len(out) - len(original),
            notes=notes,
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
                new_rel=f"0x{new_rel:X}",
            ))

    rebuilt = bytes(table + data_out)
    ctx.report.append(ReportRow(
        archive_path or "__root__.FAT", "fat_container", "rebuilt_fat",
        len(original), len(rebuilt), len(rebuilt) - len(original),
        notes=f"entries={len(valid_entries)};table_count={table_count};first_data=0x{first_data:X}",
    ))
    return rebuilt, "rebuilt_fat"


def write_report(path: Path, rows: list[ReportRow]) -> None:
    fields = ["path", "kind", "source", "source_size", "new_size", "delta", "old_rel", "new_rel", "notes"]
    dicts = []
    for r in rows:
        dicts.append({
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
    write_csv(path, dicts, fields)


def cmd_extract(args: argparse.Namespace) -> int:
    if getattr(args, "all_in_folder", False):
        args.folder = args.source_fat
        args.out_root = args.out_dir
        args.summary = getattr(args, "manifest", None)
        return cmd_extract_folder(args)

    src = Path(args.source_fat)
    out_dir = Path(args.out_dir)
    if not src.is_file():
        raise SystemExit(f"ERROR: source FAT not found: {src}")
    prepare_output_dir(out_dir, args.overwrite)

    data = src.read_bytes()
    if parse_fat_segment(data, "") is None:
        raise SystemExit(f"ERROR: does not look like a supported standalone FAT: {src}")

    rows: list[ExtractRow] = []
    extract_fat_recursive(
        data,
        out_dir,
        "",
        rows,
        trim_txt_tail=args.trim_txt_tail,
        txt_terminator=args.txt_terminator & 0xFF,
        save_raw_fat=args.save_raw_fat,
    )

    manifest = Path(args.manifest) if args.manifest else out_dir / "_fat_extract_manifest.csv"
    write_extract_manifest(manifest, rows)

    print("PixyGarden FAT extract")
    print("----------------------")
    print(f"Source:      {src}")
    print(f"Output dir:  {out_dir}")
    print(f"Manifest:    {manifest}")
    print(f"Rows:        {len(rows)}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    src = Path(args.source_fat)
    if not src.is_file():
        raise SystemExit(f"ERROR: source FAT not found: {src}")
    data = src.read_bytes()
    rows: list[ExtractRow] = []
    list_fat_recursive(data, "", rows)

    if args.manifest:
        write_extract_manifest(Path(args.manifest), rows)

    for r in rows:
        indent = "  " * max(0, r.path.count("/") if r.path != "__root__.FAT" else 0)
        extra = f" {r.size}" if r.size else ""
        off = f" @ {r.rel_off}" if r.rel_off else ""
        print(f"{indent}{r.kind}: {r.path}{extra}{off}")
    return 0


def cmd_extract_folder(args: argparse.Namespace) -> int:
    in_dir = Path(args.folder)
    out_root = Path(args.out_root)

    if not in_dir.is_dir():
        raise SystemExit(f"ERROR: input folder not found: {in_dir}")

    fat_files = sorted(in_dir.rglob("*.FAT") if args.recursive else in_dir.glob("*.FAT"))

    if not fat_files:
        print(f"No .FAT files found in: {in_dir}")
        return 0

    out_root.mkdir(parents=True, exist_ok=True)

    summary_rows = []
    ok_count = 0
    fail_count = 0

    for fat_path in fat_files:
        if args.recursive:
            rel = fat_path.relative_to(in_dir)
            out_dir = out_root / rel.with_suffix("")
        else:
            out_dir = out_root / fat_path.stem

        try:
            if out_dir.exists() and any(out_dir.iterdir()) and not args.overwrite:
                raise RuntimeError(f"output folder is not empty: {out_dir}; use --overwrite")
            out_dir.mkdir(parents=True, exist_ok=True)

            data = fat_path.read_bytes()
            if parse_fat_segment(data, "") is None:
                raise RuntimeError("does not look like a supported standalone FAT")

            rows: list[ExtractRow] = []
            extract_fat_recursive(
                data,
                out_dir,
                "",
                rows,
                trim_txt_tail=args.trim_txt_tail,
                txt_terminator=args.txt_terminator & 0xFF,
                save_raw_fat=args.save_raw_fat,
            )

            manifest = out_dir / "_fat_extract_manifest.csv"
            write_extract_manifest(manifest, rows)

            print(f"Extracted {fat_path} -> {out_dir}")
            summary_rows.append({
                "source_fat": str(fat_path),
                "output_dir": str(out_dir),
                "manifest": str(manifest),
                "rows": len(rows),
                "status": "ok",
                "notes": "",
            })
            ok_count += 1

        except Exception as exc:
            print(f"ERROR extracting {fat_path}: {exc}")
            summary_rows.append({
                "source_fat": str(fat_path),
                "output_dir": str(out_dir),
                "manifest": "",
                "rows": 0,
                "status": "error",
                "notes": str(exc),
            })
            fail_count += 1
            if not args.continue_on_error:
                break

    summary_path = Path(args.summary) if args.summary else out_root / "_fat_folder_extract_summary.csv"
    write_csv(
        summary_path,
        summary_rows,
        ["source_fat", "output_dir", "manifest", "rows", "status", "notes"],
    )

    print("")
    print("PixyGarden FAT folder extract")
    print("-----------------------------")
    print(f"Input folder: {in_dir}")
    print(f"Output root:  {out_root}")
    print(f"Found FATs:   {len(fat_files)}")
    print(f"Extracted:    {ok_count}")
    print(f"Failed:       {fail_count}")
    print(f"Summary:      {summary_path}")

    return 0 if fail_count == 0 else 1


def cmd_repack(args: argparse.Namespace) -> int:
    src = Path(args.source_fat)
    repl = Path(args.replacement_dir)
    out = Path(args.out_fat)

    if not src.is_file():
        raise SystemExit(f"ERROR: source FAT not found: {src}")
    if not repl.is_dir():
        raise SystemExit(f"ERROR: replacement/extracted directory not found: {repl}")

    data = src.read_bytes()
    if parse_fat_segment(data, "") is None:
        raise SystemExit(f"ERROR: does not look like a supported standalone FAT: {src}")

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
            notes=f"pad_byte=0x{ctx.pad_byte:02X}",
        ))

    if len(rebuilt) > len(data):
        ctx.report.append(ReportRow(
            "__final_output__", "growth", "rebuilt_fat",
            len(data), len(rebuilt), len(rebuilt) - len(data),
            notes="output_grew",
        ))

    report = Path(args.report) if args.report else out.with_suffix(out.suffix + ".repack_report.csv")
    write_report(report, ctx.report)

    if not args.dry_run:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(rebuilt)

    print("PixyGarden FAT repack")
    print("---------------------")
    print(f"Source:      {src}")
    print(f"Input dir:   {repl}")
    print(f"Output FAT:  {out}")
    print(f"Report:      {report}")
    print(f"Old size:    0x{len(data):X}")
    print(f"New size:    0x{len(rebuilt):X}")
    print(f"Delta:       {len(rebuilt)-len(data)}")
    print(f"Dry run:     {args.dry_run}")
    return 0


def add_common_repack_args(ap: argparse.ArgumentParser) -> None:
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract/list/repack PixyGarden standalone .FAT archives.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ex = sub.add_parser("extract", help="Recursively extract a standalone .FAT archive")
    ex.add_argument("source_fat", help="Source .FAT file, or folder when --all-in-folder is used")
    ex.add_argument("out_dir", help="Output folder, or output root when --all-in-folder is used")
    ex.add_argument("--all-in-folder", action="store_true", help="Treat source_fat as a folder and extract every .FAT inside it")
    ex.add_argument("--recursive", action="store_true", help="With --all-in-folder, search recursively for .FAT files")
    ex.add_argument("--continue-on-error", action="store_true", help="With --all-in-folder, continue if one FAT fails")
    ex.add_argument("--manifest", help="CSV manifest path. Default: <out_dir>/_fat_extract_manifest.csv")
    ex.add_argument("--overwrite", action="store_true", help="Allow writing into a non-empty output directory")
    ex.add_argument("--save-raw-fat", action="store_true", help="Also save raw FAT blobs as __raw.FAT files")
    ex.add_argument("--trim-txt-tail", action="store_true", help="For .TXT files, extract only through the first terminator byte")
    ex.add_argument("--txt-terminator", type=lambda x: int(x, 0), default=0x00)
    ex.set_defaults(func=cmd_extract)


    exf = sub.add_parser("extract-folder", help="Extract every .FAT file in a folder into separate output folders")
    exf.add_argument("folder", help="Folder containing .FAT files")
    exf.add_argument("out_root", help="Output root folder; each FAT extracts to its own subfolder")
    exf.add_argument("--recursive", action="store_true", help="Search recursively for .FAT files")
    exf.add_argument("--summary", help="CSV summary path. Default: <out_root>/_fat_folder_extract_summary.csv")
    exf.add_argument("--overwrite", action="store_true", help="Allow writing into existing/non-empty output folders")
    exf.add_argument("--continue-on-error", action="store_true", help="Continue extracting other FATs if one fails")
    exf.add_argument("--save-raw-fat", action="store_true", help="Also save raw FAT blobs as __raw.FAT files")
    exf.add_argument("--trim-txt-tail", action="store_true", help="For .TXT files, extract only through the first terminator byte")
    exf.add_argument("--txt-terminator", type=lambda x: int(x, 0), default=0x00)
    exf.set_defaults(func=cmd_extract_folder)

    ls = sub.add_parser("list", help="List a standalone .FAT archive")
    ls.add_argument("source_fat")
    ls.add_argument("--manifest", help="Optional CSV manifest path")
    ls.set_defaults(func=cmd_list)

    rp = sub.add_parser("repack", help="Rebuild a standalone .FAT archive from an extracted/replacement folder")
    rp.add_argument("source_fat")
    rp.add_argument("replacement_dir")
    rp.add_argument("out_fat")
    rp.add_argument("--report", help="CSV report path. Default: <out_fat>.repack_report.csv")
    add_common_repack_args(rp)
    rp.set_defaults(func=cmd_repack)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
