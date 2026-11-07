"""Guard the separate hardware and emulator configuration contracts."""

from pathlib import Path

import pytest

import emu


ROOT = Path(__file__).resolve().parents[2]


def read_defaults(path):
    settings = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        key, separator, value = line.partition("=")
        if separator and key.startswith("CONFIG_"):
            settings[key] = value
    return settings


def test_hardware_defaults_and_emulator_overlay_keep_required_settings(tmp_path, capsys):
    hardware = read_defaults(ROOT / "native_firmware" / "sdkconfig.defaults")
    overlay = read_defaults(ROOT / "emulator" / "sdkconfig.emulator")
    effective = {**hardware, **overlay}
    required_common = {
        "CONFIG_IDF_TARGET": '"esp32s3"',
        "CONFIG_ESPTOOLPY_FLASHSIZE_8MB": "y",
        "CONFIG_SPIRAM": "y",
        "CONFIG_SPIRAM_MODE_OCT": "y",
        "CONFIG_SPIRAM_SPEED_80M": "y",
        "CONFIG_SPIRAM_USE_MALLOC": "y",
        "CONFIG_PARTITION_TABLE_CUSTOM": "y",
        "CONFIG_PARTITION_TABLE_CUSTOM_FILENAME": '"partitions.csv"',
    }
    for key, value in required_common.items():
        assert hardware.get(key) == value, key
        assert effective.get(key) == value, key

    assert hardware["CONFIG_ESPTOOLPY_FLASHMODE_QIO"] == "y"
    assert hardware["CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG"] == "y"
    assert {
        key: overlay.get(key)
        for key in (
            "CONFIG_BADGE_EMULATOR",
            "CONFIG_ESPTOOLPY_FLASHMODE_QIO",
            "CONFIG_ESPTOOLPY_FLASHMODE_DIO",
            "CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG",
            "CONFIG_ESP_CONSOLE_UART_DEFAULT",
            "CONFIG_ESP_TASK_WDT_EN",
            "CONFIG_ESP_INT_WDT",
        )
    } == {
        "CONFIG_BADGE_EMULATOR": "y",
        "CONFIG_ESPTOOLPY_FLASHMODE_QIO": "n",
        "CONFIG_ESPTOOLPY_FLASHMODE_DIO": "y",
        "CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG": "n",
        "CONFIG_ESP_CONSOLE_UART_DEFAULT": "y",
        "CONFIG_ESP_TASK_WDT_EN": "n",
        "CONFIG_ESP_INT_WDT": "n",
    }
    assert (emu.FLASH_SIZE, emu.PSRAM) == ("8MB", "8M")

    sdkconfig = tmp_path / "sdkconfig"
    sdkconfig.write_text("".join(f"{key}={value}\n" for key, value in effective.items()))
    assert emu.check_config(sdkconfig) is None
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    ("missing", "warning"),
    [
        ("CONFIG_ESPTOOLPY_FLASHMODE_DIO", "flash mode is not DIO"),
        ("CONFIG_ESP_CONSOLE_UART_DEFAULT", "console is not UART0"),
        ("CONFIG_BADGE_EMULATOR", "CONFIG_BADGE_EMULATOR missing"),
    ],
)
def test_check_config_warns_for_each_missing_emulator_setting(
    tmp_path, capsys, missing, warning
):
    required = (
        "CONFIG_ESPTOOLPY_FLASHMODE_DIO",
        "CONFIG_ESP_CONSOLE_UART_DEFAULT",
        "CONFIG_BADGE_EMULATOR",
    )
    sdkconfig = tmp_path / "sdkconfig"
    sdkconfig.write_text(
        "".join(
            f"# {key} is not set\n" if key == missing else f"{key}=y\n"
            for key in required
        )
    )
    assert emu.check_config(sdkconfig) is None
    diagnostics = capsys.readouterr().err
    assert diagnostics.count("emu: warning:") == 1
    assert warning in diagnostics


def test_check_config_warns_about_all_missing_settings(tmp_path, capsys):
    sdkconfig = tmp_path / "sdkconfig"
    sdkconfig.write_text("")
    assert emu.check_config(sdkconfig) is None
    diagnostics = capsys.readouterr().err
    assert diagnostics.count("emu: warning:") == 3
    assert "flash mode is not DIO" in diagnostics
    assert "console is not UART0" in diagnostics
    assert "CONFIG_BADGE_EMULATOR missing" in diagnostics
