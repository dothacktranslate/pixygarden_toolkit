# CLI Reference
This reference lists the original scripts in `tools/` and the grouped wrapper scripts in `impl/`.

The detected option lists are generated from static `argparse.add_argument(...)` calls. A script may also support positional arguments or custom usage text that is documented in the script header.

Run any script with `--help` when available:

```bash
python tools/script_name.py --help
```

## Original scripts in `tools/`
### `tools/pixygarden_CDF_Extractor.py`
**Purpose:** Extracts CDF archives into ordinary files and folders.

**Detected options:**

- `--cdf`
- `--extract-dir`
- `--extract-nested`
- `--manifest`
- `--max-depth`

**Examples:**

```bash
python tools/pixygarden_CDF_Extractor.py PLANET.CDF out/PLANET
python tools/pixygarden_CDF_Extractor.py TREE.CDF out/TREE
```

**Notes:**

- CDF entries are sector-based. Preserve entry sizes and allocations when rebuilding related archives.

**Line count:** 239

### `tools/pixygarden_CDF_Repacker.py`
**Purpose:** Rebuilds CDF archives from extracted or edited files.

**Detected options:**

- `--allow-cdf-shrink`
- `--compact-fat-table`
- `--compact-smaller-replacements`
- `--dry-run`
- `--fat-align`
- `--no-normalize-txt-tail`
- `--out-cdf`
- `--pad-byte`
- `--replacement-dir`
- `--report`
- `--source-cdf`
- `--top-align`
- `--txt-terminator`

**Examples:**

```bash
python tools/pixygarden_CDF_Repacker.py --help
python tools/pixygarden_CDF_Repacker.py extracted_PLANET PLANET_rebuilt.CDF
```

**Notes:**

- Use a clean original archive as the layout reference when the script supports template/reference input.

**Line count:** 505

### `tools/pixygarden_DAT_Extractor.py`
**Purpose:** Extracts DAT-style payloads used by PixyGarden assets.

**Script docstring:**

```text
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
```

**Detected options:**

- `--all-in-folder`
- `--continue-on-error`
- `--dry-run`
- `--min-gap`
- `--no-recursive`
- `--save-gaps`
- `--types`
- `-o, --out`

**Examples:**

```bash
python tools/pixygarden_DAT_Extractor.py --help
```

**Notes:**

- DAT output should be treated as binary unless the extractor documents a text/image subtype.

**Line count:** 821

### `tools/pixygarden_FAT_Repacker.py`
**Purpose:** Rebuilds FAT archives from edited folders or file lists.

**Detected options:**

- `--allow-shrink`
- `--compact-fat-table`
- `--compact-smaller-replacements`
- `--dry-run`
- `--fat-align`
- `--no-normalize-txt-tail`
- `--out-fat`
- `--pad-byte`
- `--preserve-source-size`
- `--replacement-dir`
- `--report`
- `--source-fat`
- `--txt-terminator`

**Examples:**

```bash
python tools/pixygarden_FAT_Repacker.py --help
```

**Notes:**

- Prefer same-slot rebuilds for translated Disc 1 data.

**Line count:** 349

### `tools/pixygarden_FAT_Tool.py`
**Purpose:** Lists, extracts, and repacks FAT archives.

**Script docstring:**

```text
pixygarden_FAT_tool.py

Two-way PixyGarden standalone .FAT utility.

Features:
  extract  - recursively extract a .FAT archive into a folder
  list     - print/list .FAT contents without extracting
  repack   - rebuild a .FAT from an extracted/replacement folder

The repack path is based on the same 0x14-byte entry structure used by the
existing pixygarden_FAT_Repacker_v2 script:
  entry = 0x10-byte ASCII name + uint32 little-endian relative data offset

Default behavior is preservation-first:
  - extraction writes exact inferred file slots unless --trim-txt-tail is used
  - repack pads smaller replacements back to their original slot size by default
  - output is padded back to source size if it would otherwise shrink
```

**Detected subcommands:** `extract`, `extract-folder`, `list`, `repack`

**Detected options:**

- `--all-in-folder`
- `--allow-shrink`
- `--compact-fat-table`
- `--compact-smaller-replacements`
- `--continue-on-error`
- `--dry-run`
- `--fat-align`
- `--manifest`
- `--no-normalize-txt-tail`
- `--overwrite`
- `--pad-byte`
- `--preserve-source-size`
- `--recursive`
- `--report`
- `--save-raw-fat`
- `--summary`
- `--trim-txt-tail`
- `--txt-terminator`

**Examples:**

```bash
python tools/pixygarden_FAT_Tool.py --help
python tools/pixygarden_FAT_Tool.py extract HELP.FAT out/HELP
python tools/pixygarden_FAT_Tool.py extract STAGE1.FAT out/STAGE1
```

**Notes:**

- Slot order and padding are important. Repack only after keeping a backup of the original FAT.

**Line count:** 794

### `tools/pixygarden_LZ_TIM_Tool.py`
**Purpose:** Decodes and encodes PixyGarden LZ-compressed TIM data and helps convert TIM assets.

**Script docstring:**

```text
pixygarden_lz_tim_tool.py

General PixyGarden LZ compressor/decompressor for compressed TIM/BIN payloads.

Deep-search v3: level 3 uses 2048 candidates instead of 1024, which matches/fits the original NAME/TREE-style streams more reliably.

Originally discovered in PARM.CDF, but the compression format is not PARM-specific.
MAIN.EXE contains the runtime decompressor at file offset 0x508B8 / RAM 0x800700B8.

Commands:
  decode       Decompress one compressed .TIM/.BIN payload directly.
  encode       Compress any decoded file/TIM/BIN directly.
  test         Test whether a file is a valid PixyGarden LZ stream.
  extract-cdf  Parse a simple PixyGarden CDF table and decode members that use this LZ format.
  reinsert-cdf Compress edited decoded files and reinsert them into a simple PixyGarden CDF.

Simple CDF layout supported:
  0x00 uint32 first_data_offset
  0x04 uint32 entry_count
  0x10 repeated 0x20-byte entries:
       0x00..0x0F ASCII name
       0x10 uint32 unknown
       0x14 uint32 start sector
       0x18 uint32 sector count
       0x1C uint32 real member size

If a CDF uses another layout, use direct decode/encode on extracted members.
```

**Detected subcommands:** `decode`, `encode`, `extract-cdf`, `reinsert-cdf`, `test`

**Detected options:**

- `--auto-png`
- `--clut-row`
- `--dry-run`
- `--entry`
- `--extract-raw-on-fail`
- `--level`
- `--manifest`
- `--max-out`
- `--no-png`
- `--pad-byte`
- `--png`
- `--replacement-dir`
- `--report`
- `--save-compressed`
- `--sheet-cols`
- `-o, --out`

**Examples:**

```bash
python tools/pixygarden_LZ_TIM_Tool.py --help
```

**Notes:**

- After recompression, decode the result again to verify round-trip safety.

**Line count:** 720

### `tools/pixygarden_MAIN.EXE_Patcher.py`
**Purpose:** Applies the Disc 1 executable patches for translated text, UI layout, graphics behavior, and compatibility.

**Detected options:**

- `--advance10-chars`
- `--advance2-chars`
- `--advance3-chars`
- `--advance4-chars`
- `--advance5-chars`
- `--advance6-chars`
- `--advance7-chars`
- `--advance8-chars`
- `--advance9-chars`
- `--align-strings`
- `--ascii-advance`
- `--ascii-map-json`
- `--capital-r-advance-delta`
- `--clear-data-selector-y`
- `--clear-data-selector-y-force`
- `--direct-mips-code-end`
- `--direct-mips-code-start`
- `--direct-mips-confidence`
- `--direct-mips-exclude-rows`
- `--direct-mips-max-gap`
- `--direct-mips-no-lifetime-aware`
- `--direct-mips-rows`
- `--disable-clear-data-selector-y-fix`
- `--disable-font-tim-metrics`
- `--disable-memory-card-centering`
- `--disable-modus-crest-template-x-fix`
- `--disable-modus-crest-text-x-patch`
- `--disable-modus-exact-ft4-edge-read-hooks`
- `--disable-modus-local-record-early-fix`
- `--disable-modus-local-record-fix`
- `--disable-modus-stage-template-element-fix`
- `--disable-name-screen-fix`
- `--disable-pixy-name-suffix-created-modus`
- `--disable-pixy-name-suffix-spacing`
- `--disable-planet-copy-slot-split-hook`
- `--disable-planet-info-element-draw-patch`
- `--disable-planet-info-planet-clut-patch`
- `--disable-planet-info-planet-icon-patch`
- `--disable-planet-stage-terminator-patch`
- `--disable-planet-title-bitmap-metrics`
- `--disable-planet-title-ram-hook`
- `--disable-plsel-following-dynamic-patch`
- `--disable-plsel-following-graphic-patch`
- `--disable-plsel-graphic-draw-patch`
- `--disable-report-text-fix`
- `--disable-selector-gsbox-hook`
- `--disable-stage-tim03-moved-uv-patch`
- `--disable-stage-tim04-na-ft4-edge-filter-hook`
- `--disable-stage-tim04-na-ft4-source-filter-hook`
- `--disable-stage-tim04-na-packet-filter-hook`
- `--disable-stage-tim04-na-static-seed`
- `--disable-stage-tim04-na-template-u-hook`
- `--disable-text-primitive-capacity-patch`
- `--disable-tree-text-buffer-patch`
- `--disable-tuto-21-step-flow`
- `--disable-v74-final-spacing-lock`
- `--disable-v75-final-spacing-lock`
- `--disable-v75-final-xoff-lock`
- `--disable-v75-pair-kern`
- `--draw-shift`
- `--dry-run`
- `--enable-shared-planet-icon-patch`
- `--enable-stage-tim04-na-packet-filter-hook`
- `--exe`
- `--font-ascii-base-abs`
- `--font-cell-height`
- `--font-cell-step-x`
- `--font-cell-step-y`
- `--font-cell-width`
- `--font-map-json`
- `--font-tim`
- `--font-tracking`
- `--font-zero-is-occupied`
- `--hyphen-advance-delta`
- `--memory-card-centering-force`
- `--memory-card-centering-no-dynamic-scan`
- `--memory-card-centering-no-known-sites`
- `--memory-card-centering-x-bias`
- `--modus-crest-template-color`
- `--modus-crest-template-h`
- `--modus-crest-template-new-x`
- `--modus-crest-template-old-x`
- `--modus-crest-template-u`
- `--modus-crest-template-v`
- `--modus-crest-template-w`
- `--modus-crest-template-y`
- `--modus-crest-text-site`
- `--modus-crest-text-x`
- `--modus-crest-text-x-force`
- `--modus-exact-ft4-edge-read-force`
- `--modus-exact-ft4-edge-record-ram`
- `--modus-local-record-early-force`
- `--modus-local-record-fix-force`
- `--modus-stage-earth-u`
- `--modus-stage-earth-w`
- `--modus-stage-element-bad-w`
- `--modus-stage-element-bad-x`
- `--modus-stage-element-h`
- `--modus-stage-element-marker`
- `--modus-stage-element-old-w`
- `--modus-stage-element-w`
- `--modus-stage-element-x`
- `--modus-stage-element-y`
- `--modus-stage-fire-u`
- `--modus-stage-fire-w`
- `--modus-stage-ft4-earth-u`
- `--modus-stage-ft4-earth-w`
- `--modus-stage-ft4-fire-u`
- `--modus-stage-ft4-fire-w`
- `--modus-stage-ft4-water-u`
- `--modus-stage-ft4-water-w`
- `--modus-stage-ft4-wind-u`
- `--modus-stage-ft4-wind-w`
- `--modus-stage-ft4-x-adjust`
- `--modus-stage-ft4-x-tolerance`
- `--modus-stage-water-u`
- `--modus-stage-water-w`
- `--modus-stage-wind-u`
- `--modus-stage-wind-w`
- `--name-screen-exclude-offsets`
- `--name-screen-inplace-body-encoding`
- `--name-screen-inplace-fill`
- `--name-screen-inplace-rows`
- `--narrow-advance`
- `--narrow-chars`
- `--no-lui`
- `--no-preserve-legacy-controls`
- `--no-strip-controls`
- `--non-strict-direct-mips`
- `--original-bytes-column`
- `--out`
- `--pair-kern`
- `--paren-advance`
- `--parser-force`
- `--pixy-name-suffix-force`
- `--pixy-name-suffix-include-created-modus`
- `--planet-info-dash-u`
- `--planet-info-dash-w`
- `--planet-info-earth-u`
- `--planet-info-earth-w`
- `--planet-info-earth-x-shift`
- `--planet-info-element-x-shift`
- `--planet-info-fire-u`
- `--planet-info-fire-w`
- `--planet-info-fire-x-shift`
- `--planet-info-force`
- `--planet-info-planet-clut`
- `--planet-info-planet-u`
- `--planet-info-planet-v`
- `--planet-info-water-u`
- `--planet-info-water-w`
- `--planet-info-water-x-shift`
- `--planet-info-wind-u`
- `--planet-info-wind-w`
- `--planet-info-wind-x-shift`
- `--planet-stage-terminator`
- `--planet-stage-terminator-force`
- `--planet-title-advance-delta`
- `--planet-title-advance-override`
- `--planet-title-allow-broad`
- `--planet-title-bitmap-guard`
- `--planet-title-bitmap-tracking`
- `--planet-title-clear-index`
- `--planet-title-force`
- `--planet-title-min-payload-addr`
- `--planet-title-no-bitmap-guard`
- `--planet-title-no-pattern-guard`
- `--planet-title-pattern-min-per-slot`
- `--planet-title-pattern-x`
- `--planet-title-pattern-y`
- `--planet-title-payload-addr`
- `--planet-title-payload-addrs`
- `--planet-title-shadow-index`
- `--planet-title-slot-h`
- `--planet-title-slot-pitch`
- `--planet-title-slot-y-offsets`
- `--planet-title-space-advance`
- `--planet-title-text-index`
- `--planet-title-text-w`
- `--planet-title-text-x`
- `--planet-title-titles`
- `--planet-title-tracking`
- `--planet-title-y`
- `--plsel-bottom-v`
- `--plsel-following-a-left-u`
- `--plsel-following-a-right-u`
- `--plsel-following-b-left-u`
- `--plsel-following-b-right-u`
- `--plsel-following-dynamic-a-right-u`
- `--plsel-following-top-v`
- `--plsel-following-u-shift`
- `--plsel-following-uv-only`
- `--plsel-following-x-shift`
- `--plsel-force`
- `--plsel-left-u`
- `--plsel-left-x`
- `--plsel-main-right-u`
- `--plsel-main-right-x`
- `--plsel-main-uv-only`
- `--plsel-patch-json`
- `--plsel-top-v`
- `--pointer-column`
- `--preserve-u-controls`
- `--ptr32-cluster-gap`
- `--ptr32-cluster-min`
- `--ptr32-cluster-unique-min`
- `--ptr32-exclude-offsets`
- `--ptr32-include-offsets`
- `--ptr32-policy`
- `--ptr32-ranges-json`
- `--ptr32-sections`
- `--ptr32-slice-count`
- `--ptr32-slice-index`
- `--punct-advance`
- `--punct-chars`
- `--report-text-force`
- `--reports-dir`
- `--selector-gsbox-force`
- `--selector-gsbox-height`
- `--selector-gsbox-left-width`
- `--selector-gsbox-no-width`
- `--selector-gsbox-no-x-shift`
- `--selector-gsbox-off-width`
- `--selector-gsbox-off-x-shift`
- `--selector-gsbox-on-width`
- `--selector-gsbox-on-x-shift`
- `--selector-gsbox-onoff-height`
- `--selector-gsbox-onoff-y-shift`
- `--selector-gsbox-proceed-extra-y-shift`
- `--selector-gsbox-proceed-y`
- `--selector-gsbox-right-width`
- `--selector-gsbox-vibration-end`
- `--selector-gsbox-vibration-start`
- `--selector-gsbox-y-shift`
- `--selector-gsbox-yes-width`
- `--selector-gsbox-yes-x-shift`
- `--selector-gsbox-yesno-height`
- `--selector-gsbox-yesno-y-shift`
- `--sheet`
- `--skip-direct-mips`
- `--slash-advance-delta`
- `--slash-fixed-advance`
- `--space-advance`
- `--stage-tim03-na-line-u, --stage-tim04-na-line-slot-u`
- `--stage-tim03-planet-u`
- `--stage-tim03-planet-v`
- `--stage-tim03-uv-force`
- `--stage-tim04-dash-w`
- `--stage-tim04-earth-u`
- `--stage-tim04-earth-w`
- `--stage-tim04-earth-x-shift`
- `--stage-tim04-element-x-shift`
- `--stage-tim04-fire-u`
- `--stage-tim04-fire-w`
- `--stage-tim04-fire-x-shift`
- `--stage-tim04-na-bad-word`
- `--stage-tim04-na-ft4-edge-filter-force`
- `--stage-tim04-na-ft4-packet-cmd`
- `--stage-tim04-na-ft4-source-filter-force`
- `--stage-tim04-na-good-word`
- `--stage-tim04-na-packet-base`
- `--stage-tim04-na-packet-filter-force`
- `--stage-tim04-na-template-color`
- `--stage-tim04-na-template-old-u`
- `--stage-tim04-na-template-size`
- `--stage-tim04-na-template-u-force`
- `--stage-tim04-na-template-v`
- `--stage-tim04-object-element-packet-prefix`
- `--stage-tim04-object-overlay-base-x`
- `--stage-tim04-object-overlay-marker`
- `--stage-tim04-object-overlay-na-slot-ram`
- `--stage-tim04-object-overlay-na-x`
- `--stage-tim04-object-overlay-object-guard-halfword`
- `--stage-tim04-water-u`
- `--stage-tim04-water-w`
- `--stage-tim04-water-x-shift`
- `--stage-tim04-wind-u`
- `--stage-tim04-wind-w`
- `--stage-tim04-wind-x-shift`
- `--text-column`
- `--text-primitive-capacity-glyphs, --text-primitive-capacity`
- `--text-primitive-force`
- `--tree-buffer-force`
- `--tree-copy-limit`
- `--tree-terminator`
- `--tree-terminator-force`
- `--tuto-close-wait-frames`
- `--tuto21-force`
- `--two-byte-advance`
- `--uppercase-advance-delta`
- `--xa-clip-id-column`
- `--xa-text-column`
- `--xa-text-sheet`
- `--xa-text-xlsx`
- `--xlsx`
- `--xoff-left1-chars`
- `--xoff-left2-chars`
- `--xoff-right1-chars`
- `--xoff-right2-chars`
- `--xoff-right3-chars`

**Examples:**

```bash
python tools/pixygarden_MAIN.EXE_Patcher.py MAIN.EXE MAIN_patched.EXE
python tools/pixygarden_MAIN.EXE_Patcher.py --help
```

**Notes:**

- Patch from a clean base executable. Avoid stacking experimental patchers onto an already-patched file.

**Line count:** 7296

### `tools/pixygarden_PLANET_HELP_CDF_Builder.py`
**Purpose:** Builds PLANET/HELP-related CDF changes.

**Detected options:**

- `--align`
- `--allow-empty`
- `--dry-run`
- `--encoding`
- `--encoding-errors`
- `--exclude-regex`
- `--fallback-translation-column`
- `--include-regex`
- `--linebreak-mode`
- `--no-fail-on-warnings`
- `--only-path-contains`
- `--out-cdf`
- `--out-help-fat`
- `--pad-byte`
- `--path-column`
- `--report`
- `--row-column`
- `--rows`
- `--sheet`
- `--source-cdf`
- `--target-container`
- `--terminator`
- `--text-mode`
- `--translation-column`
- `--write-manifest`
- `--xlsx`

**Examples:**

```bash
python tools/pixygarden_PLANET_HELP_CDF_Builder.py --help
```

**Notes:**

- Preserve source archive layout unless the script documents a full rebuild.

**Line count:** 710

### `tools/pixygarden_SCR_Repacker.py`
**Purpose:** Rebuilds script/SCR-style files, including template-safe workflows for EVENT-like data.

**Detected options:**

- `--blank-means-empty`
- `--cdf-pad-byte`
- `--conditional-jump-opcodes`
- `--dry-run`
- `--encoding`
- `--encoding-errors`
- `--event-cdf`
- `--event-cdf-report`
- `--extract-filter`
- `--extract-text-csv`
- `--fullwidth-digits`
- `--jump-opcodes`
- `--keep-original-tail`
- `--linebreak-mode`
- `--near-target-delta`
- `--no-auto-c7`
- `--no-auto-fix-near-targets`
- `--out`
- `--out-dir`
- `--out-event-cdf`
- `--preserve-record-lengths`
- `--recursive`
- `--repack-cdf`
- `--replacements-csv`
- `--report`
- `--report-dir`
- `--scr`
- `--scr-dir`
- `--scr-glob`
- `--scr-pattern`
- `--strict-final-targets`
- `--strict-targets`
- `--template-event-cdf`
- `--terminator`
- `--text-mode`
- `--text-opcode`

**Examples:**

```bash
python tools/pixygarden_SCR_Repacker.py --help
```

**Notes:**

- EVENT-style files are structurally sensitive. Preserve control bytes, branch targets, opcode endings, and required terminators.

**Line count:** 1440

### `tools/pixygarden_STAGE_HELP_FAT_Builder.py`
**Purpose:** Builds STAGE/HELP FAT changes.

**Detected options:**

- `--align`
- `--allow-empty`
- `--dry-run`
- `--encoding`
- `--encoding-errors`
- `--exclude-regex`
- `--fallback-translation-column`
- `--include-regex`
- `--linebreak-mode`
- `--must-fit-original`
- `--no-fail-on-warnings`
- `--only-path-contains`
- `--out-help-fat`
- `--pad-byte`
- `--pad-to-original`
- `--path-column`
- `--report`
- `--root-path`
- `--row-column`
- `--rows`
- `--sheet`
- `--source-help-fat`
- `--terminator`
- `--text-mode`
- `--translation-column`
- `--write-manifest`
- `--xlsx`

**Examples:**

```bash
python tools/pixygarden_STAGE_HELP_FAT_Builder.py --help
```

**Notes:**

- Check all rebuilt STAGE FAT files when a shared asset is updated.

**Line count:** 692

### `tools/pixygarden_String_Extractor.py`
**Purpose:** Extracts strings from binary files for review or translation planning.

**Script docstring:**

```text
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
```

**Detected options:**

- `-o, --output-dir`

**Examples:**

```bash
python tools/pixygarden_String_Extractor.py --help
```

**Notes:**

- String extraction does not prove text is safe to rebuild as plain text. Script-like files still require structural handling.

**Line count:** 489

### `tools/pixygarden_TIM_Repacker.py`
**Purpose:** Extracts TIM images to PNG and reinserts edited PNGs into TIM files.

**Script docstring:**

```text
pixygarden_tim_roundtrip_v7_baseline_delta.py

A self-contained PlayStation TIM <-> PNG round-trip tool with palette-safe indexed PNG injection, CLUT-page layer merging, TIM-index and baseline delta merging, and forced transparency handling.

Main goals
----------
1. Extract every visible image page from a TIM as PNG.
   - For 4bpp/8bpp indexed TIMs, each CLUT page is extracted as its own PNG.
   - For direct-color TIMs, one PNG is extracted.

2. Inject edited PNGs back into the original TIM in-place.
   - The original TIM structure, headers, offsets, and block sizes are preserved.
   - For indexed TIMs, pixel indices are repacked into the original PXL block.
   - Optionally, indexed PNG palettes can be written back into the TIM CLUT.

Why indexed PNGs matter
-----------------------
A 4bpp TIM has one shared pixel-index plane. If it has 3 CLUT pages, those
are three palettes applied to the same pixel indices. Editing one page can
therefore affect the appearance of other pages. This is normal for PS1 TIMs.

For safest round-tripping:
    Extract as indexed PNG.
    Edit while preserving indexed/palette mode.
    Inject the indexed PNG back.

RGBA PNG injection is also supported by mapping colors back to the selected
TIM CLUT page. Use --nearest if your editor introduced slightly altered colors.

Basic examples
--------------

For font TIMs where CLUT pages reveal different visible layers/images, use:
    python pixygarden_tim_roundtrip_v7_baseline_delta.py inject --tim FONT11Z0.TIM --png-dir edited_FONT11Z0 --out FONT11Z0_patched.TIM --multi-page-mode merge-visible

Basic examples
--------------

Show TIM info:
    python pixygarden_tim_roundtrip_v7_baseline_delta.py info --tim 48.tim

Extract all pages as indexed PNGs:
    python pixygarden_tim_roundtrip_v7_baseline_delta.py extract --tim 48.tim --out-dir extracted_48

Inject edited PNGs back, preserving original TIM CLUTs and remapping edited PNG palette colors back to TIM indices:
    python pixygarden_tim_roundtrip_v7_baseline_delta.py inject --tim 48.tim --png-dir extracted_48 --out 48_patched.tim

Inject one edited page:
    python pixygarden_tim_roundtrip_v7_baseline_delta.py inject --tim 48.tim --png extracted_48/48_page02.png --out 48_patched.tim

Inject an RGBA PNG by nearest-color palette mapping:
    python pixygarden_tim_roundtrip_v7_baseline_delta.py inject --tim 48.tim --png edited_page02_rgba.png --page 2 --out 48_patched.tim --nearest

Embedded TIM at offset:
    python pixygarden_tim_roundtrip_v7_baseline_delta.py extract --tim archive.bin --tim-offset 0x1234 --out-dir extracted_embedded

Override interpretation values for odd files:
    python pixygarden_tim_roundtrip_v7_baseline_delta.py extract --tim weird.tim --out-dir out --force-bpp 4 --force-width 256 --force-height 253

Notes
-----
- For standard TIMs, you should not need offsets or size flags.
- The tool writes helpful metadata into extracted PNGs, so reinjection usually
  does not need --page.
- For multi-page CLUT TIMs
```

**Detected subcommands:** `extract`, `info`, `inject`

**Detected options:**

- `--alpha-threshold`
- `--baseline-dir`
- `--delta-compare`
- `--direct-order`
- `--force-bpp`
- `--force-clut-data-offset`
- `--force-height`
- `--force-page-count`
- `--force-palette-size`
- `--force-pixel-offset`
- `--force-width`
- `--glob`
- `--indexed-index-mode`
- `--multi-page-mode`
- `--nearest`
- `--out`
- `--out-dir`
- `--overlap-policy`
- `--page`
- `--palette-csv`
- `--pixel-conflict`
- `--pixel-source-page`
- `--png`
- `--png-dir`
- `--png-mode`
- `--prefix`
- `--stp-policy`
- `--tim`
- `--tim-offset`
- `--transparent-index`
- `--transparent-png-index`
- `--transparent-rgb`
- `--update-clut-from-png-palette`

**Examples:**

```bash
python tools/pixygarden_TIM_Repacker.py --help
```

**Notes:**

- For indexed PNGs, preserve palette order. RGB-equivalent remapping is not safe when raw 4bpp indexes matter.

**Line count:** 1733

### `tools/pixygarden_TREE_CDF_Batch_Rebuilder.py`
**Purpose:** Batch-rebuilds TREE.CDF assets after text or related changes.

**Script docstring:**

```text
pixygarden_TREE_CDF_InPlace_NAME_JTEST_Rebuilder_v10.py

In-place TREE.CDF NAME/TIM + JTEST replacement workflow.

This fixes the previous TREE batch-rebuilder problem: the old script rebuilt
nested FAT/CDF containers and could accidentally rewrite unrelated assets such
as DETAILS.FAT/INFO.FAT text. v8/v9/v10 never rebuild archive tables. It starts from
--source-cdf bytes and overwrites only the exact mapped compressed DAT/BIN
payload slots, preserving every other byte of the source CDF.

Manual workflow mirrored per asset:

  python pixygarden_TIM_Tool_v2.py insert SOURCE.TIM EDITED.png -o PATCHED.TIM
  python pixygarden_LZ_TIM_Tool_v3_deep.py encode PATCHED.TIM -o PATCHED.BIN --level 3

Safety:
  - Patched TIM must stay exactly the same byte size as source TIM.
  - Encoded BIN/DAT stream must fit the original archive slot.
  - Smaller streams are padded back to the exact slot size.
  - Output CDF size is exactly the same as source CDF.
  - Only mapped entries are overwritten; no CDF/FAT repack happens.

Typical use:

  python pixygarden_TREE_CDF_InPlace_NAME_JTEST_Rebuilder_v10.py ^
    --source-cdf TREE_working.CDF ^
    --png-dir edited_tree_pngs ^
    --tim-dir decoded_tree_tim ^
    --out-cdf TREE_patched.CDF ^
    --clean-work

If automatic mapping is ambiguous, pass:

  --archive-map-csv tree_archive_map.csv

CSV format:
  stem,archive_path
  N01,DETAILS.FAT/NAME.FAT/N01.DAT
  N02,DETAILS.FAT/NAME.FAT/N02.DAT
  JTEST,DETAILS.FAT/TEST_G.FAT/JTEST.DAT

JTEST support:
  v10 can patch JTEST.DAT alongside N01-N29. Put JTEST_page00.png
  or JTEST.png in --png-dir / --extra-png-dir, and JTEST.TIM in --tim-dir
  / --extra-tim-dir. It maps to DETAILS.FAT/TEST_G.FAT/JTEST.DAT.
```

**Detected options:**

- `--archive-map-csv`
- `--bin-dir`
- `--clean-work`
- `--dry-run`
- `--extra-png-dir`
- `--extra-tim-dir`
- `--internal-lz-candidates`
- `--limit`
- `--lz-mode`
- `--lz-tool`
- `--out-cdf`
- `--patched-tim-dir`
- `--png-dir`
- `--prepared-png-dir`
- `--small-internal-lz-candidates`
- `--source-cdf`
- `--tim-dir`
- `--tim-mode`
- `--tim-tool`

**Examples:**

```bash
python tools/pixygarden_TREE_CDF_Batch_Rebuilder.py --help
```

**Notes:**

- Compare top-level CDF structure after rebuilding.

**Line count:** 944

### `tools/pixygarden_TREE_TXT_Builder.py`
**Purpose:** Builds translated TREE text files.

**Detected options:**

- `--align`
- `--allow-empty`
- `--default-terminator`
- `--encoding`
- `--encoding-errors`
- `--fallback-translation-column`
- `--force-terminator`
- `--manifest`
- `--original-size-column`
- `--out-dir`
- `--pad-byte`
- `--pad-to-original`
- `--path-column`
- `--paths`
- `--report`
- `--row-column`
- `--rows`
- `--sheet`
- `--terminator-column`
- `--text-byte-length-column`
- `--text-mode`
- `--translation-column`
- `--xlsx`

**Examples:**

```bash
python tools/pixygarden_TREE_TXT_Builder.py --help
```

**Notes:**

- Check terminators and padding after rebuilds.

**Line count:** 365

### `tools/pixygarden_TREE_TXT_CDF_Builder.py`
**Purpose:** Builds TREE text changes back into TREE.CDF workflows.

**Detected options:**

- `--align`
- `--allow-container-growth`
- `--allow-empty`
- `--allow-shrink`
- `--dry-run`
- `--encoding`
- `--encoding-errors`
- `--exclude-regex`
- `--fallback-translation-column`
- `--include-regex`
- `--linebreak-mode`
- `--no-fail-on-warnings`
- `--only-path-contains`
- `--out-cdf`
- `--out-info-fat`
- `--pad-byte`
- `--path-column`
- `--report`
- `--row-column`
- `--rows`
- `--sheet`
- `--source-cdf`
- `--target-container`
- `--terminator`
- `--text-mode`
- `--top-parent`
- `--translation-column`
- `--write-manifest`
- `--xlsx`

**Examples:**

```bash
python tools/pixygarden_TREE_TXT_CDF_Builder.py --help
```

**Notes:**

- Use the current executable terminator behavior consistently with the generated text files.

**Line count:** 842

### `tools/pixygarden_TXT_Builder.py`
**Purpose:** Builds fixed-size translated TXT payloads.

**Detected options:**

- `--align`
- `--allow-empty`
- `--default-terminator`
- `--encoding`
- `--encoding-errors`
- `--escape-terminator-byte`
- `--escape-terminator-strategy`
- `--exclude-regex`
- `--fallback-translation-column`
- `--force-terminator`
- `--include-ext`
- `--include-regex`
- `--linebreak-mode`
- `--manifest`
- `--original-size-column`
- `--out-dir`
- `--pad-byte`
- `--pad-to-original`
- `--path-column`
- `--paths`
- `--report`
- `--row-column`
- `--rows`
- `--sheet`
- `--terminator-column`
- `--text-byte-length-column`
- `--text-mode`
- `--translation-column`
- `--xlsx`

**Examples:**

```bash
python tools/pixygarden_TXT_Builder.py --help
```

**Notes:**

- Translated TREE/HELP text slots normally use 00 followed by FF padding.

**Line count:** 577

## Grouped wrapper scripts in `impl/`
### `impl/_launcher.py`
**Purpose:** Internal wrapper launcher helper used by grouped impl scripts.

**Script docstring:**

```text
Shared launcher helpers for the PixyGarden Disc 1 toolkit front-end scripts.

This file is used by the merged category tools. It runs one of the tested
implementation modules from the implementation/ directory while preserving the
same command-line behavior as the original script.
```

**Detected options:** none found by static scan.

**Line count:** 68

### `impl/pixygarden_MAIN.EXE_Patcher.py`
**Purpose:** Wrapper entry point for the MAIN.EXE patcher.

**Detected options:**

- `--advance10-chars`
- `--advance2-chars`
- `--advance3-chars`
- `--advance4-chars`
- `--advance5-chars`
- `--advance6-chars`
- `--advance7-chars`
- `--advance8-chars`
- `--advance9-chars`
- `--align-strings`
- `--ascii-advance`
- `--ascii-map-json`
- `--capital-r-advance-delta`
- `--clear-data-selector-y`
- `--clear-data-selector-y-force`
- `--direct-mips-code-end`
- `--direct-mips-code-start`
- `--direct-mips-confidence`
- `--direct-mips-exclude-rows`
- `--direct-mips-max-gap`
- `--direct-mips-no-lifetime-aware`
- `--direct-mips-rows`
- `--disable-clear-data-selector-y-fix`
- `--disable-font-tim-metrics`
- `--disable-memory-card-centering`
- `--disable-modus-crest-template-x-fix`
- `--disable-modus-crest-text-x-patch`
- `--disable-modus-exact-ft4-edge-read-hooks`
- `--disable-modus-local-record-early-fix`
- `--disable-modus-local-record-fix`
- `--disable-modus-stage-template-element-fix`
- `--disable-name-screen-fix`
- `--disable-pixy-name-suffix-created-modus`
- `--disable-pixy-name-suffix-spacing`
- `--disable-planet-copy-slot-split-hook`
- `--disable-planet-info-element-draw-patch`
- `--disable-planet-info-planet-clut-patch`
- `--disable-planet-info-planet-icon-patch`
- `--disable-planet-stage-terminator-patch`
- `--disable-planet-title-bitmap-metrics`
- `--disable-planet-title-ram-hook`
- `--disable-plsel-following-dynamic-patch`
- `--disable-plsel-following-graphic-patch`
- `--disable-plsel-graphic-draw-patch`
- `--disable-report-text-fix`
- `--disable-selector-gsbox-hook`
- `--disable-stage-tim03-moved-uv-patch`
- `--disable-stage-tim04-na-ft4-edge-filter-hook`
- `--disable-stage-tim04-na-ft4-source-filter-hook`
- `--disable-stage-tim04-na-packet-filter-hook`
- `--disable-stage-tim04-na-static-seed`
- `--disable-stage-tim04-na-template-u-hook`
- `--disable-text-primitive-capacity-patch`
- `--disable-tree-text-buffer-patch`
- `--disable-tuto-21-step-flow`
- `--disable-v74-final-spacing-lock`
- `--disable-v75-final-spacing-lock`
- `--disable-v75-final-xoff-lock`
- `--disable-v75-pair-kern`
- `--draw-shift`
- `--dry-run`
- `--enable-shared-planet-icon-patch`
- `--enable-stage-tim04-na-packet-filter-hook`
- `--exe`
- `--font-ascii-base-abs`
- `--font-cell-height`
- `--font-cell-step-x`
- `--font-cell-step-y`
- `--font-cell-width`
- `--font-map-json`
- `--font-tim`
- `--font-tracking`
- `--font-zero-is-occupied`
- `--hyphen-advance-delta`
- `--memory-card-centering-force`
- `--memory-card-centering-no-dynamic-scan`
- `--memory-card-centering-no-known-sites`
- `--memory-card-centering-x-bias`
- `--modus-crest-template-color`
- `--modus-crest-template-h`
- `--modus-crest-template-new-x`
- `--modus-crest-template-old-x`
- `--modus-crest-template-u`
- `--modus-crest-template-v`
- `--modus-crest-template-w`
- `--modus-crest-template-y`
- `--modus-crest-text-site`
- `--modus-crest-text-x`
- `--modus-crest-text-x-force`
- `--modus-exact-ft4-edge-read-force`
- `--modus-exact-ft4-edge-record-ram`
- `--modus-local-record-early-force`
- `--modus-local-record-fix-force`
- `--modus-stage-earth-u`
- `--modus-stage-earth-w`
- `--modus-stage-element-bad-w`
- `--modus-stage-element-bad-x`
- `--modus-stage-element-h`
- `--modus-stage-element-marker`
- `--modus-stage-element-old-w`
- `--modus-stage-element-w`
- `--modus-stage-element-x`
- `--modus-stage-element-y`
- `--modus-stage-fire-u`
- `--modus-stage-fire-w`
- `--modus-stage-ft4-earth-u`
- `--modus-stage-ft4-earth-w`
- `--modus-stage-ft4-fire-u`
- `--modus-stage-ft4-fire-w`
- `--modus-stage-ft4-water-u`
- `--modus-stage-ft4-water-w`
- `--modus-stage-ft4-wind-u`
- `--modus-stage-ft4-wind-w`
- `--modus-stage-ft4-x-adjust`
- `--modus-stage-ft4-x-tolerance`
- `--modus-stage-water-u`
- `--modus-stage-water-w`
- `--modus-stage-wind-u`
- `--modus-stage-wind-w`
- `--name-screen-exclude-offsets`
- `--name-screen-inplace-body-encoding`
- `--name-screen-inplace-fill`
- `--name-screen-inplace-rows`
- `--narrow-advance`
- `--narrow-chars`
- `--no-lui`
- `--no-preserve-legacy-controls`
- `--no-strip-controls`
- `--non-strict-direct-mips`
- `--original-bytes-column`
- `--out`
- `--pair-kern`
- `--paren-advance`
- `--parser-force`
- `--pixy-name-suffix-force`
- `--pixy-name-suffix-include-created-modus`
- `--planet-info-dash-u`
- `--planet-info-dash-w`
- `--planet-info-earth-u`
- `--planet-info-earth-w`
- `--planet-info-earth-x-shift`
- `--planet-info-element-x-shift`
- `--planet-info-fire-u`
- `--planet-info-fire-w`
- `--planet-info-fire-x-shift`
- `--planet-info-force`
- `--planet-info-planet-clut`
- `--planet-info-planet-u`
- `--planet-info-planet-v`
- `--planet-info-water-u`
- `--planet-info-water-w`
- `--planet-info-water-x-shift`
- `--planet-info-wind-u`
- `--planet-info-wind-w`
- `--planet-info-wind-x-shift`
- `--planet-stage-terminator`
- `--planet-stage-terminator-force`
- `--planet-title-advance-delta`
- `--planet-title-advance-override`
- `--planet-title-allow-broad`
- `--planet-title-bitmap-guard`
- `--planet-title-bitmap-tracking`
- `--planet-title-clear-index`
- `--planet-title-force`
- `--planet-title-min-payload-addr`
- `--planet-title-no-bitmap-guard`
- `--planet-title-no-pattern-guard`
- `--planet-title-pattern-min-per-slot`
- `--planet-title-pattern-x`
- `--planet-title-pattern-y`
- `--planet-title-payload-addr`
- `--planet-title-payload-addrs`
- `--planet-title-shadow-index`
- `--planet-title-slot-h`
- `--planet-title-slot-pitch`
- `--planet-title-slot-y-offsets`
- `--planet-title-space-advance`
- `--planet-title-text-index`
- `--planet-title-text-w`
- `--planet-title-text-x`
- `--planet-title-titles`
- `--planet-title-tracking`
- `--planet-title-y`
- `--plsel-bottom-v`
- `--plsel-following-a-left-u`
- `--plsel-following-a-right-u`
- `--plsel-following-b-left-u`
- `--plsel-following-b-right-u`
- `--plsel-following-dynamic-a-right-u`
- `--plsel-following-top-v`
- `--plsel-following-u-shift`
- `--plsel-following-uv-only`
- `--plsel-following-x-shift`
- `--plsel-force`
- `--plsel-left-u`
- `--plsel-left-x`
- `--plsel-main-right-u`
- `--plsel-main-right-x`
- `--plsel-main-uv-only`
- `--plsel-patch-json`
- `--plsel-top-v`
- `--pointer-column`
- `--preserve-u-controls`
- `--ptr32-cluster-gap`
- `--ptr32-cluster-min`
- `--ptr32-cluster-unique-min`
- `--ptr32-exclude-offsets`
- `--ptr32-include-offsets`
- `--ptr32-policy`
- `--ptr32-ranges-json`
- `--ptr32-sections`
- `--ptr32-slice-count`
- `--ptr32-slice-index`
- `--punct-advance`
- `--punct-chars`
- `--report-text-force`
- `--reports-dir`
- `--selector-gsbox-force`
- `--selector-gsbox-height`
- `--selector-gsbox-left-width`
- `--selector-gsbox-no-width`
- `--selector-gsbox-no-x-shift`
- `--selector-gsbox-off-width`
- `--selector-gsbox-off-x-shift`
- `--selector-gsbox-on-width`
- `--selector-gsbox-on-x-shift`
- `--selector-gsbox-onoff-height`
- `--selector-gsbox-onoff-y-shift`
- `--selector-gsbox-proceed-extra-y-shift`
- `--selector-gsbox-proceed-y`
- `--selector-gsbox-right-width`
- `--selector-gsbox-vibration-end`
- `--selector-gsbox-vibration-start`
- `--selector-gsbox-y-shift`
- `--selector-gsbox-yes-width`
- `--selector-gsbox-yes-x-shift`
- `--selector-gsbox-yesno-height`
- `--selector-gsbox-yesno-y-shift`
- `--sheet`
- `--skip-direct-mips`
- `--slash-advance-delta`
- `--slash-fixed-advance`
- `--space-advance`
- `--stage-tim03-na-line-u, --stage-tim04-na-line-slot-u`
- `--stage-tim03-planet-u`
- `--stage-tim03-planet-v`
- `--stage-tim03-uv-force`
- `--stage-tim04-dash-w`
- `--stage-tim04-earth-u`
- `--stage-tim04-earth-w`
- `--stage-tim04-earth-x-shift`
- `--stage-tim04-element-x-shift`
- `--stage-tim04-fire-u`
- `--stage-tim04-fire-w`
- `--stage-tim04-fire-x-shift`
- `--stage-tim04-na-bad-word`
- `--stage-tim04-na-ft4-edge-filter-force`
- `--stage-tim04-na-ft4-packet-cmd`
- `--stage-tim04-na-ft4-source-filter-force`
- `--stage-tim04-na-good-word`
- `--stage-tim04-na-packet-base`
- `--stage-tim04-na-packet-filter-force`
- `--stage-tim04-na-template-color`
- `--stage-tim04-na-template-old-u`
- `--stage-tim04-na-template-size`
- `--stage-tim04-na-template-u-force`
- `--stage-tim04-na-template-v`
- `--stage-tim04-object-element-packet-prefix`
- `--stage-tim04-object-overlay-base-x`
- `--stage-tim04-object-overlay-marker`
- `--stage-tim04-object-overlay-na-slot-ram`
- `--stage-tim04-object-overlay-na-x`
- `--stage-tim04-object-overlay-object-guard-halfword`
- `--stage-tim04-water-u`
- `--stage-tim04-water-w`
- `--stage-tim04-water-x-shift`
- `--stage-tim04-wind-u`
- `--stage-tim04-wind-w`
- `--stage-tim04-wind-x-shift`
- `--text-column`
- `--text-primitive-capacity-glyphs, --text-primitive-capacity`
- `--text-primitive-force`
- `--tree-buffer-force`
- `--tree-copy-limit`
- `--tree-terminator`
- `--tree-terminator-force`
- `--tuto-close-wait-frames`
- `--tuto21-force`
- `--two-byte-advance`
- `--uppercase-advance-delta`
- `--xa-clip-id-column`
- `--xa-text-column`
- `--xa-text-sheet`
- `--xa-text-xlsx`
- `--xlsx`
- `--xoff-left1-chars`
- `--xoff-left2-chars`
- `--xoff-right1-chars`
- `--xoff-right2-chars`
- `--xoff-right3-chars`

**Examples:**

```bash
python impl/pixygarden_MAIN.EXE_Patcher.py --help
```

**Line count:** 7296

### `impl/pixygarden_archive_tool.py`
**Purpose:** Grouped archive wrapper for CDF, FAT, DAT, and related container tasks.

**Script docstring:**

```text
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
```

**Detected options:** none found by static scan.

**Examples:**

```bash
python impl/pixygarden_archive_tool.py --help
```

**Line count:** 82

### `impl/pixygarden_graphics_tool.py`
**Purpose:** Grouped graphics wrapper for TIM, PNG, LZ, and graphics extraction/reinsertion workflows.

**Script docstring:**

```text
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
```

**Detected options:** none found by static scan.

**Examples:**

```bash
python impl/pixygarden_graphics_tool.py --help
```

**Line count:** 67

### `impl/pixygarden_text_tool.py`
**Purpose:** Grouped text wrapper for SCR, TREE, HELP, TXT, and string workflows.

**Script docstring:**

```text
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
```

**Detected options:** none found by static scan.

**Examples:**

```bash
python impl/pixygarden_text_tool.py --help
```

**Line count:** 80

