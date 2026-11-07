# Test coverage audit

Maps badge functionality to the tests that cover it. Legend: ✅ covered ·
◐ partial · ⬜ gap. "unit" = `tests/unit` (pytest, host). "behave" = `tests/behave`
(QEMU via `emu.py`); `@wip` = spec written, waits on firmware.

| Area | Behaviour | unit | behave | Notes / gap owner |
|---|---|---|---|---|
| Boot | PSRAM up, app_main reached, no panic | — | ✅ boot | passes in QEMU |
| Boot | LittleFS `/data` mounts | ✅ emu_helpers | ✅ boot | make_littlefs round-trip |
| Boot | `load_counter` bump + persist | ✅ config | ⬜ | — |
| Boot | splash while counter<2 | ✅ config | ⬜ | behave needs an FS fixture w/ counter 0 (`emu.py test --fs`) |
| Boot | easter egg at #42 + `never_epd.png` name | ✅ config | ⬜ | regression guard for the stock hyphen bug |
| EPD | RGB565→black/red/white rule | ✅ pixel | — | cross-checked vs `emu.write_png` |
| EPD | MONO_HLSB packing + red precedence | ✅ pixel | — | — |
| EPD | CoG init / DC-DC / refresh command sequence | ⬜ | ◐ @wip fbdump | **gap**: add a golden-sequence test (my `epd.c` vs `epd.py`) |
| EPD | rendered screen snapshot | ✅ write_png | ◐ @wip fbdump | needs T55 `badge_emu_fb_*` symbols |
| LEDs | menu-bar mapping (page/cursor/colours) | ✅ led_menu | — | ported from `led_bar.py` |
| LEDs | `bitmask_to_leds` | ✅ led_menu | — | — |
| LEDs | submenu single-LED | ✅ c_core | — | C-backed |
| LEDs | live LED output | — | ◐ @wip | needs `EMU_LED` (T55) |
| Buttons | press → action | — | ◐ @wip | `@btn` (T55); no host debounce test (firmware-level) |
| Apps | switcher nav (select/wrap/launch/back) | ✅ c_core | ◐ @wip app_switch | C state machine vs model |
| Apps | Nametag render | — | ◐ @wip app_switch | fbdump |
| Apps | Gallery (44 PNGs, naming) | ✅ assets | ◐ @wip gallery | random/re-roll is firmware RNG — integration only |
| Apps | Schedule + Workshops (parse, split) | ✅ schedule | ◐ @wip schedule | 43 rows, talks vs rooms |
| Apps | Terminal echo | — | ◐ @wip terminal | — |
| Apps | TinyStories LLM | ✅ llm_model | ◐ @wip tinystories | model/tokenizer format vs vocab 512 |
| Assets | gallery/graphics/models/schedule staged | ✅ assets | — | guards the LittleFS payload |
| Emulator | harness helpers (partitions, png, littlefs) | ✅ emu_helpers | — | — |
| Contract | T55 symbols + log formats present | — | ⬜ | **gap**: conformance scenario once firmware emits |

## Host C tests (T13) — the C, not just the model

`tests/unit/test_c_core.py` compiles `native_firmware/components/badge_core/`
(`epd_pack.c`, `led_menu.c`, `config.c`, `switcher.c`) into a shared lib and asserts
it matches `badge_spec.py`. So these rows are backed by the **actual firmware C**:
pixel classification, MONO_HLSB packing, LED menu + submenu, load-counter/boot-image,
and the app-switcher navigation state machine. Still model-only: schedule CSV parsing
(moves to C with the Schedule app).

## Pre-hardware verdict (no badge needed)

**Covered now and green** (50 pytest + behave in QEMU): every pure logic unit
(classification, packing, LEDs, switcher nav, config/boot flow), the data/asset
contracts (schedule, gallery, graphics, LLM model+tokenizer), the LittleFS packer,
the **static memory budget** (planes + LLM + FS fit PSRAM/partitions), and in QEMU:
**boot + PSRAM load + LittleFS mount + the full draw→plane→refresh→gdb-dump display
path with pixel-exact assertions** (`preflight.feature`), plus the app-switcher
(LED nav + launch), terminal echo, gallery and schedule-render apps. The T55
emulator contract (`badge_emu_fb_*`, `EMU_EPD_REFRESH`, `EMU_READY`, `@btn`,
`EMU_LED`) is implemented. None of this needs the badge.

**Specced, pending more FIRMWARE (not hardware)** — QEMU-testable once built:
- in-app day paging (schedule `@wip`), the TinyStories LLM run (`@wip @slow`);
- EPD command-sequence golden test (driver bytes vs `epd.py`, issue #4);
- real PNG decode + text-plate pixel assertions (issues #6), schedule parser in C
  (issue #7), TinyStories hardening (issue #8);
- boot-flow fixtures for splash/#42 — need Copilot's `emu.make_flash` per-scenario FS helper.

**Genuinely HARDWARE-ONLY (correctly deferred until the badge is connected, user-gated):**
- on-target Unity smoke: real SPI e-paper refresh + timing, WS2812 RMT output,
  physical button GPIO/debounce, battery ADC, SAO I²C, flash/restore-to-stock,
  USB-JTAG coredump pull. These cannot and should not have host/QEMU tests.

Bottom line: the pre-hardware test scaffolding is complete for what exists today;
the remaining non-hardware tests are blocked only on the firmware emitting the
emulator contract, which is the next firmware pass — not on hardware.
