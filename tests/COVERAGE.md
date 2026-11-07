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
| LEDs | submenu single-LED | ⬜ | — | minor gap |
| LEDs | live LED output | — | ◐ @wip | needs `EMU_LED` (T55) |
| Buttons | press → action | — | ◐ @wip | `@btn` (T55); no host debounce test (firmware-level) |
| Apps | switcher nav (select/wrap/page) | ⬜ | ◐ @wip app_switch | **gap**: pure-C nav fn + host test once firmware exists |
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
(`epd_pack.c`, `led_menu.c`, `config.c`) into a shared lib and asserts it matches
`badge_spec.py` for the same inputs. So these rows are now backed by the **actual
firmware C**, not only the reference model: pixel classification, MONO_HLSB
packing, LED menu-bar mapping, and the load-counter / boot-image logic.
Still model-only (no C yet): schedule parsing and app-switcher nav — they move to
C-backed as I factor those into `badge_core`.

## Known gaps (to validate with Copilot / do next)

1. **Boot-flow behave fixtures** — splash (counter<2) and egg (#42) need a
   scenario-specific LittleFS with a doctored `panel_config.json`. `emu.py test`
   takes `--fs DIR`; confirm that's the intended way to inject per-scenario data.
2. **EPD command-sequence golden test** — host test asserting the driver emits the
   exact CoG-init/DC-DC/refresh byte sequence from `epd.py` (no QEMU needed).
3. **AppSwitcher nav unit test** — factor the select/wrap/page logic as a pure C
   function so it's host-testable; add once the firmware exists (I own it now).
4. **T55 contract-conformance scenario** — once the firmware exports
   `badge_emu_fb_*` and logs `EMU_EPD_REFRESH`/`EMU_LED`, add a behave scenario that
   checks the symbols (gdb) and log formats directly.
5. **On-target Unity smoke** — real-hardware mount/refresh/button/LED; deferred
   until we flash (user-gated).

Firmware features tagged `@wip` above convert to live acceptance tests as I wire
the T55 logging into the no-LVGL firmware.
