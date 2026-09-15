from __future__ import annotations

import sqlite3

import pytest

from onionatlas.storage import migrate
from onionatlas.storage.repositories import ensure_service
from tests.helpers import onion_host


def test_migrations_are_idempotent(db: sqlite3.Connection) -> None:
    migrate(db)
    versions = db.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    assert [row[0] for row in versions] == [1, 2]


def test_wal_and_foreign_keys_enabled(db: sqlite3.Connection) -> None:
    assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_service_uniqueness(db: sqlite3.Connection) -> None:
    host = onion_host(1)
    first_id, created = ensure_service(db, host)
    second_id, second_created = ensure_service(db, host)
    assert created is True
    assert second_created is False
    assert first_id == second_id


def test_foreign_key_violation_is_rejected(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO pages(
                service_id, canonical_url, path, query, first_seen_at, created_at, updated_at
            ) VALUES (999, 'http://x/', '/', '', 'now', 'now', 'now')
            """
        )
