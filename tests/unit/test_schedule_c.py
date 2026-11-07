"""Host tests for the shipped C schedule parser (badge_core/schedule.c), issue #7.
Compiles badge_core and drives badge_schedule_parse via ctypes against the real
schedule.csv and malformed inputs — the actual C, not a model."""
import ctypes
import shutil
import subprocess
from pathlib import Path

import pytest

from support import badge_spec as spec

REPO = Path(__file__).resolve().parents[2]
CORE = REPO / "native_firmware" / "components" / "badge_core"
SRCS = sorted((CORE / "src").glob("*.c"))

CANDIDATES = [
    REPO / "native_firmware" / "vfs_root" / "data" / "schedule.csv",
    REPO / "badge_fs" / "data" / "schedule.csv",
]
MAX = 128


class Talk(ctypes.Structure):
    _fields_ = [
        ("day", ctypes.c_char * 8), ("time", ctypes.c_char * 8),
        ("stage", ctypes.c_char * 24), ("talk", ctypes.c_char * 128),
        ("speaker", ctypes.c_char * 64), ("image", ctypes.c_char * 40),
    ]


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if cc is None:
        pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("sched") / "libbadgecore.so"
    subprocess.run([cc, "-shared", "-fPIC", "-O2", "-std=c99",
                    "-I", str(CORE / "include"), *map(str, SRCS), "-o", str(out)], check=True)
    dll = ctypes.CDLL(str(out))
    dll.badge_schedule_parse.restype = ctypes.c_int
    dll.badge_schedule_parse.argtypes = [ctypes.c_char_p, ctypes.POINTER(Talk), ctypes.c_int]
    dll.badge_schedule_is_talk.restype = ctypes.c_int
    dll.badge_schedule_is_talk.argtypes = [ctypes.c_char_p]
    return dll


def _parse(lib, text):
    buf = (Talk * MAX)()
    n = lib.badge_schedule_parse(text.encode("utf-8"), buf, MAX)
    return n, buf


def _schedule_path():
    for p in CANDIDATES:
        if p.exists():
            return p
    pytest.skip("schedule.csv not found")


def test_parses_real_csv_like_the_reference(lib):
    text = _schedule_path().read_text(encoding="utf-8")
    n, buf = _parse(lib, text)
    ref = spec.parse_schedule(_schedule_path())
    assert n == len(ref), f"C parsed {n}, reference {len(ref)}"
    # Spot-check a few rows match the reference field-for-field.
    for i in (0, 1, n - 1):
        assert buf[i].day.decode() == ref[i]["day"]
        assert buf[i].time.decode() == ref[i]["time"]
        assert buf[i].stage.decode() == ref[i]["stage"]
        assert buf[i].talk.decode() == ref[i]["talk"]


def test_talks_vs_workshops_split(lib):
    n, buf = _parse(lib, _schedule_path().read_text(encoding="utf-8"))
    talks = sum(1 for i in range(n) if lib.badge_schedule_is_talk(buf[i].stage))
    workshops = n - talks
    assert talks > 0 and workshops > 0 and talks + workshops == n


def test_quoted_comma_title_stays_one_field(lib):
    text = ('day,time,stage,talk,speaker,image\n'
            'Sat,10:00,Lamarr,"Hacking, Soldering, and You",A. Mouse,x.png\n')
    n, buf = _parse(lib, text)
    assert n == 1
    assert buf[0].talk.decode() == "Hacking, Soldering, and You"
    assert buf[0].speaker.decode() == "A. Mouse"


def test_malformed_rows_skipped_without_crashing(lib):
    text = (
        "day,time,stage,talk,speaker,image\n"
        "Sat,10:00,Lamarr,Good Row,Sp,a.png\n"   # ok
        "\n"                                       # blank
        "Sat,nope,Lamarr,Bad Time,Sp,b.png\n"      # bad time -> skip
        "Sat,11:00,Lamarr,Missing Fields\n"        # too few -> skip
        "Sat,12:00,Hopper,Too,Many,Fields,Here,Extra\n"  # too many -> skip
        "Sun,9:00,Room A,Workshop,Sp,c.png\r\n"    # ok, CRLF, H:MM time
    )
    n, buf = _parse(lib, text)
    assert n == 2
    assert buf[0].talk.decode() == "Good Row"
    assert buf[1].day.decode() == "Sun" and buf[1].time.decode() == "9:00"


def test_empty_and_garbage_inputs_are_safe(lib):
    for text in ("", "\n\n\n", "no header here", "a,b,c,d,e,f\n" * 500):
        n, _ = _parse(lib, text)
        assert 0 <= n <= MAX   # never crashes, never overruns the buffer
