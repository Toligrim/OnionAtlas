from __future__ import annotations

import sqlite3
from datetime import timedelta
from onionatlas.discovery.engine import ensure_http_source, process_candidates
from onionatlas.discovery.recrawl import schedule_due_recrawls
from onionatlas.frontier import enqueue_url, lease_tasks
from onionatlas.importer import import_result
from onionatlas.timeutil import to_iso, utcnow
from tests.helpers import onion_host
from tests.test_importer import success_result

HOSTS = [onion_host(index) for index in range(1, 7)]


def test_external_candidates_are_deduplicated_and_enqueued(db: sqlite3.Connection) -> None:
    source_id = ensure_http_source(db, name="test", url_template="https://example.test/?q={query}")
    outcome = process_candidates(db, source_id=source_id, candidates=[HOSTS[0], HOSTS[0], HOSTS[1], "not-an-onion"], query_text="privacy")
    assert outcome.raw == 4 and outcome.valid == 3 and outcome.unique == 2 and outcome.new == 2 and outcome.rejected == 1
    assert db.execute("SELECT COUNT(*) FROM frontier").fetchone()[0] == 2


def test_low_novelty_eventually_cools_source(db: sqlite3.Connection) -> None:
    source_id = ensure_http_source(db, name="test", url_template="https://example.test/")
    hosts = [onion_host(index) for index in range(100, 110)]
    process_candidates(db, source_id=source_id, candidates=hosts)
    process_candidates(db, source_id=source_id, candidates=hosts)
    process_candidates(db, source_id=source_id, candidates=hosts)
    row = db.execute("SELECT state, low_novelty_streak FROM discovery_sources WHERE id = ?", (source_id,)).fetchone()
    assert row["state"] == "cooling" and row["low_novelty_streak"] >= 2


def test_due_recrawl_requeues_completed_target(db: sqlite3.Connection) -> None:
    enqueue_url(db, HOSTS[0]); task = lease_tasks(db, worker_id="w1", limit=1)[0]; import_result(db, success_result(task))
    db.execute("UPDATE services SET next_recrawl_at = ? WHERE onion_host = ?", (to_iso(utcnow() - timedelta(seconds=1)), HOSTS[0]))
    assert schedule_due_recrawls(db) == 1
    row = db.execute("SELECT state FROM frontier WHERE target_url = ?", (f"http://{HOSTS[0]}/",)).fetchone()
    assert row["state"] == "queued"
