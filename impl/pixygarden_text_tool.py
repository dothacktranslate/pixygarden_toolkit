#!/usr/bin/env python3
"""
PixyGarden text and script tool.

Purpose
-------
This front-end groups workbook-to-text builders, TREE/HELP rebuilders, string
extraction, and EVENT/SCR script rebuilding.

Usage
-----
    python tools/pixygarden_text_tool.py <command> [command options]

Run a command with --help to see the underlying options:
    python tools/pixygarden_text_tool.py txt-build --help
    python tools/pixygarden_text_tool.py scr --help

Commands
--------
strings
    Extract strings from binaries and archives into reviewable data.

txt-build
    Build TXT replacement files from a translation workbook. This is the
    general workbook-to-TXT builder.

tree-txt-build
    Build TREE TXT files from a TREE Strings workbook sheet.

tree-cdf-build
    Rebuild TREE.CDF directly from a workbook and source TREE.CDF. This command
    targets DETAILS.FAT/INFO.FAT by default and writes an updated CDF.

planet-help-cdf-build
    Rebuild the HELP.FAT container inside PLANET.CDF from workbook text.

stage-help-fat-build
    Rebuild a standalone DATA/STAGE HELP.FAT from workbook text.

scr
    Extract and rebuild SCR files. EVENT.CDF template-safe mode is available
    through this command and should be used when EVENT script control bytes must
    be preserved.
"""
from __future__ import annotations

import sys
from _launcher import print_command_table, run_implementation

COMMANDS: dict[str, tuple[str, str]] = {
    "strings": ("pixygarden_String_Extractor.py", "Extract strings for workbook/review workflows."),
    "txt-build": ("pixygarden_TXT_Builder.py", "Build replacement TXT files from a workbook."),
    "tree-txt-build": ("pixygarden_TREE_TXT_Builder.py", "Build TREE TXT files from a TREE Strings sheet."),
    "tree-cdf-build": ("pixygarden_TREE_TXT_CDF_Builder.py", "Patch TREE.CDF from workbook text."),
    "planet-help-cdf-build": ("pixygarden_PLANET_HELP_CDF_Builder.py", "Patch HELP.FAT inside PLANET.CDF."),
    "stage-help-fat-build": ("pixygarden_STAGE_HELP_FAT_Builder.py", "Patch standalone stage/DATA HELP.FAT."),
    "scr": ("pixygarden_SCR_Repacker.py", "Extract/rebuild SCR and EVENT.CDF script text."),
}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print_command_table("PixyGarden text tool", COMMANDS)
        return 0

    command = args.pop(0)
    if command not in COMMANDS:
        print(f"Unknown text command: {command}", file=sys.stderr)
        print("", file=sys.stderr)
        print_command_table("PixyGarden text tool", COMMANDS)
        return 2

    script_name, _description = COMMANDS[command]
    return run_implementation(script_name, args)


if __name__ == "__main__":
    raise SystemExit(main())
