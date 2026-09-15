from __future__ import annotations

import sqlite3

from onionatlas.frontier import enqueue_url
from onionatlas.timeutil import to_iso, utcnow


def schedule_due_recrawls(connection: sqlite3.Connection, *, limit: int = 100) -> int:
    now = to_iso(utcnow()) or ""
    rows = connection.execute(
        """SELECT s.id, s.onion_host, s.current_homepage_page_id, p.canonical_url
           FROM services s LEFT JOIN pages p ON p.id = s.current_homepage_page_id
           WHERE s.next_recrawl_at IS NOT NULL AND s.next_recrawl_at <= ?
           ORDER BY s.next_recrawl_at ASC LIMIT ?""",
        (now, max(1, limit)),
    ).fetchall()
    scheduled = 0
    for row in rows:
        target = row["canonical_url"] or f"http://{row['onion_host']}/"
        enqueue_url(connection, target, depth=0, priority=10.0, evidence_type="recrawl", requeue_existing=True)
        connection.execute("UPDATE services SET next_recrawl_at = NULL, updated_at = ? WHERE id = ?", (now, row["id"]))
        scheduled += 1
    return scheduled
