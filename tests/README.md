# Tests

Two layers, both describing the badge's intended functionality:

- **`unit/`** — fast host tests (pytest), no hardware or QEMU. They pin the data
  and algorithm contracts the firmware must satisfy and validate the emulator
  harness's pure helpers. The contracts live as executable reference models in
  `support/badge_spec.py` (ported from the stock `badge_fs/` sources).
- **`behave/`** — behaviour specs (Gherkin) that drive the real firmware in
  Copilot's QEMU emulator by reusing `emulator/emu.py`. Scenarios tagged `@wip`
  describe behaviour not built yet (TDD) and are skipped by default.

## Setup

```sh
# project venv already has esptool / pyserial / littlefs-python
.venv/bin/pip install -r tests/requirements.txt
```

## Unit tests (no QEMU — this is what CI runs)

```sh
.venv/bin/python -m pytest tests/unit
```

## Behave against the emulator (needs ESP-IDF + Espressif QEMU 9.2.2)

```sh
source ~/esp/esp-idf-v5.4.2/export.sh
cd tests/behave
behave                    # implemented scenarios (boots the firmware in QEMU)
behave -t @wip            # the not-yet-built specs
EMU_SKIP_BUILD=1 behave   # reuse the previous emulator build
```

`environment.py` builds + images the firmware once via `emu.py`, then gives each
`@emu` scenario a fresh `emu.Qemu` with snapshot mode enabled. Flash writes are
discarded when the scenario exits, so they cannot affect later scenarios. Tests
that need persistence across separate QEMU processes must use their own flash
image without snapshot mode. If IDF/QEMU aren't present, `@emu` scenarios skip
(so the suite is safe to invoke anywhere).

## Emulator contract these tests assume (T55, see `../emulator/README.md`)

- firmware exposes `badge_emu_fb_black` / `badge_emu_fb_red` (960x672 MONO_HLSB
  planes) and logs `EMU_EPD_REFRESH <n>` after each refresh;
- UART line `@btn <menu_minus|menu_plus|a|b>` = a button press;
- LED activity logged as `EMU_LED <i> <r> <g> <b>`.
