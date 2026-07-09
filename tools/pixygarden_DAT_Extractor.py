#!/usr/bin/env python3
"""
generic_dat_extractor.py

Conservative embedded-file carver for .DAT-style game containers.

It does not assume that every .DAT has a directory table. Instead it scans the
byte stream for file formats whose sizes can be parsed safely, then extracts
those records and writes a manifest. It is especially useful for PlayStation-era
containers that are just concatenated resources, such as DAT files containing
standard TIM images.

Currently recognizes:
  - Sony PlayStation TIM images (.TIM), including CLUT metadata
  - PNG (.png)
  - BMP (.bmp)
  - GIF (.gif)
  - RIFF-family files: WAV/AVI/WEBP/RIFF (.wav/.avi/.webp/.riff)
  - FORM-family files: AIFF/8SVX/etc. (.aiff/.iff)
  - Ogg streams by capture-page scan (.ogg)
  - ZIP archives via EOCD search (.zip)
  - gzip streams as a header-only/fallback carve to next known file (.gz)

Unknown data can optionally be carved as gap files with --save-gaps.

Usage examples:
  python generic_dat_extractor.py PLSEL00.DAT
  python generic_dat_extractor.py PLSEL00.DAT -o extracted_plsel --save-gaps
  python generic_dat_extractor.py *.DAT --types tim,png,riff --save-gaps
  python generic_dat_extractor.py PLSEL00.DAT --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import struct
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Iterable, Optional


TIM_MAGIC = b"\x10\x00\x00\x00"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
GIF_MAGICS = (b"GIF87a", b"GIF89a")
ZIP_LOCAL_MAGIC = b"PK\x03\x04"
ZIP_EOCD_MAGIC = b"PK\x05\x06"
GZIP_MAGIC = b"\x1f\x8b"
OGG_MAGIC = b"OggS"


@dataclass
class CarvedFile:
    index: int
    offset: int
    end: int
    size: int
    kind: str
    extension: str
    filename: str
    confidence: str
    notes: str = ""
    warning: str = ""


@dataclass
class Candidate:
    offset: int
    end: int
    kind: str
    extension: str
    confidence: str
    notes: str = ""
    warning: str = ""

    @property
    def size(self) -> int:
        return self.end - self.offset


def u16le(data: bytes, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]


def u32le(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def u32be(data: bytes, off: int) -> int:
    return struct.unpack_from(">I", data, off)[0]


def ascii_safe(raw: bytes) -> str:
    return "".join(chr(b) if 32 <= b <= 126 else "." for b in raw)


def parse_tim(data: bytes, off: int) -> Optional[Candidate]:
    """Parse a standard PlayStation TIM at data[off:]."""
    n = len(data)
    if off + 8 > n or data[off:off + 4] != TIM_MAGIC:
        return None

    flags = u32le(data, off + 4)
    # Normal TIM flags use pixel-mode bits 0-2 and optional CLUT bit 3.
    # Be strict to avoid false positives inside arbitrary binary data.
    pixmode = flags & 0x07
    has_clut = bool(flags & 0x08)
    if pixmode not in (0, 1, 2, 3):
        return None
    if flags & ~0x0F:
        return None

    pos = off + 8
    notes_parts = [f"flags=0x{flags:08X}"]
    warnings: list[str] = []

    if has_clut:
        if pos + 12 > n:
            return None
        clut_size = u32le(data, pos)
        if clut_size < 12 or clut_size % 2 != 0:
            return None
        clut_end = pos + clut_size
        if clut_end > n:
            return None
        clut_x = u16le(data, pos + 4)
        clut_y = u16le(data, pos + 6)
        clut_w = u16le(data, pos + 8)
        clut_h = u16le(data, pos + 10)
        clut_expected = 12 + clut_w * clut_h * 2
        if clut_expected != clut_size:
            warnings.append(
                f"CLUT block size {clut_size} != expected {clut_expected} "
                f"from {clut_w}x{clut_h}"
            )
        notes_parts.append(
            f"clut={clut_w}x{clut_h}@({clut_x},{clut_y}) block={clut_size}"
        )
        pos = clut_end

    if pos + 12 > n:
        return None
    img_size = u32le(data, pos)
    if img_size < 12 or img_size % 2 != 0:
        return None
    img_end = pos + img_size
    if img_end > n:
        return None

    img_x = u16le(data, pos + 4)
    img_y = u16le(data, pos + 6)
    img_w_words = u16le(data, pos + 8)
    img_h = u16le(data, pos + 10)
    if img_w_words == 0 or img_h == 0:
        return None

    raw_expected = img_w_words * 2 * img_h
    raw_actual = img_size - 12
    if raw_expected != raw_actual:
        warnings.append(
            f"image block data {raw_actual} bytes != expected {raw_expected} "
            f"from w_words={img_w_words}, h={img_h}"
        )

    if pixmode == 0:
        fmt = "4bpp"
        pixel_w = img_w_words * 4
    elif pixmode == 1:
        fmt = "8bpp"
        pixel_w = img_w_words * 2
    elif pixmode == 2:
        fmt = "16bpp"
        pixel_w = img_w_words
    else:
        fmt = "24bpp"
        # TIM stores row width as 16-bit words. 24bpp pixel width may be fractional
        # if padding is present, so report the exact word width too.
        pixel_w = (img_w_words * 2) // 3

    notes_parts.append(
        f"format={fmt}; pixels~={pixel_w}x{img_h}; "
        f"image_words={img_w_words}x{img_h}@({img_x},{img_y}) block={img_size}"
    )

    return Candidate(
        offset=off,
        end=img_end,
        kind="tim",
        extension=".TIM",
        confidence="high",
        notes="; ".join(notes_parts),
        warning=" | ".join(warnings),
    )


def parse_png(data: bytes, off: int) -> Optional[Candidate]:
    n = len(data)
    if not data.startswith(PNG_MAGIC, off):
        return None
    pos = off + len(PNG_MAGIC)
    width = height = None
    while pos + 12 <= n:
        length = u32be(data, pos)
        ctype = data[pos + 4:pos + 8]
        chunk_end = pos + 12 + length
        if chunk_end > n:
            return None
        if ctype == b"IHDR" and length >= 8:
            width = u32be(data, pos + 8)
            height = u32be(data, pos + 12)
        pos = chunk_end
        if ctype == b"IEND":
            notes = ""
            if width is not None and height is not None:
                notes = f"pixels={width}x{height}"
            return Candidate(off, pos, "png", ".png", "high", notes)
    return None


def parse_bmp(data: bytes, off: int) -> Optional[Candidate]:
    n = len(data)
    if off + 14 > n or data[off:off + 2] != b"BM":
        return None
    size = u32le(data, off + 2)
    if size < 14 or off + size > n:
        return None
    width = height = None
    if off + 26 <= n:
        dib_size = u32le(data, off + 14)
        if dib_size >= 12 and off + 14 + dib_size <= n:
            if dib_size == 12:
                width = u16le(data, off + 18)
                height = u16le(data, off + 20)
            elif dib_size >= 40:
                width = struct.unpack_from("<i", data, off + 18)[0]
                height = struct.unpack_from("<i", data, off + 22)[0]
    notes = f"declared_size={size}"
    if width is not None and height is not None:
        notes += f"; pixels={width}x{height}"
    return Candidate(off, off + size, "bmp", ".bmp", "medium", notes)


def parse_riff(data: bytes, off: int) -> Optional[Candidate]:
    n = len(data)
    if off + 12 > n or data[off:off + 4] != b"RIFF":
        return None
    size = u32le(data, off + 4) + 8
    if size < 12 or off + size > n:
        return None
    form = data[off + 8:off + 12]
    form_text = ascii_safe(form)
    ext = {
        b"WAVE": ".wav",
        b"AVI ": ".avi",
        b"WEBP": ".webp",
        b"RMID": ".rmi",
    }.get(form, ".riff")
    return Candidate(off, off + size, "riff", ext, "medium", f"form={form_text}; declared_size={size}")


def parse_form(data: bytes, off: int) -> Optional[Candidate]:
    n = len(data)
    if off + 12 > n or data[off:off + 4] != b"FORM":
        return None
    size = u32be(data, off + 4) + 8
    if size < 12 or off + size > n:
        return None
    form = data[off + 8:off + 12]
    form_text = ascii_safe(form)
    ext = ".aiff" if form in (b"AIFF", b"AIFC") else ".iff"
    return Candidate(off, off + size, "form", ext, "medium", f"form={form_text}; declared_size={size}")


def parse_gif(data: bytes, off: int) -> Optional[Candidate]:
    n = len(data)
    if off + 13 > n or data[off:off + 6] not in GIF_MAGICS:
        return None
    pos = off + 6
    width = u16le(data, pos)
    height = u16le(data, pos + 2)
    packed = data[pos + 4]
    pos += 7
    if packed & 0x80:
        gct_size = 3 * (2 ** ((packed & 0x07) + 1))
        pos += gct_size
        if pos > n:
            return None
    while pos < n:
        b = data[pos]
        pos += 1
        if b == 0x3B:  # trailer
            return Candidate(off, pos, "gif", ".gif", "medium", f"pixels={width}x{height}")
        if b == 0x2C:  # image descriptor
            if pos + 9 > n:
                return None
            local_packed = data[pos + 8]
            pos += 9
            if local_packed & 0x80:
                lct_size = 3 * (2 ** ((local_packed & 0x07) + 1))
                pos += lct_size
                if pos > n:
                    return None
            if pos >= n:
                return None
            pos += 1  # LZW minimum code size
            pos = skip_gif_subblocks(data, pos)
            if pos is None:
                return None
        elif b == 0x21:  # extension block
            if pos >= n:
                return None
            pos += 1  # extension label
            pos = skip_gif_subblocks(data, pos)
            if pos is None:
                return None
        else:
            return None
    return None


def skip_gif_subblocks(data: bytes, pos: int) -> Optional[int]:
    n = len(data)
    while pos < n:
        size = data[pos]
        pos += 1
        if size == 0:
            return pos
        pos += size
        if pos > n:
            return None
    return None


def parse_zip(data: bytes, off: int) -> Optional[Candidate]:
    # ZIP files begin with a local file header, but the archive size is most
    # reliably found from the EOCD record. Search forward for EOCD candidates.
    n = len(data)
    if not data.startswith(ZIP_LOCAL_MAGIC, off):
        return None
    search_pos = off + 4
    best_end: Optional[int] = None
    while True:
        eocd = data.find(ZIP_EOCD_MAGIC, search_pos)
        if eocd < 0:
            break
        if eocd + 22 <= n:
            comment_len = u16le(data, eocd + 20)
            end = eocd + 22 + comment_len
            if end <= n:
                best_end = end
                break
        search_pos = eocd + 1
    if best_end is None:
        return None
    return Candidate(off, best_end, "zip", ".zip", "medium", f"declared_end=0x{best_end:X}")


def parse_ogg(data: bytes, off: int) -> Optional[Candidate]:
    n = len(data)
    if not data.startswith(OGG_MAGIC, off):
        return None
    pos = off
    pages = 0
    end = None
    while pos + 27 <= n and data.startswith(OGG_MAGIC, pos):
        header_type = data[pos + 5]
        seg_count = data[pos + 26]
        if pos + 27 + seg_count > n:
            return None
        segs = data[pos + 27:pos + 27 + seg_count]
        page_size = 27 + seg_count + sum(segs)
        if pos + page_size > n:
            return None
        pages += 1
        pos += page_size
        if header_type & 0x04:  # end of stream
            end = pos
            break
    if end is None:
        return None
    return Candidate(off, end, "ogg", ".ogg", "medium", f"pages={pages}")


def parse_gzip_header(data: bytes, off: int, next_known: Optional[int] = None) -> Optional[Candidate]:
    # gzip does not include a simple uncompressed/compressed stream length in the
    # header. This parser validates the header and, if used, carves only until the
    # next known candidate or EOF. Mark confidence low.
    n = len(data)
    if off + 10 > n or data[off:off + 2] != GZIP_MAGIC:
        return None
    method = data[off + 2]
    flags = data[off + 3]
    if method != 8 or flags & 0xE0:
        return None
    pos = off + 10
    if flags & 0x04:
        if pos + 2 > n:
            return None
        xlen = u16le(data, pos)
        pos += 2 + xlen
    if flags & 0x08:
        zero = data.find(b"\x00", pos)
        if zero < 0:
            return None
        pos = zero + 1
    if flags & 0x10:
        zero = data.find(b"\x00", pos)
        if zero < 0:
            return None
        pos = zero + 1
    if flags & 0x02:
        pos += 2
    if pos >= n:
        return None
    end = next_known if next_known is not None and next_known > off else n
    return Candidate(off, end, "gzip", ".gz", "low", "gzip header valid; size is estimated")


PARSERS: dict[str, Callable[[bytes, int], Optional[Candidate]]] = {
    "tim": parse_tim,
    "png": parse_png,
    "bmp": parse_bmp,
    "gif": parse_gif,
    "riff": parse_riff,
    "form": parse_form,
    "zip": parse_zip,
    "ogg": parse_ogg,
}

SIGNATURE_FIRST_BYTES: dict[int, set[str]] = {}
for name, sigs in {
    "tim": [TIM_MAGIC],
    "png": [PNG_MAGIC],
    "bmp": [b"BM"],
    "gif": list(GIF_MAGICS),
    "riff": [b"RIFF"],
    "form": [b"FORM"],
    "zip": [ZIP_LOCAL_MAGIC],
    "ogg": [OGG_MAGIC],
}.items():
    for sig in sigs:
        SIGNATURE_FIRST_BYTES.setdefault(sig[0], set()).add(name)
SIGNATURE_FIRST_BYTES.setdefault(GZIP_MAGIC[0], set()).add("gzip")


def find_candidates(data: bytes, enabled_types: set[str]) -> list[Candidate]:
    candidates: list[Candidate] = []
    n = len(data)
    off = 0

    while off < n:
        possible = SIGNATURE_FIRST_BYTES.get(data[off], set()) & enabled_types
        found: list[Candidate] = []
        for typ in sorted(possible):
            if typ == "gzip":
                # gzip length depends on knowing the next record; handle later.
                continue
            parser = PARSERS.get(typ)
            if not parser:
                continue
            cand = parser(data, off)
            if cand is not None and cand.end > off:
                found.append(cand)

        if found:
            # Prefer high confidence, then longer file, then deterministic type name.
            found.sort(key=lambda c: ({"high": 3, "medium": 2, "low": 1}.get(c.confidence, 0), c.size, c.kind), reverse=True)
            cand = found[0]
            candidates.append(cand)
            off = cand.end
            continue

        off += 1

    # Optional gzip pass: because gzip size cannot be known from header alone,
    # carve from gzip header to the next already-detected candidate or EOF.
    if "gzip" in enabled_types:
        known_offsets = sorted(c.offset for c in candidates)
        gzip_candidates: list[Candidate] = []
        off = 0
        while True:
            off = data.find(GZIP_MAGIC, off)
            if off < 0:
                break
            if not any(c.offset <= off < c.end for c in candidates):
                next_known = next((x for x in known_offsets if x > off), None)
                cand = parse_gzip_header(data, off, next_known)
                if cand is not None:
                    gzip_candidates.append(cand)
            off += 1
        candidates.extend(gzip_candidates)

    return merge_non_overlapping(candidates)


def merge_non_overlapping(candidates: list[Candidate]) -> list[Candidate]:
    # Sort by offset; where candidates overlap, keep the one that starts earlier.
    # If same start, prefer confidence and larger size.
    candidates.sort(key=lambda c: (c.offset, -{"high": 3, "medium": 2, "low": 1}.get(c.confidence, 0), -c.size))
    accepted: list[Candidate] = []
    for cand in candidates:
        if not accepted:
            accepted.append(cand)
            continue
        prev = accepted[-1]
        if cand.offset >= prev.end:
            accepted.append(cand)
            continue
        # Overlap. Replace only if same offset and clearly better.
        prev_score = {"high": 3, "medium": 2, "low": 1}.get(prev.confidence, 0)
        cand_score = {"high": 3, "medium": 2, "low": 1}.get(cand.confidence, 0)
        if cand.offset == prev.offset and (cand_score, cand.size) > (prev_score, prev.size):
            accepted[-1] = cand
    return accepted


def write_bytes(path: Path, blob: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(blob)


def safe_stem(path: Path) -> str:
    return path.stem.replace(" ", "_")


def extract_one(
    input_path: Path,
    out_root: Path,
    enabled_types: set[str],
    save_gaps: bool,
    min_gap: int,
    dry_run: bool,
) -> list[CarvedFile]:
    data = input_path.read_bytes()
    candidates = find_candidates(data, enabled_types)
    stem = safe_stem(input_path)
    out_dir = out_root if len(input_paths_global) == 1 else out_root / stem
    records: list[CarvedFile] = []

    def add_record(cand: Candidate, suffix_kind: str = "") -> None:
        idx = len(records)
        name_kind = cand.kind if not suffix_kind else suffix_kind
        filename = f"{idx:04d}_off_{cand.offset:08X}_{name_kind}{cand.extension}"
        if not dry_run:
            write_bytes(out_dir / filename, data[cand.offset:cand.end])
        records.append(CarvedFile(
            index=idx,
            offset=cand.offset,
            end=cand.end,
            size=cand.size,
            kind=cand.kind,
            extension=cand.extension,
            filename=filename,
            confidence=cand.confidence,
            notes=cand.notes,
            warning=cand.warning,
        ))

    pos = 0
    for cand in candidates:
        if save_gaps and cand.offset > pos and cand.offset - pos >= min_gap:
            gap = Candidate(
                offset=pos,
                end=cand.offset,
                kind="unknown_gap",
                extension=".bin",
                confidence="unknown",
                notes=f"unidentified bytes before next carved file at 0x{cand.offset:X}",
            )
            add_record(gap, "unknown_gap")
        add_record(cand)
        pos = cand.end

    if save_gaps and pos < len(data) and len(data) - pos >= min_gap:
        gap = Candidate(
            offset=pos,
            end=len(data),
            kind="unknown_gap",
            extension=".bin",
            confidence="unknown",
            notes="unidentified trailing bytes",
        )
        add_record(gap, "unknown_gap")

    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest_csv = out_dir / f"{stem}_manifest.csv"
        manifest_json = out_dir / f"{stem}_manifest.json"
        with manifest_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(asdict(records[0]).keys()) if records else [
                "index", "offset", "end", "size", "kind", "extension", "filename", "confidence", "notes", "warning"
            ])
            writer.writeheader()
            for r in records:
                writer.writerow(asdict(r))
        with manifest_json.open("w", encoding="utf-8") as f:
            json.dump([asdict(r) for r in records], f, indent=2)

    return records


def parse_types(types_text: str) -> set[str]:
    aliases = {
        "all": set(PARSERS) | {"gzip"},
        "images": {"tim", "png", "bmp", "gif"},
        "audio": {"riff", "form", "ogg"},
    }
    requested: set[str] = set()
    for part in types_text.split(","):
        p = part.strip().lower()
        if not p:
            continue
        if p in aliases:
            requested |= aliases[p]
        else:
            requested.add(p)
    valid = set(PARSERS) | {"gzip"}
    unknown = requested - valid
    if unknown:
        raise SystemExit(f"Unknown --types value(s): {', '.join(sorted(unknown))}. Valid: {', '.join(sorted(valid | set(aliases)))}")
    return requested or aliases["all"]


def print_summary(input_path: Path, records: list[CarvedFile], dry_run: bool) -> None:
    print(f"\n{input_path}")
    print(f"  carved records: {len(records)}" + (" [dry run]" if dry_run else ""))
    counts: dict[str, int] = {}
    for r in records:
        counts[r.kind] = counts.get(r.kind, 0) + 1
    if counts:
        print("  by type: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    for r in records:
        note = f" | {r.notes}" if r.notes else ""
        warn = f" | WARNING: {r.warning}" if r.warning else ""
        print(f"  [{r.index:04d}] 0x{r.offset:08X}-0x{r.end:08X} {r.size:8d} {r.kind:11s} {r.filename}{note}{warn}")



def collect_dat_inputs_from_folders(inputs: list[Path], recursive: bool) -> tuple[list[Path], dict[Path, Path]]:
    """Expand folder inputs to .DAT files.

    Returns:
      dat_files: sorted list of DAT files
      rel_roots: mapping from DAT file to the folder root it came from

    If a normal file is provided alongside folders, it is included as-is.
    """
    dat_files: list[Path] = []
    rel_roots: dict[Path, Path] = {}

    for item in inputs:
        item = Path(item)
        if item.is_dir():
            found = sorted(item.rglob("*.DAT") if recursive else item.glob("*.DAT"))
            for f in found:
                f = f.resolve()
                dat_files.append(f)
                rel_roots[f] = item.resolve()
        elif item.is_file():
            f = item.resolve()
            if item.suffix.upper() == ".DAT":
                dat_files.append(f)
                rel_roots[f] = item.parent.resolve()
            else:
                raise SystemExit(f"--all-in-folder file input is not a .DAT: {item}")
        else:
            raise SystemExit(f"Input not found: {item}")

    # Deduplicate while preserving sort order.
    out: list[Path] = []
    seen: set[Path] = set()
    for f in sorted(dat_files):
        if f not in seen:
            seen.add(f)
            out.append(f)

    return out, rel_roots


def folder_output_dir(out_root: Path, dat_path: Path, root: Path) -> Path:
    """Output path for folder extraction, preserving relative subfolders."""
    try:
        rel = dat_path.relative_to(root)
    except ValueError:
        rel = Path(dat_path.name)
    return out_root / rel.with_suffix("")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generic conservative extractor/carver for DAT containers and concatenated game-resource files."
    )
    parser.add_argument("inputs", nargs="+", type=Path, help=".DAT or other binary container(s) to scan")
    parser.add_argument("--all-in-folder", action="store_true", help="Treat input path(s) as folder(s), recursively find every .DAT, and extract each to its own output folder")
    parser.add_argument("--no-recursive", dest="recursive", action="store_false", help="With --all-in-folder, only scan the top level of each folder")
    parser.set_defaults(recursive=True)
    parser.add_argument("--continue-on-error", action="store_true", help="With --all-in-folder, keep extracting other DATs if one fails")
    parser.add_argument("-o", "--out", type=Path, default=None, help="output directory; default: <input_stem>_extract or dat_extract for multiple inputs")
    parser.add_argument("--types", default="all", help="comma list: all,tim,png,bmp,gif,riff,form,ogg,zip,gzip,images,audio")
    parser.add_argument("--save-gaps", action="store_true", help="also save unidentified byte ranges between recognized files")
    parser.add_argument("--min-gap", type=int, default=1, help="minimum unknown gap size to save when --save-gaps is enabled")
    parser.add_argument("--dry-run", action="store_true", help="scan and print manifest without writing extracted files")
    args = parser.parse_args()

    enabled_types = parse_types(args.types)

    global input_paths_global

    if args.all_in_folder:
        dat_files, rel_roots = collect_dat_inputs_from_folders(args.inputs, args.recursive)
        if not dat_files:
            print("No .DAT files found.")
            return 0

        out_root = args.out if args.out is not None else Path("dat_extract")
        summary_rows = []
        ok_count = 0
        fail_count = 0

        print(f"Found {len(dat_files)} .DAT file(s).")
        print(f"Output root: {out_root}")

        for input_path in dat_files:
            root = rel_roots.get(input_path, input_path.parent)
            actual_out = folder_output_dir(out_root, input_path, root)

            try:
                # Force extract_one to write directly into this DAT's unique folder.
                input_paths_global = [input_path]
                records = extract_one(
                    input_path=input_path,
                    out_root=actual_out,
                    enabled_types=enabled_types,
                    save_gaps=args.save_gaps,
                    min_gap=args.min_gap,
                    dry_run=args.dry_run,
                )
                print_summary(input_path, records, args.dry_run)
                if not args.dry_run:
                    print(f"  wrote: {actual_out}")

                summary_rows.append({
                    "input": str(input_path),
                    "output": str(actual_out),
                    "records": len(records),
                    "status": "ok",
                    "notes": "",
                })
                ok_count += 1

            except Exception as exc:
                print(f"\nERROR extracting {input_path}: {exc}")
                summary_rows.append({
                    "input": str(input_path),
                    "output": str(actual_out),
                    "records": 0,
                    "status": "error",
                    "notes": str(exc),
                })
                fail_count += 1
                if not args.continue_on_error:
                    break

        if not args.dry_run:
            out_root.mkdir(parents=True, exist_ok=True)
            summary_csv = out_root / "_dat_folder_extract_summary.csv"
            with summary_csv.open("w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=["input", "output", "records", "status", "notes"])
                writer.writeheader()
                writer.writerows(summary_rows)
            print(f"\nSummary written: {summary_csv}")

        print("")
        print("DAT folder extract complete")
        print("---------------------------")
        print(f"Found:     {len(dat_files)}")
        print(f"Extracted: {ok_count}")
        print(f"Failed:    {fail_count}")
        return 0 if fail_count == 0 else 1

    inputs = args.inputs
    for p in inputs:
        if not p.is_file():
            raise SystemExit(f"Input not found or not a file: {p}")

    input_paths_global = inputs

    if args.out is not None:
        out_root = args.out
    elif len(inputs) == 1:
        out_root = inputs[0].with_name(inputs[0].stem + "_extract")
    else:
        out_root = Path("dat_extract")

    for input_path in inputs:
        records = extract_one(
            input_path=input_path,
            out_root=out_root,
            enabled_types=enabled_types,
            save_gaps=args.save_gaps,
            min_gap=args.min_gap,
            dry_run=args.dry_run,
        )
        print_summary(input_path, records, args.dry_run)
        if not args.dry_run:
            actual_out = out_root if len(inputs) == 1 else out_root / safe_stem(input_path)
            print(f"  wrote: {actual_out}")

    return 0


# Used only to decide output layout inside extract_one(). It is set in main().
input_paths_global: list[Path] = []


if __name__ == "__main__":
    raise SystemExit(main())
