# Safety Checklist

## Archive structure

- CDF entry count matches the expected file.
- FAT path set matches the expected file.
- No entry size exceeds its allocation.
- No invalid nonblank FAT table entries are present.
- Rebuilt archive sizes are expected and documented.

## Text

- Displayed TREE/HELP text ends with `00` followed by `FF` padding.
- No translated text accidentally restores `0x39` as a terminator unless the executable expects it.
- CP932 encoding is valid where CP932 is required.
- EVENT/SCR-like files are rebuilt with a structural template.

## Graphics

- TIM dimensions are unchanged unless a code patch expects new dimensions.
- Indexed PNG palette order is preserved.
- 4bpp raw pixel index edits are verified.
- Recompressed graphics decode successfully.

## MAIN.EXE

- Patcher is applied to a clean base executable.
- Hook markers do not indicate an unexpected older experimental hook.
- Main menu and Neredy graphics show no corruption.
- Planet, Modus, Object Overlay, Report, and selector prompts are checked.

## Emulator testing

- DuckStation boots and displays text correctly.
- At least one additional emulator boots and reaches gameplay.
- Tutorial and EVENT scenes do not crash.
- Save/load and clear-data flows are tested with a clean memory card.
