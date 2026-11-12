"""Test configuration.

The DSH sandbox denies pytest's default temporary directory, so tests use a
workspace-local directory instead.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tmp_path() -> Path:
    base = ROOT / ".tmp" / "pytest"
    base.mkdir(parents=True, exist_ok=True)
    directory = base / uuid.uuid4().hex
    directory.mkdir()
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)
