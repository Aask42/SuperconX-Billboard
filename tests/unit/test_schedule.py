"""Unit tests for the Schedule app's data contract (schedule.csv parsing).

These validate the real badge data and pin the format the native C parser must
honour. When the firmware grows a host-compilable schedule parser, add a test
here that runs it and asserts it matches parse_schedule().
"""
import io
from pathlib import Path

import pytest

from support.badge_spec import (
    parse_schedule, SCHEDULE_FIELDS, VALID_DAYS, VALID_STAGES,
    TALK_STAGES, WORKSHOP_STAGES, _TIME_RE,
)

REPO = Path(__file__).resolve().parents[2]
CANDIDATES = [
    REPO / "native_firmware" / "vfs_root" / "data" / "schedule.csv",
    REPO / "badge_fs" / "data" / "schedule.csv",
]


def _schedule_path() -> Path:
    for p in CANDIDATES:
        if p.exists():
            return p
    pytest.skip("schedule.csv not found in vfs_root/ or badge_fs/")


def test_schedule_parses_and_is_nonempty():
    rows = parse_schedule(_schedule_path())
    assert len(rows) >= 40, f"expected the full conference schedule, got {len(rows)} rows"


def test_every_row_has_all_fields():
    for row in parse_schedule(_schedule_path()):
        for field in SCHEDULE_FIELDS:
            assert field in row and row[field] is not None, f"missing {field}: {row}"


def test_days_and_stages_are_known_values():
    rows = parse_schedule(_schedule_path())
    assert {r["day"] for r in rows} <= VALID_DAYS
    assert {r["stage"] for r in rows} <= VALID_STAGES


def test_schedule_splits_into_talks_and_workshops():
    """One CSV feeds two apps: Schedule (talk stages) and Workshops (rooms)."""
    rows = parse_schedule(_schedule_path())
    talks = [r for r in rows if r["stage"] in TALK_STAGES]
    workshops = [r for r in rows if r["stage"] in WORKSHOP_STAGES]
    assert talks, "no talk rows found"
    assert workshops, "no workshop rows found"
    assert len(talks) + len(workshops) == len(rows), "a row fell outside both groups"


def test_times_look_like_clock_times():
    for row in parse_schedule(_schedule_path()):
        assert _TIME_RE.match(row["time"]), f"bad time {row['time']!r}"


def test_talks_and_speakers_present():
    for row in parse_schedule(_schedule_path()):
        assert row["talk"].strip(), f"empty talk: {row}"
        assert row["speaker"].strip(), f"empty speaker: {row}"


def test_quoted_commas_survive_parsing():
    """A talk title containing a comma must stay one field (RFC4180 quoting).

    This is the trap a naive comma-split C parser falls into; the test documents
    it against a synthetic row rather than relying on the real data to contain one.
    """
    sample = io.StringIO(
        "day,time,stage,talk,speaker,image\n"
        'Sat,10:00,Lamarr,"Hacking, Soldering, and You",A. Nony Mouse,x.png\n'
    )
    import csv
    rows = list(csv.DictReader(sample))
    assert rows[0]["talk"] == "Hacking, Soldering, and You"
    assert rows[0]["speaker"] == "A. Nony Mouse"
