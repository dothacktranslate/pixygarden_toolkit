#!/usr/bin/env python3
"""
Shared launcher helpers for the PixyGarden Disc 1 toolkit front-end scripts.

This file is used by the merged category tools. It runs one of the tested
implementation modules from the implementation/ directory while preserving the
same command-line behavior as the original script.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

TOOLKIT_ROOT = Path(__file__).resolve().parent.parent
IMPLEMENTATION_DIR = TOOLKIT_ROOT / "implementation"


def run_implementation(script_name: str, args: list[str]) -> int:
    """Run an implementation script with a replacement argv list.

    Parameters
    ----------
    script_name:
        File name inside implementation/, for example
        ``pixygarden_FAT_Tool.py``.
    args:
        Arguments passed to the implementation script, excluding the script
        name itself.

    Returns
    -------
    int
        Process-style exit code. A missing script raises FileNotFoundError.
    """
    script_path = IMPLEMENTATION_DIR / script_name
    if not script_path.exists():
        raise FileNotFoundError(f"Implementation script not found: {script_path}")

    old_argv = sys.argv[:]
    sys.argv = [str(script_path), *args]
    try:
        try:
            runpy.run_path(str(script_path), run_name="__main__")
        except SystemExit as exc:
            code = exc.code
            if code is None:
                return 0
            if isinstance(code, int):
                return code
            print(code, file=sys.stderr)
            return 1
        return 0
    finally:
        sys.argv = old_argv


def print_command_table(title: str, commands: dict[str, tuple[str, str]]) -> None:
    """Print a compact command table for a category front-end."""
    print(title)
    print("=" * len(title))
    print()
    width = max(len(name) for name in commands) if commands else 0
    for name, (_script, description) in commands.items():
        print(f"  {name:<{width}}  {description}")
    print()
    print("Use '<command> --help' to show the implementation script's options.")
