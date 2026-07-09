#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import os
import re
import struct
from pathlib import Path

SECTOR = 0x800

def cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("ascii", errors="replace")

def safe_name(name: str) -> str:
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name)
    return name or "_unnamed"

def parse_top_cdf(data: bytes):
    if len(data) < 0x10:
        raise ValueError("File too small for TREE-style CDF header")
    data_start, count, unk1, unk2 = struct.unpack_from("<IIII", data, 0)
    entries = []
    table_start = 0x10
    for i in range(count):
        off = table_start + i * 0x20
        if off + 0x20 > len(data):
            raise ValueError(f"Top-level entry {i} exceeds file size")
        name = cstr(data[off:off + 0x14])
        sector, sector_count, size = struct.unpack_from("<III", data, off + 0x14)
        abs_off = sector * SECTOR
        alloc = sector_count * SECTOR
        entries.append({
            "level": 0,
            "container": Path(args.cdf).name if "args" in globals() else "CDF",
            "path": name,
            "index": i,
            "name": name,
            "kind": "CDF_ENTRY",
            "table_off": off,
            "rel_off": abs_off,
            "abs_off": abs_off,
            "sector": sector,
            "sector_count": sector_count,
            "size": size,
            "alloc": alloc,
            "padding": alloc - size,
        })
    return {
        "data_start": data_start,
        "entry_count": count,
        "unknown1": unk1,
        "unknown2": unk2,
        "entries": entries,
    }

def parse_fat_segment(seg: bytes, base_abs: int, container_path: str, level: int):
    """
    PixyGarden nested FAT mini-archive:
      entry size 0x14
      name[0x10]
      u32 relative_offset
    Count is inferred from first entry's relative offset / 0x14.
    Blank padding entries can exist at the end of the table.
    """
    if len(seg) < 0x14:
        return []
    first_data = struct.unpack_from("<I", seg, 0x10)[0]
    if first_data <= 0 or first_data > len(seg):
        return []
    table_count = first_data // 0x14
    raw = []
    for i in range(table_count):
        off = i * 0x14
        if off + 0x14 > len(seg):
            break
        name = cstr(seg[off:off + 0x10])
        rel = struct.unpack_from("<I", seg, off + 0x10)[0]
        if name and 0 < rel <= len(seg):
            raw.append({"index": i, "name": name, "rel_off": rel, "table_off": off})
    entries = []
    for j, e in enumerate(raw):
        next_rel = len(seg)
        for e2 in raw[j + 1:]:
            if e2["rel_off"] >= e["rel_off"]:
                next_rel = e2["rel_off"]
                break
        size = next_rel - e["rel_off"]
        abs_off = base_abs + e["rel_off"]
        path = f"{container_path}/{e['name']}"
        entries.append({
            "level": level,
            "container": container_path,
            "path": path,
            "index": e["index"],
            "name": e["name"],
            "kind": "FAT_ENTRY",
            "table_off": base_abs + e["table_off"],
            "rel_off": e["rel_off"],
            "abs_off": abs_off,
            "sector": "",
            "sector_count": "",
            "size": size,
            "alloc": "",
            "padding": "",
            "first_data": first_data,
            "table_count": table_count,
        })
    return entries

def recursive_manifest(data: bytes, top_entries: list[dict], max_depth: int = 10):
    out = list(top_entries)

    def rec(seg: bytes, base_abs: int, container_path: str, level: int):
        if level > max_depth:
            return
        entries = parse_fat_segment(seg, base_abs, container_path, level)
        out.extend(entries)
        for e in entries:
            if e["name"].upper().endswith(".FAT"):
                child = seg[e["rel_off"]:e["rel_off"] + e["size"]]
                rec(child, e["abs_off"], e["path"], level + 1)

    for e in top_entries:
        if e["name"].upper().endswith(".FAT"):
            seg = data[e["abs_off"]:e["abs_off"] + e["size"]]
            rec(seg, e["abs_off"], e["path"], 1)
    return out

def write_manifest_csv(path: Path, entries: list[dict]):
    fields = [
        "level", "container", "path", "index", "name", "kind",
        "table_off", "rel_off", "abs_off", "sector", "sector_count",
        "size", "alloc", "padding", "first_data", "table_count"
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for e in entries:
            row = dict(e)
            for key in ["table_off", "rel_off", "abs_off", "size", "alloc", "padding", "first_data"]:
                if isinstance(row.get(key), int):
                    row[key] = f"0x{row[key]:X}"
            for key in ["sector", "sector_count", "table_count"]:
                if isinstance(row.get(key), int):
                    row[key] = str(row[key])
            w.writerow(row)

def extract_entries(data: bytes, entries: list[dict], outdir: Path, nested: bool):
    """
    Extract entries safely.

    When --extract-nested is used, .FAT entries are both real files and
    containers for child entries. Windows cannot have a file and directory with
    the same name, so FAT containers are extracted as directories, with the raw
    archive bytes written to __self.bin inside that directory:

        DETAILS.FAT/__self.bin
        DETAILS.FAT/INFO.FAT/__self.bin
        DETAILS.FAT/INFO.FAT/INFO_01A.TXT

    Without --extract-nested, top-level files are extracted directly by name.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    container_paths = set()
    if nested:
        for ent in entries:
            if ent.get("name", "").upper().endswith(".FAT"):
                container_paths.add(ent.get("path", ""))

    for e in entries:
        if e["level"] > 0 and not nested:
            continue
        if not e["name"]:
            continue
        parts = [safe_name(p) for p in e["path"].split("/") if p]
        relpath = Path(*parts)

        if nested and e.get("path") in container_paths:
            # .FAT is a container: store its raw bytes inside a directory.
            outpath = outdir / relpath / "__self.bin"
        else:
            outpath = outdir / relpath

        # If an old extraction created a conflicting file where we now need a
        # directory, give a clear error instead of a Python traceback.
        if outpath.parent.exists() and not outpath.parent.is_dir():
            raise RuntimeError(
                f"Cannot create directory {outpath.parent}: a file already exists there. "
                f"Use a fresh --extract-dir or delete the previous extraction output."
            )
        outpath.parent.mkdir(parents=True, exist_ok=True)
        blob = data[e["abs_off"]:e["abs_off"] + e["size"]]
        outpath.write_bytes(blob)

def main():
    global args
    ap = argparse.ArgumentParser(description="Inspect/extract PixyGarden TREE-style CDF archives and nested FAT mini-archives. v2 avoids FAT file/directory extraction collisions.")
    ap.add_argument("--cdf", required=True, help="Input .CDF file")
    ap.add_argument("--manifest", help="Write recursive manifest CSV")
    ap.add_argument("--extract-dir", help="Extract files to this directory")
    ap.add_argument("--extract-nested", action="store_true", help="Also extract nested FAT entries")
    ap.add_argument("--max-depth", type=int, default=10)
    args = ap.parse_args()

    cdf_path = Path(args.cdf)
    data = cdf_path.read_bytes()
    top = parse_top_cdf(data)
    entries = recursive_manifest(data, top["entries"], args.max_depth)

    print("PixyGarden CDF inspection")
    print("-------------------------")
    print(f"Input:        {cdf_path}")
    print(f"File size:    0x{len(data):X} ({len(data)} bytes)")
    print(f"Data start:   0x{top['data_start']:X}")
    print(f"Entry count:  {top['entry_count']}")
    print(f"Unknown1/2:   0x{top['unknown1']:08X} / 0x{top['unknown2']:08X}")
    print(f"Manifest entries including nested: {len(entries)}")

    print("\nTop-level entries:")
    for e in top["entries"]:
        print(f"  {e['index']:02d} {e['name']:<14} off=0x{e['abs_off']:06X} "
              f"sector=0x{e['sector']:03X} sectors={e['sector_count']:3} "
              f"size=0x{e['size']:06X} pad=0x{e['padding']:04X}")

    if args.manifest:
        out = Path(args.manifest)
        write_manifest_csv(out, entries)
        print(f"\nWrote manifest: {out}")

    if args.extract_dir:
        extract_entries(data, entries, Path(args.extract_dir), nested=args.extract_nested)
        print(f"Wrote extracted files to: {args.extract_dir}")

if __name__ == "__main__":
    main()
