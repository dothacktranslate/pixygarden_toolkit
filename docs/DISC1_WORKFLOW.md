# Disc 1 Workflow Guide

This guide gives concrete command patterns for common PixyGarden Disc 1 translation tasks.

## 1. Work from clean inputs

```bash
mkdir -p work/original work/build work/extract
cp MAIN.EXE work/original/
cp PLANET.CDF work/original/
cp TREE.CDF work/original/
cp EVENT.CDF work/original/
cp HELP.FAT work/original/
cp STAGE*.FAT work/original/
```

Keep `work/original/` untouched. Write rebuilt files to `work/build/`.

## 2. Extract CDF archives

```bash
python tools/pixygarden_CDF_Extractor.py work/original/PLANET.CDF work/extract/PLANET
python tools/pixygarden_CDF_Extractor.py work/original/TREE.CDF work/extract/TREE
```

Use the CDF repacker only after confirming the extracted layout and the intended replacement files.

```bash
python tools/pixygarden_CDF_Repacker.py --help
```

## 3. Extract FAT archives

```bash
python tools/pixygarden_FAT_Tool.py --help
python tools/pixygarden_FAT_Tool.py extract work/original/HELP.FAT work/extract/HELP
python tools/pixygarden_FAT_Tool.py extract work/original/STAGE1.FAT work/extract/STAGE1
```

Preserve slot order and padding unless the specific tool documents a complete archive relocation.

## 4. Edit TIM and indexed PNG graphics

```bash
python tools/pixygarden_TIM_Repacker.py --help
python tools/pixygarden_LZ_TIM_Tool.py --help
```

Indexed-image safety rules:

```text
4bpp TIM images may share one pixel-index plane across multiple CLUT pages.
A PNG can look visually correct while raw palette indexes are wrong.
Palette order must be preserved for raw index reinsertion.
Image editors must not optimize, deduplicate, or reorder indexed palettes.
```

## 5. Rebuild TREE and HELP text

TREE and HELP displayed text slots normally use:

```text
text bytes
00 terminator
FF padding to the end of the slot
```

Useful scripts:

```bash
python tools/pixygarden_TREE_TXT_Builder.py --help
python tools/pixygarden_TREE_TXT_CDF_Builder.py --help
python tools/pixygarden_TREE_CDF_Batch_Rebuilder.py --help
python tools/pixygarden_TXT_Builder.py --help
```

## 6. Rebuild EVENT or SCR-like scripts

EVENT-style text is structurally sensitive. Text is mixed with script bytes and control flow.

```bash
python tools/pixygarden_SCR_Repacker.py --help
```

Recommended rule:

```text
Use the original Japanese archive as the structural template.
Replace only translated text payloads.
Preserve control bytes, branch targets, opcode endings, and required terminators.
```

## 7. Patch MAIN.EXE

Patch from a clean base executable.

```bash
python tools/pixygarden_MAIN.EXE_Patcher.py work/original/MAIN.EXE work/build/MAIN.EXE
python tools/pixygarden_MAIN.EXE_Patcher.py --help
```

Default options should be used unless a screen-specific tuning option is required.

## 8. Test rebuilt files

Recommended checks:

```text
Boot to title screen.
Open main menu and confirm no corruption.
Start a new game with an empty save card.
Open Planet, Modus, Point, Object Overlay, and Report screens.
Check Save Clear Data and Proceed without saving? prompts.
Check TREE/HELP text pages.
Trigger several EVENT scenes.
Test DuckStation and at least one additional emulator when possible.
```
