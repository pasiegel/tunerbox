"""Every test runs against its own empty data folder (settings.json + tunerbox.db), never your real one."""
import os
import shutil
import tempfile
from pathlib import Path

import pytest

# must be set before tunerbox.config is imported: it picks the data folder at import time
_DATA = Path(tempfile.mkdtemp(prefix="tunerbox-tests-"))
os.environ["TUNERBOX_DATA"] = str(_DATA)

from tunerbox import config, db, guide  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_data():
    """Empty data folder, default settings and a new database for each test."""
    for p in _DATA.iterdir():
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    config._cache = None
    db.init()
    guide.invalidate()
    guide._cache["all"] = None
    yield _DATA


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_DATA, ignore_errors=True)
