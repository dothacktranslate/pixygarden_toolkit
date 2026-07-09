#!/usr/bin/env python3
"""
PixyGarden archive and container tool.

Purpose
-------
This front-end groups archive-related commands for Disc 1 work. It covers
standalone FAT archives, top-level CDF archives, generic DAT scans, and simple
compressed CDF members.

Usage
-----
    python tools/pixygarden_archive_tool.py <command> [command options]

Run a command with --help to see the underlying options:
    python tools/pixygarden_archive_tool.py fat --help
    python tools/pixygarden_archive_tool.py cdf-extract --help

Command groups
--------------
fat
    Extract, list, and repack standalone FAT archives such as HELP.FAT and
    STAGE*.FAT. The subcommands are the FAT tool's own subcommands:
    extract, extract-folder, list, and repack.

fat-repack
    Direct standalone FAT repacker. Useful when a single repack operation is
    preferred over the FAT tool's subcommand interface.

cdf-extract
    Inspect and extract TREE-style CDF archives, including nested FAT content
    when requested.

cdf-repack
    Rebuild a CDF from an original CDF template and replacement directory.
    Smaller replacements are padded by default, making this the conservative
    option for TREE.CDF-style archives.

dat-extract
    Scan DAT or binary containers for embedded TIM, PNG, BMP, GIF, RIFF, FORM,
    OGG, ZIP, gzip, image, or audio payloads.

lz
    Run PixyGarden LZ commands, including raw decode/encode/test and simple
    compressed CDF extract/reinsert operations.
"""
from __future__ import annotations

import argparse
import sys
from _launcher import print_command_table, run_implementation

COMMANDS: dict[str, tuple[str, str]] = {
    "fat": ("pixygarden_FAT_Tool.py", "Extract, list, or repack standalone FAT archives."),
    "fat-repack": ("pixygarden_FAT_Repacker.py", "Direct recursive standalone FAT repacker."),
    "cdf-extract": ("pixygarden_CDF_Extractor.py", "Inspect or extract TREE-style CDF archives."),
    "cdf-repack": ("pixygarden_CDF_Repacker.py", "Rebuild CDF archives from replacement trees."),
    "dat-extract": ("pixygarden_DAT_Extractor.py", "Scan DAT/binary containers for embedded files."),
    "lz": ("pixygarden_LZ_TIM_Tool.py", "PixyGarden LZ decode, encode, test, extract-cdf, and reinsert-cdf."),
}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print_command_table("PixyGarden archive tool", COMMANDS)
        return 0

    command = args.pop(0)
    if command not in COMMANDS:
        print(f"Unknown archive command: {command}", file=sys.stderr)
        print("", file=sys.stderr)
        print_command_table("PixyGarden archive tool", COMMANDS)
        return 2

    script_name, _description = COMMANDS[command]
    return run_implementation(script_name, args)


if __name__ == "__main__":
    raise SystemExit(main())
