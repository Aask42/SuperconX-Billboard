# Supercon 10 "Billboard Badge" — Firmware Teardown

Date: 2026-11-06 · Method: read-only flash dump over USB (`esptool.py read_flash`, 460800 baud) + offline analysis. Nothing was written to the badge.

## TL;DR

- The badge is **not running ESP32-DIV**. It runs a custom **LVGL MicroPython** build ("Billboard Badge") with a small Python app framework on a LittleFS filesystem.
- What it does: e-paper **conference badge**: splash screen, **Nametag**, **Gallery** (44 artworks), **Sat/Sun talk schedules**, **Workshops**, plus an 8-LED menu bar and a button-driven app switcher.
- **No radio use in the shipped app code.** Wi-Fi/BLE/ESP-NOW/WebREPL are compiled into the interpreter but are not started (WebREPL lines in `boot.py` are commented out).
- **Completely open:** no secure boot, no flash encryption, download mode and USB-JTAG enabled. You can dump, modify and reflash it freely, and the app layer is plain `.py` you can edit.
- The ESP32-DIV repo is a different device (RF offensive/defensive toolkit on a TFT + CC1101/NRF24 shield). Don't flash its images onto this badge: different display, pins, partition layout and missing radios.

## Hardware (from chip probe + pin definitions in firmware)

| Item | Detail |
|---|---|
| SoC | ESP32-S3 (QFN56) rev v0.2, dual-core, Wi-Fi + BLE |
| Memory | 8 MB quad flash, 8 MB embedded octal PSRAM |
| USB | Native USB-Serial/JTAG (shows up as `/dev/tty.usbmodem*`) |
| Display | Pervasive Displays 9.69" (960×672) **red/black/white e-paper**, two driver halves (CS1=GPIO13, CS2=GPIO3), SoftSPI SCK=21 / MOSI=47, DC=10, RST=11, BUSY=12/8, power-enable=48 |
| Buttons | Menu− GPIO40, Menu+ GPIO15 (silkscreen swapped per source comment), User A GPIO39, User B GPIO16, Boot GPIO0 |
| LEDs | 8× WS2812 on GPIO38 (menu position indicator) |
| Status LED | GPIO1 (active-low) |
| Battery sense | GPIO14 ADC, ½ divider on regulator input |
| SAO ports | 3× SAO; shared I²C0 SCL=18 / SDA=17 @ 400 kHz; GPIO pairs SAO1 2/42, SAO2 4/5, SAO3 7/6 |
| Test points | TP8=GPIO9, TP9=GPIO41; back-pad UART noted as unusable (`board.py` TODO) |

## Security posture (eFuses, read-only)

| eFuse | Value |
|---|---|
| SECURE_BOOT_EN | False |
| SPI_BOOT_CRYPT_CNT (flash encryption) | Disabled |
| DIS_DOWNLOAD_MODE / ENABLE_SECURITY_DOWNLOAD | False / False |
| DIS_USB_JTAG / DIS_PAD_JTAG / SOFT_DIS_JTAG | False / False / 0 |
| DIS_USB_SERIAL_JTAG(_DOWNLOAD_MODE) | False |

Implication: anyone with USB access can read or replace the firmware. That's great for hacking it, but it also means badges can be tampered with if left unattended.

## Flash layout

| Partition | Type | Offset | Size | Contents |
|---|---|---|---|---|
| bootloader | — | 0x0000 | — | ESP-IDF 2nd-stage bootloader |
| partition table | — | 0x8000 | — | |
| nvs | data/nvs | 0x9000 | 24 KB | **Empty** (all 0xFF, so no stored Wi-Fi creds) |
| phy_init | data/phy | 0xF000 | 4 KB | RF calibration |
| factory | app | 0x10000 | 3 MB | MicroPython + LVGL image (≈2.6 MB used, SHA-256 validated) |
| vfs | data/fat* | 0x310000 | ~4.9 MB | **LittleFS** (block 4096) with all Python apps/assets |

\*Subtype 0x81 is MicroPython's standard `vfs` partition; it's formatted LittleFS, not FAT.

## Native firmware (factory app)

- Build path string: `/home/ben/projects/2026-Billboard_Badge-Private/firmware/lv_micropython/ports/esp32/build-ESP32_GENERIC_S3-BILLBOARD_BADGE/` → a **private** board definition on top of public `lv_micropython`.
- Machine string: "Billboard Badge Generic ESP32S3 module with Octal-SPIRAM with ESP32S3"; **ESP-IDF v5.2.3**; built **2026-11-04** (two days before this dump).
- Version strings conflict (`v1.20.0-2527.g44aa2b153.dirty` vs. `MicroPython-1.24.1`): it's a dirty fork; treat the exact base as ~1.24.
- Notable built-in modules: `lvgl` (LVGL 9 with fonts, qrcode, gif, barcode widgets), `framebuf`, `btree`, `neopixel`, `machine`, `esp32`, **`network`, `bluetooth`, `espnow`, `socket`, `ssl`, `webrepl`**. The radios are available to anyone writing apps but unused by default.

## Python layer (LittleFS)

```
boot.py                 # default; WebREPL commented out
main.py                 # boot sequence + asyncio task launcher
apps/   base_app, gallery, nametag, schedule, template
lib/    app_switcher, app_list, base_app, board, button, epd, large_png,
        PNGdecoder, zlib, led_bar, sao, json_configs
data/   panel_config.json, schedule.csv, app_list (btree), user_apps.py,
        rendered_luts.py, graphics/*.png (10), gallery/001-044.png
```

### Boot flow (`main.py`)
1. Imports the switcher, buttons, e-paper driver, and LED bar.
2. Increments `load_counter` in `data/panel_config.json` (currently **3** on this badge).
   - First boot (`< 2`): draws `supercon-x-splash.png`.
   - **Easter egg at boot #42**: shows a "never EPD" image (but see the bugs below).
3. Starts 4 asyncio tasks: button scan (1 ms), e-paper refresh worker, LED sequencer, app switcher.
4. Prints a 60 s heartbeat + `micropython.mem_info()` to USB serial.

### App framework
- `AppSwitcher` auto-discovers every `.py` in `/apps`, rewrites `data/user_apps.py`, and instantiates every `BaseApp` subclass (except `base_app`/`template`). App names are cached in a `btree` at `data/app_list`.
- **Menu ±** enters selection mode; the LED bar shows the cursor. **User A/B** launches the app. An app's `run()` is called in a loop with `launched=True` on the first pass.
- **Drop a new `.py` into `/apps` and it shows up in the menu.** This is the intended hacking path.

### Shipped apps
| App | Behaviour |
|---|---|
| Nametag | Draws `hello_i_am_blank.png` with an LVGL text plate (name strings are empty by default; edit `nametag.py` to set yours) |
| Gallery | Random image from `/data/gallery/001-044.png`, re-rolls every 300 s |
| Sat Talks / Sun Talks | Parses `data/schedule.csv` (43 talks: 2 Fri, 25 Sat, 16 Sun), lays out per-stage (Lamarr/Hopper) boxes with LVGL |
| Workshops | Same engine, workshop list |
| Template | Example app (excluded from the menu) |

### Display pipeline
- LVGL renders RGB565 into a 1.3 MB PSRAM buffer. A **pure-Python flush callback** converts each pixel to black/red framebuffers, then the driver sends halves to both COGs and performs a full global refresh. Expect **multi-second updates**; there's no partial refresh. Drawing straight into `display.black` / `display.red` (`framebuf`) is far faster than LVGL.
- PNGs are decoded by a Python decoder (`large_png`/`PNGdecoder`), which is why loads print "This will take a while…".

## Bugs / oddities found

1. **Easter egg broken:** `main.py` loads `/data/graphics/never-epd.png`, but the file is `never_epd.png`. Boot #42 will raise `OSError` → main crashes to REPL.
2. **SAO addressing broken:** `Sao.set_address(addr)` ignores `addr` and sets `self.address = 0`, also creating an `I2CTarget` on the same bus as the controller.
3. Back-pad UART is unusable per `board.py` (UART1 conflicts with the e-paper), so **USB CDC is the only serial path**.
4. Nametag ships with empty strings, so it displays the blank "Hello I am" art until edited.
5. Every boot rewrites `panel_config.json` and `user_apps.py`, which causes minor flash wear and is the reason boot is slightly slow.

## About the ESP32-DIV repo (asked to compare)

`cifertech/ESP32-DIV` (checked out at `90f7967`, v1.7.2) is an Arduino C++ firmware for a separate handheld: ESP32-S3 + 2.8" ILI9341 TFT + shield with 3× NRF24, CC1101, IR, optional PN532/GPS. Features: Wi-Fi scanning/monitoring and attacks (deauth, beacon spam, captive portal, karma), BLE scanning/spoofing/jamming/BLE-HID ducky, 2.4 GHz (NRF24) sniff/replay/MouseJack, Sub-GHz replay/jam/brute, IR record/replay, RFID/NFC read/clone, GPS wardriving. Several features are disruptive and only legal on your own gear or with authorization.

**It is not compatible with this badge.** Pins, display, partition layout and the required external radios all differ.

## Artifacts (local, keep private: the full dump contains your badge state)

| File | SHA-256 |
|---|---|
| `dump/badge_full_8MB.bin` | `d951ac56a6fc99de66bec5855e974d2183058b29727e87453640fa5d3916abeb` |
| `dump/badge_app.bin` (factory app, 3 MB) | `d0a76b47e5c49d237498ab79b2026e5b79a751a8364f0856fd0d2cf5b2b896b9` |
| `dump/badge_vfs.bin` (LittleFS) | extracted to `badge_fs.orig/` (pristine) and `badge_fs/` (working copy) |

**Restore to stock:** `esptool.py --port /dev/tty.usbmodem1101 write_flash 0 dump/badge_full_8MB.bin`

## Unknowns

- The private board config (`BILLBOARD_BADGE`) isn't public. Rebuilding the native image means recreating it from the pin map above.
- Native C code was identified by strings and module tables only and was not disassembled. No hidden native behaviour was ruled out beyond that.
- The bootloader wasn't analysed.
