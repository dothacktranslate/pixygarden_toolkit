# Command Examples

This file provides copyable command examples for common PixyGarden Disc 1 translation tasks.

The commands are examples, not a script. Copy only the command that matches the current task and edit paths before running it.

## Folder setup used by the examples

```text
work/
  original/   clean original game files
  extract/    extracted working files
  build/      rebuilt output files
```

Example setup commands:

```bash
mkdir -p work/original work/extract work/build
cp MAIN.EXE work/original/
cp PLANET.CDF work/original/
cp TREE.CDF work/original/
cp EVENT.CDF work/original/
cp HELP.FAT work/original/
cp STAGE*.FAT work/original/
```

## View help for the main tools

```bash
python tools/pixygarden_CDF_Extractor.py --help
python tools/pixygarden_CDF_Repacker.py --help
python tools/pixygarden_FAT_Tool.py --help
python tools/pixygarden_FAT_Repacker.py --help
python tools/pixygarden_TIM_Repacker.py --help
python tools/pixygarden_LZ_TIM_Tool.py --help
python tools/pixygarden_SCR_Repacker.py --help
python tools/pixygarden_MAIN.EXE_Patcher.py --help
```

## Extract CDF archives

Extract `PLANET.CDF`:

```bash
python tools/pixygarden_CDF_Extractor.py work/original/PLANET.CDF work/extract/PLANET
```

Extract `TREE.CDF`:

```bash
python tools/pixygarden_CDF_Extractor.py work/original/TREE.CDF work/extract/TREE
```

Extract `EVENT.CDF`:

```bash
python tools/pixygarden_CDF_Extractor.py work/original/EVENT.CDF work/extract/EVENT
```

## Repack CDF archives

Check the repacker help before rebuilding because template/reference options matter:

```bash
python tools/pixygarden_CDF_Repacker.py --help
```

Typical pattern:

```bash
python tools/pixygarden_CDF_Repacker.py work/extract/PLANET work/build/PLANET.CDF
```

If the script supports an original/template archive option, use the clean original file as the layout reference.

## Extract FAT archives

Extract `HELP.FAT`:

```bash
python tools/pixygarden_FAT_Tool.py extract work/original/HELP.FAT work/extract/HELP
```

Extract stage archives:

```bash
python tools/pixygarden_FAT_Tool.py extract work/original/STAGE1.FAT work/extract/STAGE1
python tools/pixygarden_FAT_Tool.py extract work/original/STAGE2.FAT work/extract/STAGE2
python tools/pixygarden_FAT_Tool.py extract work/original/STAGE3.FAT work/extract/STAGE3
python tools/pixygarden_FAT_Tool.py extract work/original/STAGE4.FAT work/extract/STAGE4
python tools/pixygarden_FAT_Tool.py extract work/original/STAGE5.FAT work/extract/STAGE5
```

## Repack FAT archives

Check the exact command syntax:

```bash
python tools/pixygarden_FAT_Repacker.py --help
```

Typical pattern:

```bash
python tools/pixygarden_FAT_Repacker.py work/original/HELP.FAT work/extract/HELP work/build/HELP.FAT
```

## Extract and rebuild TIM graphics

View TIM tool commands:

```bash
python tools/pixygarden_TIM_Repacker.py --help
python tools/pixygarden_LZ_TIM_Tool.py --help
```

Common workflow:

```bash
python tools/pixygarden_TIM_Repacker.py extract input.TIM output_png_folder
```

After editing PNG files, rebuild using the command format documented by the script:

```bash
python tools/pixygarden_TIM_Repacker.py --help
```

Important rule for indexed graphics:

```text
Preserve the PNG palette order. Do not use image-editor export settings that reorder, optimize, or deduplicate indexed palettes.
```

## Decode and rebuild LZ-compressed graphics

Check supported LZ/TIM commands:

```bash
python tools/pixygarden_LZ_TIM_Tool.py --help
```

Recommended workflow:

```text
1. Decode compressed data.
2. Edit or replace the decoded TIM data.
3. Re-encode the data.
4. Decode the rebuilt data again to verify that it round-trips.
```

## Build TREE and HELP text

View available text-builder options:

```bash
python tools/pixygarden_TREE_TXT_Builder.py --help
python tools/pixygarden_TREE_TXT_CDF_Builder.py --help
python tools/pixygarden_TREE_CDF_Batch_Rebuilder.py --help
python tools/pixygarden_TXT_Builder.py --help
```

Recommended text-slot rule:

```text
Displayed translated TREE/HELP strings should end with 00 followed by FF padding.
```

## Rebuild EVENT or SCR-like scripts

View SCR repacker options:

```bash
python tools/pixygarden_SCR_Repacker.py --help
```

Recommended EVENT-style workflow:

```text
1. Keep a clean original EVENT archive.
2. Use the original archive as the structural/control-byte template.
3. Insert translated text into the translated/base archive.
4. Preserve control bytes, branch targets, opcode endings, double-null endings, and color/control behavior.
5. Test rebuilt EVENT scenes in emulator.
```

## Patch MAIN.EXE

Patch from a clean base executable:

```bash
python tools/pixygarden_MAIN.EXE_Patcher.py work/original/MAIN.EXE work/build/MAIN.EXE
```

View all patcher options:

```bash
python tools/pixygarden_MAIN.EXE_Patcher.py --help
```

Use default patcher options unless a specific screen needs retuning.

## Use grouped wrapper tools

The grouped wrappers in `impl/` provide alternate entry points organized by task category.

Archive wrapper:

```bash
python impl/pixygarden_archive_tool.py --help
```

Text wrapper:

```bash
python impl/pixygarden_text_tool.py --help
```

Graphics wrapper:

```bash
python impl/pixygarden_graphics_tool.py --help
```

MAIN.EXE wrapper:

```bash
python impl/pixygarden_MAIN.EXE_Patcher.py --help
```

## Suggested final test pass

```text
Boot to title screen.
Open main menu.
Start a new game with an empty memory card.
Open Planet, Modus, Point, Object Overlay, and Report screens.
Check Save Clear Data and Proceed without saving? prompts.
Check TREE and HELP text pages.
Trigger several EVENT scenes.
Test DuckStation and at least one additional emulator.
```
