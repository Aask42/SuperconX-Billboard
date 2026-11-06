#!/usr/bin/env bash
# On-hardware debugging over the ESP32-S3's built-in USB-JTAG (same USB-C cable; no probe needed).
# Never writes flash. Usage:
#   emulator/hwdebug.sh check                 # attach OpenOCD, confirm both cores are visible, detach
#   emulator/hwdebug.sh openocd               # keep OpenOCD running (gdb :3333, telnet :4444)
#   emulator/hwdebug.sh gdb [ELF]             # gdb attached to the running badge (start openocd first)
#   emulator/hwdebug.sh decode ELF ADDR...    # panic backtrace addresses -> func at file:line
# ELF defaults to the web UI's hardware build (emulator/out/<project>-hw).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
IDF="${IDF_PATH:-$HOME/esp/esp-idf-v5.4.2}"
# shellcheck disable=SC1091
command -v openocd >/dev/null 2>&1 || . "$IDF/export.sh" >/dev/null
PROJECT="${PROJECT:-native_firmware}"
CFG=(-f board/esp32s3-builtin.cfg)

default_elf() {
  local bdir="$HERE/out/${PROJECT}-hw"
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["app_elf"])' \
    "$bdir/project_description.json" 2>/dev/null || { echo "no hw build in $bdir (Compile for badge first)" >&2; exit 1; }
}

case "${1:-}" in
  check)
    out="$(perl -e 'alarm 20; exec @ARGV' openocd "${CFG[@]}" -c 'init; targets; halt; reg pc; resume; shutdown' 2>&1 || true)"
    echo "$out" | grep -E 'Error|esp32s3\.(cpu|core)|JTAG tap|pc \(' || true
    if echo "$out" | grep -qE 'JTAG tap: esp32s3\.(tap|cpu)0? tap/device found|esp32s3\.(cpu|core)0.*(Examined|halted)'; then
      echo "OK: badge reachable over USB-JTAG"
    else
      echo "FAIL: no ESP32-S3 on USB-JTAG (cable? badge powered? another openocd running?)"; exit 1
    fi ;;
  openocd) exec openocd "${CFG[@]}" ;;
  gdb)
    elf="${2:-$(default_elf)}"
    exec xtensa-esp32s3-elf-gdb "$elf" -ex 'set remotetimeout 10' -ex 'target extended-remote :3333' \
      -ex 'mon reset halt' -ex 'maintenance flush register-cache' -ex 'thb app_main' ;;
  decode)
    elf="${2:?ELF}"; shift 2
    exec xtensa-esp32s3-elf-addr2line -pfiaC -e "$elf" "$@" ;;
  *) sed -n '2,8p' "$0"; exit 2 ;;
esac
