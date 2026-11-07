"""Boot-flow + config contract (main.py / json_configs.py).

Includes a regression guard for the stock easter-egg bug: main.py loaded
'never-epd.png' but the asset is 'never_epd.png', so boot #42 crashed. The
native rewrite must reference the real filename.
"""
import json
from pathlib import Path

import pytest

from support.badge_spec import (
    next_load_counter, boot_image_for, SPLASH_IMAGE, EGG_IMAGE,
    SPLASH_BELOW_COUNT, EASTER_EGG_COUNT,
)

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "native_firmware" / "vfs_root" / "data"


def test_load_counter_starts_at_one_when_absent():
    assert next_load_counter({}) == 1


def test_load_counter_increments():
    assert next_load_counter({"load_counter": 3}) == 4


def test_panel_config_is_valid_json_with_counter():
    if not (DATA / "panel_config.json").exists():
        pytest.skip("vfs_root/data not staged (gitignored; present only locally)")
    cfg = json.loads((DATA / "panel_config.json").read_text())
    assert isinstance(cfg.get("load_counter"), int)


def test_splash_shows_only_on_early_boots():
    assert boot_image_for(0) == SPLASH_IMAGE
    assert boot_image_for(1) == SPLASH_IMAGE
    assert boot_image_for(SPLASH_BELOW_COUNT) is None
    assert boot_image_for(3) is None


def test_easter_egg_boot_number():
    assert boot_image_for(EASTER_EGG_COUNT) == EGG_IMAGE
    assert boot_image_for(EASTER_EGG_COUNT - 1) is None


def test_egg_image_filename_exists_regression():
    """The egg must point at a file that actually exists (the stock bug did not)."""
    if not (DATA / "graphics").is_dir():
        pytest.skip("vfs_root/data/graphics not staged (gitignored; present only locally)")
    assert (DATA / "graphics" / EGG_IMAGE).exists(), \
        f"{EGG_IMAGE} missing — would reproduce the stock never-epd.png crash"
    # And the buggy stock name must NOT be what we rely on.
    assert not (DATA / "graphics" / "never-epd.png").exists()
