#!/usr/bin/env python3
"""
PixyGarden Text Extractor

It extracts the major written-text sources:

  - MAIN.EXE     : Shift-JIS / CP932 null-terminated strings in known text ranges
  - EVENT.CDF    : SCR message commands of the form 11 LL 63 37 [text] 00 00
  - PLANET.CDF   : nested FAT/TXT text files
  - TREE.CDF     : nested FAT/TXT text files

It outputs CSV files with offsets, decoded Japanese text, and raw bytes.

Notes:
  - This script is meant for extraction, not insertion.
  - MAIN.EXE uses RAM pointers; file offset -> pointer is:
        pointer = 0x80020000 + (file_offset - 0x800)
      equivalently:
        pointer = file_offset + 0x8001F800
  - EVENT.CDF strings may vary slightly depending on how strict you make the
    SCR-command filter. This script extracts high-confidence 11 LL 63 37
    message commands containing Japanese text.
"""

from __future__ import annotations

import argparse
import csv
import re
import struct
from pathlib import Path
from typing import Iterable


SECTOR_SIZE = 0x800
CP932 = "cp932"


# MAIN.EXE text areas observed during extraction.
# These ranges are deliberately narrow to avoid thousands of false positives
# from interpreting MIPS/code bytes as CP932.
MAIN_TEXT_RANGES = [
    (0x0008FC, 0x000E40),
    (0x0017BC, 0x001B04),
    (0x001D58, 0x0020F0),
    (0x00232C, 0x002BDC),
    (0x002D4C, 0x002D4C),
    (0x002E9C, 0x00334C),
    (0x003728, 0x003E34),
    (0x004BBC, 0x006638),
    (0x007540, 0x007A00),
    (0x0ADD84, 0x0ADDAC),
    (0x0BAD84, 0x0BADA8),
    (0x0BAF78, 0x0BAF80),
]


def hex_bytes(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def decode_cp932(raw: bytes) -> str | None:
    try:
        return raw.decode(CP932)
    except UnicodeDecodeError:
        return None


def has_japaneseish(text: str) -> bool:
    """Return True for strings containing Japanese/fullwidth/CJK-style chars."""
    for ch in text:
        if (
            "\u3040" <= ch <= "\u30ff" or  # hiragana/katakana
            "\u3400" <= ch <= "\u9fff" or  # CJK
            "\u3000" <= ch <= "\u303f" or  # Japanese punctuation
            "\uff00" <= ch <= "\uffef"     # fullwidth/halfwidth forms
        ):
            return True
    return False


def has_private_use_garbage(text: str) -> bool:
    # CP932 false positives often decode to private-use chars.
    return any("\uf000" <= ch <= "\uf8ff" for ch in text)


def safe_decoded_text(text: str) -> str:
    # Keep literal backslash-n visible in spreadsheets/CSVs.
    return text.replace("\r", "\\r").replace("\n", "\\n")


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def scan_null_terminated_cp932_strings(
    data: bytes,
    ranges: Iterable[tuple[int, int]],
    pointer_base: int,
) -> list[dict]:
    """
    Scan known MAIN.EXE text ranges for null-terminated CP932 strings.

    The ranges are inclusive and represent observed text clusters.
    """
    rows: list[dict] = []
    seen_offsets: set[int] = set()

    for start, end in ranges:
        pos = start
        end = min(end, len(data) - 1)

        while pos <= end:
            # Text strings generally begin at pos and end at first NUL.
            nul = data.find(b"\x00", pos, min(len(data), end + 0x400))
            if nul < 0 or nul <= pos:
                pos += 1
                continue

            raw = data[pos:nul]
            text = decode_cp932(raw)

            if (
                text
                and len(raw) >= 4
                and has_japaneseish(text)
                and not has_private_use_garbage(text)
            ):
                # Avoid catching every byte inside a valid string as a shifted
                # false start. Prefer starts after NUL, at range start, or
                # obvious control prefix starts.
                prev_ok = pos == start or pos == 0 or data[pos - 1] == 0
                prefix_ok = raw.startswith(b"v3c7")
                if prev_ok or prefix_ok:
                    seen_offsets.add(pos)
                    rows.append({
                        "pointer": f"0x{pointer_base + pos:08X}",
                        "file_offset": f"0x{pos:X}",
                        "string_decoded": safe_decoded_text(text),
                        "string_bytes": hex_bytes(raw),
                        "english_translation": "",
                    })
                    pos = nul + 1
                    continue

            pos += 1

    # Sort and deduplicate.
    dedup = {}
    for row in rows:
        dedup[row["file_offset"]] = row

    return [dedup[k] for k in sorted(dedup, key=lambda x: int(x, 16))]


def parse_psx_exe_pointer_base(data: bytes) -> int:
    """
    Return the pointer base for a PS-X EXE file.

    PS-X EXE has a 0x800-byte header. Payload is loaded at the load address
    stored at 0x18. Therefore:
        runtime_pointer = load_address + (file_offset - 0x800)
    """
    if data[:8] != b"PS-X EXE":
        raise ValueError("Not a PS-X EXE file")
    load_addr = struct.unpack_from("<I", data, 0x18)[0]
    return load_addr - 0x800


def extract_main_exe(path: Path) -> list[dict]:
    data = path.read_bytes()
    pointer_base = parse_psx_exe_pointer_base(data)
    return scan_null_terminated_cp932_strings(data, MAIN_TEXT_RANGES, pointer_base)


def clean_ascii_name(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("ascii", "ignore")


def scan_cdf_name_first_entries(data: bytes, max_scan: int = 0x20000) -> list[dict]:
    """
    Find CDF table entries where the structure is:

        char filename[20]
        u32  start_sector
        u32  sector_count
        u32  byte_size

    This finds the relevant .SCR/.FAT entries used by EVENT/PLANET/TREE.
    Some other archive entries use slightly different layouts; those are not
    needed for string extraction here.
    """
    exts = rb"(?:SCR|FAT|TXT|TIM|SEQ|VB|VH|DAT|ANM)"
    pattern = re.compile(rb"[A-Z0-9_]+\." + exts)

    rows: list[dict] = []
    seen: set[tuple[str, int, int, int]] = set()

    scan_limit = min(len(data), max_scan)
    for match in pattern.finditer(data[:scan_limit]):
        name_start = match.start()
        if name_start + 32 > len(data):
            continue

        name_raw = data[name_start:name_start + 20]
        name = clean_ascii_name(name_raw)
        if not re.fullmatch(r"[A-Z0-9_]+\.(SCR|FAT|TXT|TIM|SEQ|VB|VH|DAT|ANM)", name):
            continue

        start_sector, sector_count, byte_size = struct.unpack_from("<III", data, name_start + 20)
        data_offset = start_sector * SECTOR_SIZE

        plausible = (
            0 <= data_offset < len(data)
            and 0 < byte_size <= (sector_count * SECTOR_SIZE + SECTOR_SIZE)
            and 0 < sector_count < 0x10000
        )
        if not plausible:
            continue

        key = (name, data_offset, byte_size, name_start)
        if key in seen:
            continue
        seen.add(key)

        rows.append({
            "name": name,
            "table_offset": name_start,
            "data_offset": data_offset,
            "sector_count": sector_count,
            "byte_size": byte_size,
        })

    rows.sort(key=lambda r: (r["data_offset"], r["name"]))
    return rows


def parse_nested_fat(data: bytes, base_abs_offset: int, path_prefix: str = "") -> list[dict]:
    """
    Parse the nested FAT format used by PLANET/TREE resources:

        char filename[16]
        u32  relative_offset

    Returns all immediate and recursively nested files.
    """
    entries: list[dict] = []
    pos = 0
    last_offset = -1

    while pos + 20 <= len(data):
        raw_name = data[pos:pos + 16]
        if raw_name[:1] in (b"\x00", b"\xff") or raw_name.strip(b"\x00\xff ") == b"":
            break

        try:
            name = raw_name.split(b"\x00", 1)[0].decode("ascii")
        except UnicodeDecodeError:
            break

        if not re.fullmatch(r"[A-Za-z0-9_]+\.[A-Za-z0-9_]+", name):
            break

        rel_offset = struct.unpack_from("<I", data, pos + 16)[0]
        if rel_offset < 0 or rel_offset >= len(data) or rel_offset < last_offset:
            break

        entries.append({
            "name": name,
            "rel_offset": rel_offset,
            "entry_offset": pos,
        })

        last_offset = rel_offset
        pos += 20

    out: list[dict] = []
    if not entries:
        return out

    offsets = [e["rel_offset"] for e in entries] + [len(data)]

    for i, entry in enumerate(entries):
        rel = entry["rel_offset"]
        next_rel = offsets[i + 1]
        if next_rel <= rel or next_rel > len(data):
            next_rel = len(data)

        full_path = f"{path_prefix}/{entry['name']}" if path_prefix else entry["name"]
        abs_offset = base_abs_offset + rel
        file_data = data[rel:next_rel]

        row = {
            "path": full_path,
            "absolute_offset": abs_offset,
            "size": len(file_data),
            "data": file_data,
        }
        out.append(row)

        if entry["name"].upper().endswith(".FAT"):
            out.extend(parse_nested_fat(file_data, abs_offset, full_path))

    return out


def extract_event_cdf(path: Path) -> list[dict]:
    """
    Extract event/script strings from .SCR files inside EVENT.CDF.

    The high-confidence message command found during analysis is:

        11 LL 63 37 [CP932 text bytes] 00 00

    LL appears to be text_byte_length + 6.
    """
    data = path.read_bytes()
    rows: list[dict] = []

    for entry in scan_cdf_name_first_entries(data):
        if not entry["name"].upper().endswith(".SCR"):
            continue

        script = data[entry["data_offset"]:entry["data_offset"] + entry["byte_size"]]

        for off in range(0, max(0, len(script) - 6)):
            if script[off] != 0x11:
                continue
            length_byte = script[off + 1]
            if script[off + 2:off + 4] != b"c7":
                continue

            text_len = length_byte - 6
            if text_len <= 0:
                continue

            text_start = off + 4
            text_end = text_start + text_len
            if text_end > len(script):
                continue

            raw = script[text_start:text_end]
            text = decode_cp932(raw)
            if not text or not has_japaneseish(text) or has_private_use_garbage(text):
                continue

            command_bytes = script[off:text_end]
            rows.append({
                "script_file": entry["name"],
                "cdf_offset": f"0x{entry['data_offset'] + off:X}",
                "script_offset": f"0x{off:X}",
                "length_byte": f"0x{length_byte:02X}",
                "control_prefix_bytes": hex_bytes(script[off:off + 4]),
                "string_decoded": safe_decoded_text(text),
                "string_bytes": hex_bytes(raw),
                "message_bytes": hex_bytes(command_bytes),
                "english_translation": "",
            })

    return rows


def extract_planet_tree_cdf(path: Path) -> list[dict]:
    """
    Extract nested .TXT strings from PLANET.CDF or TREE.CDF.

    These .TXT files generally end with byte 0x39 followed by 0xFF padding.
    """
    data = path.read_bytes()
    rows: list[dict] = []

    for entry in scan_cdf_name_first_entries(data):
        if not entry["name"].upper().endswith(".FAT"):
            continue

        fat_blob = data[entry["data_offset"]:entry["data_offset"] + entry["byte_size"]]
        nested_files = parse_nested_fat(fat_blob, entry["data_offset"], entry["name"])

        for nested in nested_files:
            if not nested["path"].upper().endswith(".TXT"):
                continue

            raw_file = nested["data"]
            raw_text = raw_file.rstrip(b"\xFF\x00")
            terminator = ""

            if raw_text.endswith(b"9"):
                raw_text = raw_text[:-1]
                terminator = "39"

            text = decode_cp932(raw_text)
            if not text or not has_japaneseish(text) or has_private_use_garbage(text):
                continue

            rows.append({
                "source_cdf": path.name,
                "archive_path": nested["path"],
                "absolute_cdf_offset": f"0x{nested['absolute_offset']:X}",
                "text_offset_in_file": "0x0",
                "original_file_size": str(nested["size"]),
                "text_byte_length": str(len(raw_text)),
                "terminator_byte": terminator,
                "string_decoded": safe_decoded_text(text),
                "string_bytes": hex_bytes(raw_text),
                "english_translation": "",
            })

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract PixyGarden Japanese strings to CSV.")
    parser.add_argument(
        "input_dir",
        nargs="?",
        default=".",
        help="Directory containing MAIN.EXE, EVENT.CDF, PLANET.CDF, TREE.CDF",
    )
    parser.add_argument(
        "-o", "--output-dir",
        default="pixygarden_extracted_text",
        help="Directory for CSV output",
    )

    args = parser.parse_args()
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    jobs = []

    main_exe = input_dir / "MAIN.EXE"
    if main_exe.exists():
        rows = extract_main_exe(main_exe)
        out = output_dir / "pixygarden_main_exe_strings.csv"
        write_csv(out, ["pointer", "file_offset", "string_decoded", "string_bytes", "english_translation"], rows)
        jobs.append(("MAIN.EXE", len(rows), out))

    event_cdf = input_dir / "EVENT.CDF"
    if event_cdf.exists():
        rows = extract_event_cdf(event_cdf)
        out = output_dir / "pixygarden_event_cdf_strings.csv"
        write_csv(out, [
            "script_file", "cdf_offset", "script_offset", "length_byte",
            "control_prefix_bytes", "string_decoded", "string_bytes",
            "message_bytes", "english_translation"
        ], rows)
        jobs.append(("EVENT.CDF", len(rows), out))

    combined_rows = []
    for cdf_name in ("PLANET.CDF", "TREE.CDF"):
        cdf_path = input_dir / cdf_name
        if not cdf_path.exists():
            continue

        rows = extract_planet_tree_cdf(cdf_path)
        combined_rows.extend(rows)

        out = output_dir / f"pixygarden_{cdf_name.lower().replace('.cdf', '')}_cdf_strings.csv"
        write_csv(out, [
            "source_cdf", "archive_path", "absolute_cdf_offset",
            "text_offset_in_file", "original_file_size",
            "text_byte_length", "terminator_byte",
            "string_decoded", "string_bytes", "english_translation"
        ], rows)
        jobs.append((cdf_name, len(rows), out))

    if combined_rows:
        out = output_dir / "pixygarden_planet_tree_cdf_strings_combined.csv"
        write_csv(out, [
            "source_cdf", "archive_path", "absolute_cdf_offset",
            "text_offset_in_file", "original_file_size",
            "text_byte_length", "terminator_byte",
            "string_decoded", "string_bytes", "english_translation"
        ], combined_rows)
        jobs.append(("PLANET+TREE combined", len(combined_rows), out))

    print("Extraction complete.")
    for name, count, path in jobs:
        print(f"{name}: {count} rows -> {path}")


if __name__ == "__main__":
    main()
