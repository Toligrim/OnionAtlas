from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Neighbor:
    onion_host: str
    direction: str
    edge_count: int
    observations: int


def service_neighbors(connection: sqlite3.Connection, onion_host: str) -> list[Neighbor]:
    service = connection.execute("SELECT id FROM services WHERE onion_host = ?", (onion_host,)).fetchone()
    if service is None:
        return []
    service_id = int(service["id"])
    outgoing = connection.execute(
        """SELECT target.onion_host, COUNT(DISTINCT l.id) AS edge_count, COALESCE(SUM(l.times_seen), 0) AS observations
           FROM links l JOIN pages p ON p.id = l.source_page_id JOIN services target ON target.id = l.target_service_id
           WHERE p.service_id = ? AND l.target_service_id IS NOT NULL AND l.target_service_id != ?
           GROUP BY target.id, target.onion_host""",
        (service_id, service_id),
    ).fetchall()
    incoming = connection.execute(
        """SELECT source.onion_host, COUNT(DISTINCT l.id) AS edge_count, COALESCE(SUM(l.times_seen), 0) AS observations
           FROM links l JOIN pages p ON p.id = l.source_page_id JOIN services source ON source.id = p.service_id
           WHERE l.target_service_id = ? AND p.service_id != ? GROUP BY source.id, source.onion_host""",
        (service_id, service_id),
    ).fetchall()
    values = [Neighbor(row["onion_host"], "outgoing", int(row["edge_count"]), int(row["observations"])) for row in outgoing]
    values.extend(Neighbor(row["onion_host"], "incoming", int(row["edge_count"]), int(row["observations"])) for row in incoming)
    return sorted(values, key=lambda item: (item.direction, -item.observations, item.onion_host))
