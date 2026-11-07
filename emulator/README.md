# Emulator (QEMU) harness

Runs `native_firmware/` on Espressif's QEMU without touching the badge. Owned by Copilot; firmware code and
its unit/behave tests are owned by Claude (see `../TASKS.md`).

## Setup (once)

```sh
source ~/esp/esp-idf-v5.4.2/export.sh
python -m pip install littlefs-python
# ESP-IDF 5.4.2 pins QEMU 9.0.0, which tops out at 4 MB quad PSRAM. The badge has 8 MB octal,
# so install 9.2.2 next to it (the harness picks the newest under ~/.espressif automatically):
D=~/.espressif/tools/qemu-xtensa/esp_develop_9.2.2_20260417 && mkdir -p $D && cd $D && \
  curl -sL https://github.com/espressif/qemu/releases/download/esp-develop-9.2.2-20260417/qemu-xtensa-softmmu-esp_develop_9.2.2_20260417-aarch64-apple-darwin.tar.xz | tar xJ
```

Override the binary with `EMU_QEMU=/path/to/qemu-system-xtensa`.

## Commands

```sh
python emulator/emu.py build                         # idf.py build -> emulator/out/native_firmware/
python emulator/emu.py run                           # interactive UART0 console, Ctrl-A X quits
python emulator/emu.py test emulator/scenarios/boot_smoke.scn
python emulator/emu.py fbdump screen.png --wait-for 'EMU_EPD_REFRESH 1' --scale 1
```

- `build` layers [sdkconfig.emulator](sdkconfig.emulator) on top of the project's own `sdkconfig.defaults`, in
  a separate build dir, so the hardware `build/` is never touched. Add `--clean-config` after changing defaults.
- `image` (implicit in run/test/fbdump) merges bootloader + partition table + app, and packs
  `native_firmware/vfs_root/data` as LittleFS into the `storage` partition (offset/size read from
  `partitions.csv`). Without `vfs_root/data` (e.g. a fresh clone or CI; the badge assets aren't in git) it packs
  an empty LittleFS. `--fs DIR` picks another directory; `--no-fs` leaves the partition erased.
- `test` exits 0 on pass, 1 on fail, and saves the serial log next to the build. It also fails fast if the
  firmware reboots (panic/abort loop) instead of waiting for the timeout. Timeouts and rejected output both
  exit 1; the error message distinguishes them.
- `fbdump` reports GDB failures or missing/incomplete planes instead of using invalid framebuffer data.

## Scenario files

One step per line (`python emulator/emu.py test -h` for the full list). Blank lines and lines starting with
`#` are ignored; inline comments are not stripped:

```text
# Wait up to 30 s for a regex, after the previous match.
expect 30 main_task: Calling app_main\(\)
send hello
button a
fbdump ../out/screen.png
# Fail if the pattern has appeared in output captured so far.
reject Guru Meditation
```

`send` and `button` append a carriage return; `sendraw` does not. The underlying Python `Qemu.send()`
writes exactly the UTF-8 text supplied by its caller, without appending a line ending.

## What QEMU emulates for this badge

| Badge part | Emulated? | Firmware must do under `CONFIG_BADGE_EMULATOR` |
|---|---|---|
| ESP32-S3 dual core, flash, 8 MB octal PSRAM, UART0 | yes | nothing |
| LittleFS on `storage` | yes (image from harness) | nothing |
| USB-Serial/JTAG console | no | overlay switches console to UART0 |
| GPIO matrix (buttons, EPD BUSY/DC/CS) | no | don't poll BUSY; take buttons from UART |
| SPI e-paper, RMT/WS2812 | no | keep framebuffers in RAM, log LED state |

## Firmware contract (proposed to Claude, implemented in `main/`)

When `CONFIG_BADGE_EMULATOR=y`:

1. **Framebuffer:** two full 960x672 MONO_HLSB planes (bit set = ink, MSB = leftmost, 80640 bytes each) reachable
   as `extern "C" uint8_t *badge_emu_fb_black` and `badge_emu_fb_red`. On each refresh, update them, then log
   `EMU_EPD_REFRESH <n>` (n counts from 1) so tests can sync before `fbdump`.
2. **Buttons:** a UART line `@btn <menu_minus|menu_plus|a|b>` behaves like a press-and-release of that button.
   Other input is untouched (serial terminal keeps working).
3. **LEDs:** log `EMU_LED <index> <r> <g> <b>` (or one `EMU_LED fill r g b`) when the bar changes.
4. **Screen mirror (also on real hardware, any build):** a console line `@fb` prints one line
   `EMU_FB <w> <h> <raw|z> <base64(black plane || red plane)>`; `z` = zlib-compressed. The web UI uses it
   to show the real badge's screen over Web Serial. `@btn` should also work on hardware (USB-Serial/JTAG console).
5. **Ready marker:** print `EMU_READY` once the console is reading input. Input sent before it is lost
   (verified on the probe), so tests and the UI must wait for it before sending `@btn`.

## Web UI (Chrome)

```sh
emulator/web/start.sh                       # native_firmware; or: --project ../probe --fs ../probe/fs
# open http://127.0.0.1:8042 in Chrome/Edge (Web Serial needs a Chromium browser)
```

- **Emulator:** Build & run boots QEMU; every `EMU_EPD_REFRESH` triggers a gdb snapshot that is drawn on the
  canvas (black/red/paper). Badge buttons (or ←/→/a/b with the canvas focused) send `@btn`. Live reload
  rebuilds + reboots on every save in the project.
- **Device (Web Serial):** Connect → device serial log + input; Mirror screen sends `@fb`. Compile for badge
  builds the real-hardware image (project defaults, no emulator overlay) into `emulator/out/<proj>-hw`;
  Flash writes it with vendored [esptool-js](web/vendor/esptool-js) (bootloader, partition table, app, and
  optionally the LittleFS image). Every flash asks for confirmation; flash-on-save is opt-in.
- The server binds to 127.0.0.1 only and rejects cross-origin POSTs. API: `/api/state`, `/api/events` (SSE),
  `/api/build`, `/api/emu/{start,stop,send,button,snapshot}`, `/api/watch`, `/api/manifest`, `/api/fw/<file>`,
  `/api/decode`.

## Test isolation and filesystem fixtures

`emu.Qemu(bdir)` boots the shared `emu_flash.bin`, and the firmware's NVS and LittleFS writes persist into it,
so a counter bumped in one test is still bumped in the next. For tests:

```python
q = emu.Qemu(bdir, snapshot=True)                       # writes are discarded on exit
img = emu.make_flash(project, fixture_dir, tmp / 'f.bin')  # copy of the image with fixture_dir as LittleFS
q = emu.Qemu(bdir, flash=img, snapshot=True)
```

Verified on the probe (`BOOTS:n` counter in `/data/boots.txt`): snapshot keeps it at 1, default goes 1 → 2,
and a fixture with `boots.txt = 41` boots to 42 without touching the shared image.

## Debugging on real hardware

- **Crash backtraces:** panic lines (`Guru Meditation`, `PC :`, `Backtrace:`) in either serial log are decoded
  to `function at file:line` with addr2line against the matching build (emulator ELF, or the "Compile for badge"
  ELF for the device). If the badge's `ELF file SHA256` doesn't match the local build, the UI warns. Try it on
  the probe: type `@panic` in the emulator serial input.
- **JTAG over the same USB-C cable** (ESP32-S3 built-in USB-JTAG, no probe needed), via `emulator/hwdebug.sh`:
  `check` (attach, read PC, detach), `openocd` (gdb :3333), `gdb [ELF]` (resets + halts the badge, breaks at
  `app_main`), `decode ELF ADDR...`. Nothing in it writes flash. If attach fails while the Web Serial monitor is
  connected, disconnect it in the UI and retry (untested whether macOS lets both share the composite device).
- **Not yet:** core dump to flash (needs a `coredump` partition + `CONFIG_ESP_COREDUMP_ENABLE_TO_FLASH`; firmware
  owner).

## CI

[ci-emulator.yml](ci-emulator.yml) is a draft GitHub Actions job (build + self-test + boot smoke). Claude owns
`.github/`, so it lives here until merged. Not yet run on Actions.

## Self-test

[probe/](probe) is a tiny firmware that exercises the harness itself (PSRAM, LittleFS packing, UART input,
gdb framebuffer dump), independent of `native_firmware`:

```sh
python emulator/emu.py build --project emulator/probe
python emulator/emu.py test emulator/scenarios/probe_selftest.scn --project emulator/probe --fs emulator/probe/fs
```
