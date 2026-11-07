"""Regression tests for the behave emulator lifecycle."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def behave_environment():
    path = Path(__file__).resolve().parents[1] / "behave" / "environment.py"
    spec = spec_from_file_location("badge_behave_environment", path)
    assert spec is not None and spec.loader is not None
    environment = module_from_spec(spec)
    spec.loader.exec_module(environment)
    return environment


@pytest.mark.parametrize(
    ("tags", "gdb_port"),
    [
        ({"emu"}, None),
        ({"emu", "fbdump"}, 4321),
    ],
)
def test_scenario_discards_flash_writes(
    behave_environment, monkeypatch, tmp_path, tags, gdb_port
):
    qemu = Mock()
    monkeypatch.setattr(behave_environment.emu, "Qemu", qemu)
    monkeypatch.setattr(behave_environment.emu, "free_port", lambda: 4321)
    context = SimpleNamespace(emu_available=True, build_dir=tmp_path)
    scenario = SimpleNamespace(effective_tags=tags, name="Snapshot isolation")

    behave_environment.before_scenario(context, scenario)

    qemu.assert_called_once_with(
        tmp_path,
        gdb_port=gdb_port,
        echo=False,
        log=tmp_path / "behave_Snapshot_isolation.log",
        snapshot=True,
    )
