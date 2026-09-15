from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from onionatlas.storage import connect, migrate


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "test.sqlite3")
    migrate(connection)
    yield connection
    connection.close()
