# PixyGarden Disc 1 Translation Toolkit

This toolkit collects the latest Disc 1 scripts used for editing and translating PixyGarden.

The `tools/` directory contains the original working scripts. These scripts remain directly runnable and are the preferred entry points when exact script behavior is required.

The `impl/` directory contains grouped wrapper scripts. These wrappers route common archive, text, graphics, and executable tasks through cleaner command groups while the original scripts remain available in `tools/`.

No game files are included. Use legally obtained original game data and keep backups before rebuilding archives or executables.

## Directory layout

```text
tools/      Original working scripts, one file per specialized task
impl/       Grouped wrapper scripts and wrapper helper
docs/       CLI reference, workflow notes, format notes, and safety checklist
examples/   Copyable command examples in Markdown
```

## Requirements

```bash
python --version
python -m pip install pillow openpyxl
```

Most archive and text operations use only the Python standard library. TIM/PNG workflows require Pillow. Workbook-based text workflows require openpyxl.

## Recommended setup

```bash
mkdir work
cp /path/to/original/files/* work/
cp -r tools impl docs examples work/
cd work
```

Keep an untouched copy of every original archive and executable. Rebuilds should be tested in an emulator before release.

## Main documentation

- `docs/CLI_REFERENCE.md` lists scripts, detected options, subcommands, and concrete examples.
- `docs/DISC1_WORKFLOW.md` gives practical Disc 1 workflows.
- `docs/FORMAT_NOTES.md` summarizes important format rules and safety constraints.
- `docs/SAFETY_CHECKLIST.md` lists checks to run before publishing a patch.
- `examples/COMMAND_EXAMPLES.md` contains copyable command examples.

## Quick start examples

Extract a CDF archive:

```bash
python tools/pixygarden_CDF_Extractor.py PLANET.CDF out/PLANET
```

Extract a FAT archive:

```bash
python tools/pixygarden_FAT_Tool.py extract STAGE1.FAT out/STAGE1
```

Patch `MAIN.EXE`:

```bash
python tools/pixygarden_MAIN.EXE_Patcher.py MAIN.EXE MAIN_patched.EXE
```

Use grouped wrappers:

```bash
python impl/pixygarden_archive_tool.py --help
python impl/pixygarden_text_tool.py --help
python impl/pixygarden_graphics_tool.py --help
```
