from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import timedelta

from onionatlas.domain.models import CrawlTask, FetchPolicy
from onionatlas.domain.urls import CanonicalOnionURL, canonicalize_onion_url
from onionatlas.storage.repositories import ensure_service
from onionatlas.timeutil import to_iso, utcnow


@dataclass(frozen=True, slots=True)
class EnqueueOutcome:
    frontier_id: int
    service_id: int
    canonical: CanonicalOnionURL
    service_created: bool
    frontier_created: bool


def _create_evidence(
    connection: sqlite3.Connection,
    *,
    service_id: int,
    evidence_type: str,
    observed_at: str,
    source_url: str | None = None,
    source_service_id: int | None = None,
    anchor_text: str | None = None,
    is_first_discovery: bool = False,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO service_evidence(
            service_id, evidence_type, source_url, source_service_id,
            anchor_text, observed_at, is_first_discovery
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            service_id,
            evidence_type,
            source_url,
            source_service_id,
            anchor_text,
            observed_at,
            int(is_first_discovery),
        ),
    )
    return int(cursor.lastrowid)


def enqueue_url(
    connection: sqlite3.Connection,
    raw_url: str,
    *,
    depth: int = 0,
    priority: float = 0.0,
    evidence_type: str = "manual",
    source_url: str | None = None,
    source_service_id: int | None = None,
    anchor_text: str | None = None,
    source_evidence_id: int | None = None,
    requeue_existing: bool = False,
) -> EnqueueOutcome:
    canonical = canonicalize_onion_url(raw_url)
    now = to_iso(utcnow())
    service_id, service_created = ensure_service(connection, canonical.onion_host)
    if source_evidence_id is None:
        source_evidence_id = _create_evidence(
            connection,
            service_id=service_id,
            evidence_type=evidence_type,
            observed_at=now,
            source_url=source_url,
            source_service_id=source_service_id,
            anchor_text=anchor_text,
            is_first_discovery=service_created,
        )

    row = connection.execute(
        "SELECT id, state, priority, depth FROM frontier WHERE target_url = ?",
        (canonical.url,),
    ).fetchone()
    if row is None:
        cursor = connection.execute(
            """
            INSERT INTO frontier(
                target_url, service_id, state, priority, depth,
                first_discovered_at, last_enqueued_at, next_attempt_at,
                source_evidence_id, created_at, updated_at
            ) VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                canonical.url,
                service_id,
                priority,
                max(0, depth),
                now,
                now,
                now,
                source_evidence_id,
                now,
                now,
            ),
        )
        return EnqueueOutcome(
            int(cursor.lastrowid), service_id, canonical, service_created, True
        )

    new_state = row["state"]
    next_attempt = None
    if requeue_existing and row["state"] in {"done", "dead"}:
        new_state = "queued"
        next_attempt = now
    connection.execute(
        """
        UPDATE frontier
        SET priority = MAX(priority, ?),
            depth = MIN(depth, ?),
            last_enqueued_at = ?,
            source_evidence_id = COALESCE(source_evidence_id, ?),
            state = ?,
            next_attempt_at = COALESCE(?, next_attempt_at),
            updated_at = ?
        WHERE id = ?
        """,
        (
            priority,
            max(0, depth),
            now,
            source_evidence_id,
            new_state,
            next_attempt,
            now,
            row["id"],
        ),
    )
    return EnqueueOutcome(int(row["id"]), service_id, canonical, service_created, False)


def release_expired_leases(connection: sqlite3.Connection) -> int:
    now = to_iso(utcnow())
    cursor = connection.execute(
        """
        UPDATE frontier
        SET state = 'queued', lease_owner = NULL, lease_id = NULL, attempt_id = NULL,
            leased_at = NULL, lease_expires_at = NULL, updated_at = ?
        WHERE state = 'leased' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?
        """,
        (now, now),
    )
    return cursor.rowcount


def lease_tasks(
    connection: sqlite3.Connection,
    *,
    worker_id: str,
    limit: int,
    lease_seconds: int = 300,
    policy: FetchPolicy | None = None,
) -> list[CrawlTask]:
    if limit <= 0:
        return []
    policy = policy or FetchPolicy()
    now_dt = utcnow()
    now = to_iso(now_dt)
    expires = to_iso(now_dt + timedelta(seconds=max(1, lease_seconds)))
    connection.execute("BEGIN IMMEDIATE")
    try:
        release_expired_leases(connection)
        rows = connection.execute(
            """
            SELECT id, target_url, depth
            FROM frontier
            WHERE state IN ('queued', 'retry')
              AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
            ORDER BY priority DESC, first_discovered_at ASC, id ASC
            LIMIT ?
            """,
            (now, limit),
        ).fetchall()
        tasks: list[CrawlTask] = []
        for row in rows:
            lease_id = str(uuid.uuid4())
            attempt_id = str(uuid.uuid4())
            connection.execute(
                """
                UPDATE frontier
                SET state = 'leased', lease_owner = ?, lease_id = ?, attempt_id = ?,
                    leased_at = ?, lease_expires_at = ?, attempt_count = attempt_count + 1,
                    updated_at = ?
                WHERE id = ? AND state IN ('queued', 'retry')
                """,
                (worker_id, lease_id, attempt_id, now, expires, now, row["id"]),
            )
            tasks.append(
                CrawlTask(
                    task_id=f"frontier:{row['id']}",
                    attempt_id=attempt_id,
                    lease_id=lease_id,
                    lease_expires_at=expires,
                    target_url=row["target_url"],
                    depth=int(row["depth"]),
                    policy=policy,
                )
            )
        connection.execute("COMMIT")
        return tasks
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def frontier_stats(connection: sqlite3.Connection) -> dict[str, int]:
    values = {"total": int(connection.execute("SELECT COUNT(*) FROM frontier").fetchone()[0])}
    for row in connection.execute("SELECT state, COUNT(*) AS n FROM frontier GROUP BY state"):
        values[str(row["state"])] = int(row["n"])
    return values
