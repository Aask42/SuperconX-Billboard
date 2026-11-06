"""Shared pytest setup: make the reference models and the emulator harness
importable from the unit tests without installing anything."""
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent

for p in (str(TESTS), str(REPO / "emulator")):
    if p not in sys.path:
        sys.path.insert(0, p)
