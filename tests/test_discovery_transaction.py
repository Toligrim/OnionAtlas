from __future__ import annotations

import sqlite3

import pytest

import onionatlas.discovery.engine as engine
from onionatlas.discovery.engine import ensure_http_source, process_candidates
from tests.helpers import onion_host


def test_discovery_candidate_import_is_atomic(db: sqlite3.Connection, monkeypatch) -> None:
    source_id = ensure_http_source(
        db,
        name="atomic-test",
        url_template="https://example.test/list",
    )
    first = onion_host(501)
    second = onion_host(502)
    real_enqueue = engine.enqueue_url
    calls = 0

    def fail_on_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic failure")
        return real_enqueue(*args, **kwargs)

    monkeypatch.setattr(engine, "enqueue_url", fail_on_second)

    with pytest.raises(RuntimeError, match="synthetic failure"):
        process_candidates(db, source_id=source_id, candidates=[first, second])

    assert db.execute("SELECT COUNT(*) FROM discovery_runs").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM discovery_candidates").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM services").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM frontier").fetchone()[0] == 0
