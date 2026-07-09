#!/usr/bin/env python3
"""
PixyGarden graphics and compression tool.

Purpose
-------
This front-end groups TIM extraction/injection, PixyGarden LZ compression, DAT
scanning, and TREE graphic batch rebuilding.

Usage
-----
    python tools/pixygarden_graphics_tool.py <command> [command options]

Run a command with --help to see the underlying options:
    python tools/pixygarden_graphics_tool.py tim --help
    python tools/pixygarden_graphics_tool.py lz --help

Commands
--------
tim
    Inspect TIM files, extract PNG pages, and inject edited PNG pages back into
    TIM files or containers. Indexed PNG mode is recommended for 4bpp/8bpp TIMs
    when raw palette indexes matter.

lz
    Decode, encode, test, extract, and reinsert PixyGarden LZ-compressed files
    and simple CDF members.

dat-extract
    Scan DAT or binary containers for embedded image/audio/data payloads.

tree-graphics-build
    Batch rebuild TREE.CDF graphics from edited PNGs and source TIM files.
"""
from __future__ import annotations

import sys
from _launcher import print_command_table, run_implementation

COMMANDS: dict[str, tuple[str, str]] = {
    "tim": ("pixygarden_TIM_Repacker.py", "TIM info, PNG extraction, and PNG injection."),
    "lz": ("pixygarden_LZ_TIM_Tool.py", "PixyGarden LZ decode, encode, and CDF member operations."),
    "dat-extract": ("pixygarden_DAT_Extractor.py", "Scan DAT/binary containers for embedded payloads."),
    "tree-graphics-build": ("pixygarden_TREE_CDF_Batch_Rebuilder.py", "Batch rebuild TREE.CDF TIM graphics from edited PNGs."),
}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print_command_table("PixyGarden graphics tool", COMMANDS)
        return 0

    command = args.pop(0)
    if command not in COMMANDS:
        print(f"Unknown graphics command: {command}", file=sys.stderr)
        print("", file=sys.stderr)
        print_command_table("PixyGarden graphics tool", COMMANDS)
        return 2

    script_name, _description = COMMANDS[command]
    return run_implementation(script_name, args)


if __name__ == "__main__":
    raise SystemExit(main())
