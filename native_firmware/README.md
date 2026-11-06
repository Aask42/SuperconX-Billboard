# native_firmware — SuperconX Billboard (ESP-IDF)

Native ESP-IDF rewrite of the Supercon 10 Billboard Badge firmware (originally an
LVGL-MicroPython fork). Target: **ESP32-S3**, 8 MB flash, 8 MB octal PSRAM,
**ESP-IDF v5.4.x**.

## Layout

```
native_firmware/
  CMakeLists.txt          project
  sdkconfig.defaults      S3 + octal PSRAM + custom partitions
  partitions.csv          mirrors the stock badge (factory 3MB + LittleFS data)
  main/app_main.cpp       entry point (scaffold)
  components/
    board/                pin map (board.h), from the stock firmware + teardown
    epd/                  e-paper driver (porting badge_fs/lib/epd.py) — WIP
```

## Build

```sh
. ~/esp/esp-idf-v5.4.2/export.sh     # or your IDF install
cd native_firmware
idf.py set-target esp32s3            # first time only
idf.py build
```

## Flash / monitor (badge on native USB)

```sh
idf.py -p /dev/tty.usbmodem1101 flash monitor
```

To restore the stock firmware, see `../BADGE_REPORT.md`.

## Rendering

Direct framebuffer draw (no LVGL): two 1-bpp planes (black + red) in `epd.h`;
drawing primitives + PNG/font blitting write straight into them, then
`epd_refresh()` does a full global panel update (multi-second).

Status and task ownership live in `../TASKS.md`; design discussion in
`../COPILOT_CLAUDE_BUFFER.md`.
