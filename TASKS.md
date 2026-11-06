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
- ✅ **T03** Project root = `native_firmware/`; Copilot keeps app+drivers in
  `main/`, Claude owns root build config + repo plumbing. _agreed split, buffer t2_
- ✅ **T04/T05** Testing/hardware = **QEMU emulator** (`CONFIG_BADGE_EMULATOR`) +
  real badge on hand. _per Kconfig.projbuild / sdkconfig.emulator.defaults_
- ⚠️ **T06** UI engine = **CONFLICT**: user told Claude "direct framebuffer, no
  LVGL" but Copilot's code uses LVGL 9.2.2. _Escalated to user. BLOCKS whether
  Claude's components/epd (no-LVGL port, now in scratchpad) comes back._

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

## 4. Tests — owner: shared

- ☐ **T40** Host unit tests: schedule.csv parser, config counter, app-switcher
  state machine, PNG decode vs known-good. _Blocked on T13/T20._
- ☐ **T41** On-target Unity smoke: FS mount, e-paper refresh, button edge, LED.
- ☐ **T42** (user floated) behavior tests for app-switcher flows.

---

## Changelog
- 2026-11-06 — Claude seeded the board. All of §0 is Copilot's to decide; §1–§2
  are mine to execute once §0 unblocks them. §3 app logic is a natural split point
  if Copilot wants to own apps while I own drivers/build. Claim what you want.
