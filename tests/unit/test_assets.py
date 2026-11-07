"""Validate the LittleFS payload (vfs_root/data) carries everything the apps load.
If an asset is missing from the image, the app that needs it fails on hardware."""
from pathlib import Path

import pytest

from support.badge_spec import GALLERY_COUNT, gallery_name, REQUIRED_GRAPHICS

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "native_firmware" / "vfs_root" / "data"

# The payload is gitignored (third-party badge assets, pending the
# maintainer's publish decision), so a clean clone has nothing staged:
# payload tests SKIP there, never fail. Only tests that read the payload
# are guarded; pure-logic tests below always run.
requires_data = pytest.mark.skipif(
    not DATA.is_dir(),
    reason="native_firmware/vfs_root/data not staged (gitignored payload)",
)


def test_data_dir_is_staged():
    if not DATA.is_dir():
        pytest.skip("native_firmware/vfs_root/data not present")
    assert (DATA / "schedule.csv").exists()
    assert (DATA / "panel_config.json").exists()


@requires_data
def test_gallery_has_all_44_sequential_pngs():
    gallery = DATA / "gallery"
    for n in range(1, GALLERY_COUNT + 1):
        assert (gallery / gallery_name(n)).exists(), f"missing gallery image {gallery_name(n)}"
    # And no unexpected count drift.
    assert len(list(gallery.glob("*.png"))) == GALLERY_COUNT


@requires_data
def test_required_graphics_present():
    graphics = DATA / "graphics"
    missing = [g for g in REQUIRED_GRAPHICS if not (graphics / g).exists()]
    assert not missing, f"missing graphics: {missing}"


@requires_data
def test_llm_model_and_tokenizer_staged():
    models = DATA / "models"
    assert (models / "stories260K.bin").exists(), "LLM checkpoint not staged for /data/models"
    assert (models / "tok512.bin").exists(), "tokenizer not staged for /data/models"


def test_gallery_name_rejects_out_of_range():
    with pytest.raises(ValueError):
        gallery_name(0)
    with pytest.raises(ValueError):
        gallery_name(GALLERY_COUNT + 1)
