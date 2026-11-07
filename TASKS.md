# Group Task Board — Native ESP-IDF Badge Port

Shared work queue for **Claude** (Claude Code, terminal: filesystem + build + flash)
and **Copilot** (editor: scope + code + review). Discussion lives in
`COPILOT_CLAUDE_BUFFER.md`; this file is the *who-does-what-and-is-it-done*.

## How to use this board
- **Claim** a task by putting your name in `Owner` and setting status `DOING`.
  Don't take a task the other agent is already `DOING`.
- **Status**: `TODO` → `DOING` → `REVIEW` (done, wants a second look) → `DONE`.
  `BLOCKED` = can't start; put the blocker in Notes.
- One task = one checkbox. Add sub-tasks indented beneath if you split one.
- Keep IDs stable (`T01`…). Add new tasks at the end with the next ID.
- When you finish or claim something, drop a one-line note in the buffer thread so
  the other agent knows to look. Don't edit a task the other owns except to add a
  Notes line prefixed with your name.

Legend: ☐ TODO · ◐ DOING · ⟳ REVIEW · ✅ DONE · ⛔ BLOCKED

---

## 0. Decisions — mostly resolved by Copilot IN CODE (see buffer turn 2)

- ✅ **T01** Scope = **badge + on-device LLM** (tinystories/llama2). _per main/ code_
- ✅ **T02** Language = **C++** throughout. _per namespace badge:: / .hpp_
- ✅ **T03** Project root = `native_firmware/`. **Ownership (user ruling, supersedes
  buffer t2): Claude owns ALL firmware code** (`native_firmware/**` incl. `main/`),
  behavior (behave) tests and unit tests, plus repo plumbing. **Copilot owns the
  emulator only** (`emulator/**`). _Copilot: user said "you do the emulator and
  claude does code and behave and unit tests"._
- ✅ **T04/T05** Testing/hardware = **QEMU emulator** (`CONFIG_BADGE_EMULATOR`) +
  real badge on hand. _per Kconfig.projbuild / sdkconfig.emulator.defaults_
- ✅ **T06** UI engine = **direct framebuffer, no LVGL.** _Copilot decided: matches
  the user's original instruction, avoids a 1.3 MB RGB565 LVGL buffer + per-flush
  conversion for a 1-bpp tri-colour panel, and the planes are exactly what the
  emulator dumps. Claude: bring back your plane-based `components/epd` and drop
  lvgl from `main/idf_component.yml`; `epd_panel.cpp` (LVGL) can go._
- ✅ **T07** Safety rules. _Copilot: never flash the badge without the user's
  explicit OK; no automatic posting to moltshit/any social site from firmware._

## 1. Project skeleton & build — owner: CLAUDE

- ☐ **T10** ESP-IDF project skeleton (top `CMakeLists.txt`, `main/`,
  `sdkconfig.defaults` for ESP32-S3 + octal PSRAM). _Blocked on T01–T03._
- ☐ **T11** Partition table (factory app + LittleFS vfs, mirror badge layout
  from report: factory @0x10000 3MB, vfs @0x310000). _Blocked on T03._
- ☐ **T12** LittleFS component + build-time FS image from `badge_fs/data`
  (graphics, gallery, schedule.csv). _Blocked on T03._
- ☐ **T13** Host build harness: CMake/Make that compiles the HAL-mocked core on
  macOS for unit tests (no ESP-IDF). _Blocked on T04._
- ☐ **T14** First `idf.py build` green with an empty `app_main` + banner.

## 2. Drivers (ported from badge_fs/lib) — owner: CLAUDE unless claimed

- ☐ **T20** HAL seam: interfaces for display / buttons / LEDs / time / storage so
  core logic builds on host. _Blocked on T02, T04._
- ☐ **T21** **E-paper driver** — Pervasive Displays 9.69" 960×672 dual-COG,
  SoftSPI, from `badge_fs/lib/epd.py` + `board.py`. **Hardest task; highest risk.**
- ☐ **T22** Black/red framebuffer + draw primitives (clear, fill, blit, rect).
- ☐ **T23** Button driver (debounce/scan) from `badge_fs/lib/button.py`.
- ☐ **T24** WS2812 LED bar from `badge_fs/lib/led_bar.py` (RMT driver).
- ☐ **T25** PNG decoder path (the badge decodes PNG in Python; pick a C decoder,
  e.g. lightweight zlib + custom, or an ESP component). _From `large_png.py`/`PNGdecoder.py`._
- ☐ **T26** Bitmap font / text plate rendering into framebuffer.

## 3. App framework & apps — owner: ? (Copilot candidate for app logic)

- ☐ **T30** App base + AppSwitcher (auto-list, menu ± select, A/B launch) from
  `app_switcher.py` / `base_app.py`.
- ☐ **T31** Config/JSON (`panel_config.json` load_counter) from `json_configs.py`.
- ☐ **T32** Splash app + load-counter boot flow (from `main.py`; note egg at #42).
- ☐ **T33** Nametag app (text plate over `hello_i_am_blank.png`).
- ☐ **T34** Gallery app (random of 44 PNGs, re-roll timer).
- ☐ **T35** Schedule/Workshops app (parse `schedule.csv`, per-stage layout).
- ☐ **T36** (scope-dependent) LLM app: llama2.c engine → e-paper, from `esp32-llm`.

## 4. Tests — owner: CLAUDE

- ✅ **T40** Host unit tests (`tests/unit/`, pytest, **23 passing**): schedule.csv
  parsing, RGB565→black/red/white + MONO_HLSB packing (cross-checked vs
  `emu.write_png`), LED menu mapping, `emu.py` partition/LittleFS helpers.
  Contracts live as reference models in `tests/support/badge_spec.py`. _Done without
  a HAL seam — reference-model approach; add real host-compiled C tests as the
  firmware grows pure modules._
- ◐ **T41** On-target/Unity smoke — partially covered by behave+QEMU (T42); native
  Unity on real hardware still TODO.
- ✅ **T42** behave BDD (`tests/behave/`) driving QEMU via `emulator/emu.py`.
  `boot.feature` **passes in QEMU** (reaches app_main, mounts LittleFS, no panic);
  `app_switch`/`terminal`/`tinystories` are `@wip` TDD specs awaiting the T55/T58
  firmware logging. _Owner: Claude._
- ✅ **T43** CI: `unit-tests` job (pytest) in `ci.yml`; `.github/workflows/emulator.yml`
  runs probe self-test + `boot_smoke.scn` + `behave`. _Owner: Claude._

## 5. Emulator (QEMU) — owner: COPILOT (`emulator/**` only)

- ⟳ **T50** Harness `emulator/emu.py`: build with overlay into
  `emulator/out/<proj>`, merge flash, pack LittleFS into `storage`, efuse, run,
  scripted `test` with fail-fast on reboot loops. _Owner: Copilot · REVIEW_
- ⟳ **T51** 8 MB **octal PSRAM** in QEMU (needs Espressif QEMU 9.2.2; setup in
  `emulator/README.md`). _Owner: Copilot · verified: "Found 8MB PSRAM device"_
- ⟳ **T52** gdb framebuffer dump → PNG (`fbdump`). Verified on the probe.
  _Owner: Copilot · needs T55 in firmware to work on native_firmware._
- ⟳ **T53** Scenarios: `boot_smoke.scn` (PASS on current tree), `probe_selftest.scn`
  (PASS). _Owner: Copilot_
- ✅ **T54** Emulator CI job → `.github/workflows/emulator.yml` (promoted from
  Copilot's `emulator/ci-emulator.yml`), extended with a `behave` step. _Claude_
- ☐ **T55** Firmware emulator contract (in `emulator/README.md`):
  `badge_emu_fb_black/red` planes + `EMU_EPD_REFRESH n` log, `@btn <name>` UART
  buttons, `EMU_LED` logs. _Owner: Claude (firmware side)._
- ✅ **T56** Removed `native_firmware/sdkconfig.emulator.defaults` (superseded by
  `emulator/sdkconfig.emulator`). _Owner: Claude._
- ⟳ **T57** Chrome dev UI `emulator/web/` (`start.sh`): emulator screen via gdb,
  buttons, live reload, Web Serial monitor, compile + flash with esptool-js.
  _Owner: Copilot · verified on the probe (build/boot/buttons/live reload/hw
  build+manifest). Real-device flash untested; user-initiated only._
- ☐ **T58** Firmware `@fb` console command → `EMU_FB <w> <h> <raw|z> <b64>` on
  hardware and emulator, and `@btn` read from the USB-Serial/JTAG console on
  hardware (enables "Mirror screen" in the web UI). _Owner: Claude._
- ⟳ **T59** Hardware debug: backtrace decode in the web UI (emulator + device,
  ELF-SHA check), `emulator/hwdebug.sh` (OpenOCD/gdb over built-in USB-JTAG).
  _Owner: Copilot · decode verified via probe `@panic`; JTAG untested on badge.
  Firmware ask (Claude): `coredump` partition + `CONFIG_ESP_COREDUMP_ENABLE_TO_FLASH`._
- ⟳ **T60** Per-scenario flash image (`emu.make_flash` + `Qemu(flash=, snapshot=)`)
  for behave FS fixtures. _Owner: Copilot · verified on probe boot counter. Claude:
  use `snapshot=True` in environment.py (state currently leaks between scenarios)._
- ☐ **T61** Fix behave step bugs found in review (readiness wait on `EMU_READY`,
  racy refresh step, `EMU_LED fill`, blank-screen fbdump) + host-compiled C unit
  tests (T13). _Owner: Claude. See buffer COPILOT turn 3._

---

## Changelog
- 2026-11-07 — Claude: **issue #9 flash/FS safety guards.** `test_partition_safety.py`
  locks partitions.csv to the stock BADGE_REPORT layout (names/type/subtype/offset/size,
  contiguous, ≤8 MB, app≠storage collision). `storage_safety.feature`: normal boot
  mounts without formatting; a corrupt FS (garbage over the storage partition, booted
  via Qemu snapshot) fails to mount, the firmware keeps running, and nothing is
  auto-formatted. **Coredump decision: deferred (won't-do now)** — keep the stock
  layout; documented in partitions.csv. Both behave scenarios pass in QEMU.
- 2026-11-07 — Claude: **issue #7 schedule parser → badge_core C** + host tests +
  firmware wiring (Schedule/Workshops parse /data/schedule.csv via the C parser).
- 2026-11-07 — Claude: **issue #4 EPD golden-sequence test.** Refactored the driver
  sequence behind a HAL seam (`components/epd/epd_seq.c` + `epd_seq.h`; `epd.c` keeps
  the GPIO/SPI HAL). `tests/unit/test_epd_sequence.py` compiles the *shipped*
  `epd_seq.c`, drives it through a capture HAL (ctypes), and asserts the transcript
  matches the stock `epd.py` reference (`tests/support/epd_sequence.py`): command
  order (images before CoG init), DC-DC ramp values, and documented shutdown pin
  states (CS1=high, RST=low, DC=low; CS2/power left as stock — flagged for hw bring-up).
  169 unit tests pass; emulator builds (0x43a60, 91% free); preflight still green.
- 2026-11-07 — Claude: **T55 / no-LVGL firmware pass.** Dropped LVGL; restored
  plane-based `components/epd` + `board`; new no-LVGL `app_main` with console
  (@btn/@app/EMU_READY), badge_core app-switcher, LED menu (EMU_LED), terminal echo,
  direct-framebuffer draw. epd exposes `badge_emu_fb_*` + logs `EMU_EPD_REFRESH`.
  behave graduated: boot/app_switch/terminal/gallery/schedule-render now pass in
  QEMU (9 scenarios, 0 fail); added `preflight.feature` (1-boot memory + pixel-exact
  display validation) and `test_memory_budget.py` (50 unit tests). Advances #3/#5,
  enables #6/#7/#8 content work. QEMU 9.2.2 still occasionally SIGSEGVs across many
  boots — mitigated with an inter-scenario settle; Copilot added Qemu snapshot isolation.
- 2026-11-06 — Claude: fixed the 4 behave step bugs Copilot found (readiness wait
  on EMU_READY, refresh poll, EMU_LED fill, blank-screen guard). Started T13: pure-C
  `components/badge_core` (classify/pack/LED menu/config) + ctypes host tests — 41
  unit tests pass, real C now under test; firmware builds with it. T55/T58/coredump
  are the next firmware pass. (buffer t5)
- 2026-11-06 — Claude: landed `tests/` (23 pytest unit + behave/QEMU), CI unit +
  emulator jobs (T40/T42/T43/T54 ✅), did T56. behave boot PASSes in QEMU. Acked
  role split + T55 contract in buffer t3.
- 2026-11-06 — Copilot: added T57 web dev UI (Chrome, Web Serial) and T58 `@fb`.
  Claude owns T13/T40/T41/T42 (unit + behave tests).
- 2026-11-06 — Copilot: user split roles (Claude = code + behave + unit tests,
  Copilot = emulator). Decided T06 = no LVGL. Added §5 emulator tasks.
- 2026-11-06 — Claude seeded the board. All of §0 is Copilot's to decide; §1–§2
  are mine to execute once §0 unblocks them. §3 app logic is a natural split point
  if Copilot wants to own apps while I own drivers/build. Claim what you want.
- 2026-11-07 — Claude: **issue #6 Nametag text rendering** (commit faa57f2).
  Pure-C 8x8 font renderer in badge_core (font.c: draw_char/draw_text/text_width
  over vendored font8x8_basic, reuses badge_plane_put — host-tested). Nametag app
  draws centered black name + red subtitle; new `@name` console cmd. 12 unit tests
  + nametag.feature (passes in QEMU). Firmware builds (0x479b0, 91% free). Gallery
  PNG decode (lodepng) = remaining #6 sub-part, still TODO. Buffer t7 flags the
  fix/review-batch-1 collision (edits deleted epd_panel.cpp) + trunk drift
  (origin/dev stale at fdbd897; proposing FF dev → scaffold tip).
- 2026-11-07 — Claude: **issue #6 COMPLETE — Gallery PNG decode** (commit 2807fef).
  New `components/png`: vendored lodepng 20261102 (zlib, decode-only) + badge_png
  wrapper → badge_core's new `badge_blit_rgba` (RGBA→classify→planes, centered,
  host-tested, 8 unit tests). Gallery app decodes /data/gallery/NNN.png; `@gallery
  <n>` console cmd. test_png.py host-integration-decodes the real staged gallery
  PNGs (palette→red, grayscale→none, junk rejected). gallery.feature de-wip'd,
  passes in QEMU (black+red ink). Firmware 0x4ebf0 (90% free); RGBA buffer in PSRAM.
  249 unit tests pass. **Also consolidated the trunk:** FF'd origin/dev off stale
  fdbd897 and merged cloud PRs #8 (tinystories hardening) + #10 (release pipeline);
  origin/dev == origin/feat/esp-idf-scaffold. fix/review-batch-1 still needs Copilot
  to rebase (edits the deleted epd_panel.cpp).
