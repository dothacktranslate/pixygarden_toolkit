# Format Notes

## CDF archives

CDF archives contain a top-level table with names, sectors, sector counts, and byte sizes. Entry data is sector-aligned. Entry size must remain within allocation unless the table and all following data are rebuilt consistently.

## FAT archives

FAT archives use fixed table entries and data offsets. Slot order matters. Repackers should preserve names, order, and padding unless a full archive relocation is intended.

## TIM graphics

PixyGarden uses TIM graphics, including 4bpp indexed TIMs with multiple CLUT pages.

Safety rules:

```text
Preserve indexed PNG palette order.
Preserve raw pixel index values when the TIM uses shared CLUT pages.
Do not rely on RGB remapping when exact palette indexes matter.
Verify dimensions, CLUT count, and raw index differences after edits.
```

## LZ-compressed TIM data

Some TIM assets are compressed before storage. Use the LZ/TIM tool for decode and encode operations. Always decode rebuilt compressed data again to verify round-trip safety.

## TREE and HELP text

Translated TREE and HELP text slots should normally end with:

```text
00 FF FF FF ...
```

The first `00` terminates displayed text. Remaining bytes should be `FF` padding.

## EVENT and SCR-like scripts

EVENT-style files are not plain text containers. Text is mixed with script control bytes.

Preserve:

```text
control bytes
branch targets
script opcodes
opcode-specific terminators
double-null endings where required
message color/control behavior
```

Use original files as structural templates for sensitive script rebuilds.

## MAIN.EXE patching

Patch from a clean base executable. Avoid stacking experimental patchers onto the same already-patched file unless the patcher explicitly supports replacing existing hook markers or forced retuning.

Important selector behavior:

```text
Normal Yes/No grey boxes use the generic selector hook.
Vibration Function uses the ON/OFF branch of the same hook.
Save Clear Data has a prompt-specific selector adjustment.
Proceed without saving? uses an exact incoming-Y selector adjustment.
```
