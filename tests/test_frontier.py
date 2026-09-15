from __future__ import annotations

import sqlite3
from datetime import timedelta

from onionatlas.frontier import enqueue_url, frontier_stats, lease_tasks, release_expired_leases
from onionatlas.timeutil import to_iso, utcnow

from tests.helpers import onion_host


HOST_A = onion_host(1)
HOST_B = onion_host(2)


def test_enqueue_is_persistent_and_deduplicated(db: sqlite3.Connection) -> None:
    first = enqueue_url(db, HOST_A, priority=10)
    second = enqueue_url(db, f"http://{HOST_A}/#x", priority=20)
    assert first.frontier_id == second.frontier_id
    assert first.frontier_created is True
    assert second.frontier_created is False
    assert frontier_stats(db) == {"total": 1, "queued": 1}
    row = db.execute("SELECT priority FROM frontier WHERE id = ?", (first.frontier_id,)).fetchone()
    assert row["priority"] == 20


def test_lease_order_and_unique_attempts(db: sqlite3.Connection) -> None:
    enqueue_url(db, HOST_A, priority=1)
    enqueue_url(db, HOST_B, priority=9)
    tasks = lease_tasks(db, worker_id="worker-a", limit=2, lease_seconds=60)
    assert [task.target_url for task in tasks] == [f"http://{HOST_B}/", f"http://{HOST_A}/"]
    assert tasks[0].attempt_id != tasks[1].attempt_id
    assert frontier_stats(db)["leased"] == 2


def test_expired_lease_is_requeued(db: sqlite3.Connection) -> None:
    enqueue_url(db, HOST_A)
    task = lease_tasks(db, worker_id="worker-a", limit=1)[0]
    db.execute(
        "UPDATE frontier SET lease_expires_at = ? WHERE attempt_id = ?",
        (to_iso(utcnow() - timedelta(seconds=1)), task.attempt_id),
    )
    assert release_expired_leases(db) == 1
    second = lease_tasks(db, worker_id="worker-b", limit=1)[0]
    assert second.attempt_id != task.attempt_id
    assert second.target_url == task.target_url
