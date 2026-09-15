from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchHit:
    page_id: int
    onion_host: str
    url: str
    title: str | None
    snippet: str
    score: float
    last_seen_at: str | None


def search_pages(connection: sqlite3.Connection, query: str, *, limit: int = 20) -> list[SearchHit]:
    if not query.strip():
        return []
    rows = connection.execute(
        """
        SELECT p.id AS page_id, s.onion_host, p.canonical_url, p.current_title,
               snippet(pages_fts, 4, '[', ']', '…', 18) AS snippet,
               bm25(pages_fts, 8.0, 3.0, 4.0, 1.0) AS score,
               p.last_seen_at
        FROM pages_fts
        JOIN pages p ON p.id = CAST(pages_fts.page_id AS INTEGER)
        JOIN services s ON s.id = p.service_id
        WHERE pages_fts MATCH ?
        ORDER BY score ASC
        LIMIT ?
        """,
        (query, max(1, min(limit, 100))),
    ).fetchall()
    return [SearchHit(int(row["page_id"]), row["onion_host"], row["canonical_url"], row["current_title"], row["snippet"], float(row["score"]), row["last_seen_at"]) for row in rows]
