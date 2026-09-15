from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from onionatlas.timeutil import to_iso, utcnow


@dataclass(frozen=True, slots=True)
class ServiceRecord:
    id: int
    onion_host: str
    current_status: str


def get_service(connection: sqlite3.Connection, onion_host: str) -> ServiceRecord | None:
    row = connection.execute(
        "SELECT id, onion_host, current_status FROM services WHERE onion_host = ?",
        (onion_host,),
    ).fetchone()
    if row is None:
        return None
    return ServiceRecord(row["id"], row["onion_host"], row["current_status"])


def ensure_service(connection: sqlite3.Connection, onion_host: str) -> tuple[int, bool]:
    now = to_iso(utcnow())
    cursor = connection.execute(
        """
        INSERT INTO services(
            onion_host, first_seen_at, current_status, created_at, updated_at
        ) VALUES (?, ?, 'unknown', ?, ?)
        ON CONFLICT(onion_host) DO NOTHING
        """,
        (onion_host, now, now, now),
    )
    created = cursor.rowcount == 1
    row = connection.execute(
        "SELECT id FROM services WHERE onion_host = ?", (onion_host,)
    ).fetchone()
    if row is None:  # pragma: no cover - defensive against external DB corruption
        raise RuntimeError("service upsert did not produce a row")
    return int(row["id"]), created
