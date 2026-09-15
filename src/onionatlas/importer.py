from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import timedelta

from onionatlas.domain.results import CrawlResult
from onionatlas.domain.urls import canonicalize_onion_url
from onionatlas.frontier import enqueue_url
from onionatlas.storage.repositories import ensure_service
from onionatlas.timeutil import to_iso, utcnow


class ImportRejected(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    attempt_id: str
    duplicate: bool
    fetch_id: int | None = None
    page_id: int | None = None
    new_services: int = 0
    new_links: int = 0


def _frontier_id(task_id: str) -> int:
    prefix, sep, value = task_id.partition(":")
    if prefix != "frontier" or not sep:
        raise ImportRejected("unsupported task_id")
    try:
        return int(value)
    except ValueError as exc:
        raise ImportRejected("invalid frontier task id") from exc


def _retry_delay(attempt_count: int) -> timedelta:
    seconds = min(7 * 24 * 3600, 300 * (2 ** max(0, min(attempt_count - 1, 12))))
    return timedelta(seconds=seconds)


def _error_status(error_class: str) -> str:
    if error_class in {"connect_timeout", "read_timeout", "total_timeout"}:
        return "timeout"
    if error_class in {"invalid_target"}:
        return "invalid"
    if error_class in {"disallowed_content_type", "invalid_redirect", "response_too_large"}:
        return "blocked_by_policy"
    return "offline"


def _insert_fetch_failure(connection: sqlite3.Connection, *, result: CrawlResult, service_id: int) -> int:
    assert result.error is not None
    cursor = connection.execute(
        """
        INSERT INTO fetches(
            service_id, task_id, attempt_id, lease_id, worker_id, requested_url,
            started_at, finished_at, success, error_class, error_message, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
        """,
        (service_id, result.task_id, result.attempt_id, result.lease_id, result.worker_id,
         result.request_url, result.started_at, result.finished_at, result.error.error_class,
         result.error.message[:500], to_iso(utcnow())),
    )
    return int(cursor.lastrowid)


def _ensure_page(connection: sqlite3.Connection, *, service_id: int, final_url: str, now: str) -> tuple[int, bool, str | None]:
    canonical = canonicalize_onion_url(final_url)
    row = connection.execute("SELECT id, current_text_hash FROM pages WHERE canonical_url = ?", (canonical.url,)).fetchone()
    if row is not None:
        return int(row["id"]), False, row["current_text_hash"]
    cursor = connection.execute(
        """
        INSERT INTO pages(service_id, canonical_url, path, query, first_seen_at, is_homepage, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (service_id, canonical.url, canonical.path, canonical.query, now, int(canonical.is_homepage), now, now),
    )
    return int(cursor.lastrowid), True, None


def _sync_fts(connection: sqlite3.Connection, *, page_id: int, title: str | None, description: str | None, h1: str | None, body: str) -> None:
    connection.execute("DELETE FROM pages_fts WHERE page_id = ?", (page_id,))
    connection.execute("INSERT INTO pages_fts(page_id, title, description, h1, body) VALUES (?, ?, ?, ?, ?)", (page_id, title or "", description or "", h1 or "", body))


def _insert_revision_if_changed(connection: sqlite3.Connection, *, page_id: int, previous_hash: str | None, result: CrawlResult, observed_at: str) -> None:
    assert result.document is not None
    if previous_hash == result.document.text_hash:
        return
    previous = connection.execute("SELECT id FROM page_revisions WHERE page_id = ? ORDER BY id DESC LIMIT 1", (page_id,)).fetchone()
    connection.execute(
        """
        INSERT INTO page_revisions(page_id, observed_at, text_hash, title, description, h1, normalized_text, supersedes_revision_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (page_id, observed_at, result.document.text_hash, result.document.title, result.document.description,
         result.document.h1, result.document.normalized_text, previous["id"] if previous else None),
    )


def import_result(connection: sqlite3.Connection, result: CrawlResult, *, max_depth: int = 3, max_text_bytes: int = 1 * 1024 * 1024, max_links: int = 500) -> ImportOutcome:
    if result.document is not None and len(result.document.normalized_text.encode("utf-8")) > max_text_bytes:
        raise ImportRejected("normalized text exceeds import limit")
    if len(result.links) > max_links:
        raise ImportRejected("link count exceeds import limit")
    existing = connection.execute("SELECT id, page_id FROM fetches WHERE attempt_id = ?", (result.attempt_id,)).fetchone()
    if existing is not None:
        return ImportOutcome(result.attempt_id, True, int(existing["id"]), int(existing["page_id"]) if existing["page_id"] is not None else None)

    frontier_id = _frontier_id(result.task_id)
    connection.execute("BEGIN IMMEDIATE")
    try:
        existing = connection.execute("SELECT id, page_id FROM fetches WHERE attempt_id = ?", (result.attempt_id,)).fetchone()
        if existing is not None:
            connection.execute("COMMIT")
            return ImportOutcome(result.attempt_id, True, int(existing["id"]), int(existing["page_id"]) if existing["page_id"] is not None else None)
        row = connection.execute("SELECT * FROM frontier WHERE id = ?", (frontier_id,)).fetchone()
        if row is None:
            raise ImportRejected("frontier task not found")
        if row["attempt_id"] != result.attempt_id or row["lease_id"] != result.lease_id:
            raise ImportRejected("stale or mismatched lease")
        if canonicalize_onion_url(result.request_url).url != row["target_url"]:
            raise ImportRejected("result target does not match leased target")

        now_dt = utcnow()
        now = to_iso(now_dt) or ""
        task_service_id = int(row["service_id"])
        if not result.success:
            if result.error is None:
                raise ImportRejected("failed result missing error")
            fetch_id = _insert_fetch_failure(connection, result=result, service_id=task_service_id)
            status = _error_status(result.error.error_class)
            next_attempt = to_iso(now_dt + _retry_delay(int(row["attempt_count"])))
            connection.execute(
                """UPDATE services SET last_seen_at = ?, last_offline_at = ?, current_status = ?,
                   crawl_count = crawl_count + 1, failure_count = failure_count + 1,
                   consecutive_failures = consecutive_failures + 1, next_recrawl_at = ?, updated_at = ? WHERE id = ?""",
                (now, now, status, next_attempt, now, task_service_id),
            )
            connection.execute(
                """UPDATE frontier SET state = 'retry', next_attempt_at = ?, last_error_class = ?,
                   lease_owner = NULL, lease_id = NULL, attempt_id = NULL, leased_at = NULL,
                   lease_expires_at = NULL, updated_at = ? WHERE id = ?""",
                (next_attempt, result.error.error_class, now, frontier_id),
            )
            connection.execute("COMMIT")
            return ImportOutcome(result.attempt_id, False, fetch_id=fetch_id)

        if result.response is None or result.document is None:
            raise ImportRejected("successful result missing response/document")
        final = canonicalize_onion_url(result.response.final_url)
        final_service_id, final_service_created = ensure_service(connection, final.onion_host)
        page_id, _page_created, previous_hash = _ensure_page(connection, service_id=final_service_id, final_url=final.url, now=now)
        _insert_revision_if_changed(connection, page_id=page_id, previous_hash=previous_hash, result=result, observed_at=result.finished_at)
        connection.execute(
            """UPDATE pages SET last_seen_at = ?, last_fetch_at = ?, current_status_code = ?, current_content_type = ?,
               current_title = ?, current_description = ?, current_h1 = ?, current_text = ?, current_text_hash = ?,
               current_body_bytes = ?, current_requires_javascript = ?, updated_at = ? WHERE id = ?""",
            (now, result.finished_at, result.response.status_code, result.response.content_type, result.document.title,
             result.document.description, result.document.h1, result.document.normalized_text, result.document.text_hash,
             result.response.body_bytes, int(result.document.requires_javascript), now, page_id),
        )
        fetch_cursor = connection.execute(
            """INSERT INTO fetches(page_id, service_id, task_id, attempt_id, lease_id, worker_id, requested_url,
               final_url, started_at, finished_at, status_code, content_type, charset, body_bytes,
               normalized_text_bytes, elapsed_ms, redirect_count, content_hash, text_hash, success, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (page_id, task_service_id, result.task_id, result.attempt_id, result.lease_id, result.worker_id,
             result.request_url, final.url, result.started_at, result.finished_at, result.response.status_code,
             result.response.content_type, result.response.charset, result.response.body_bytes,
             result.response.normalized_text_bytes, result.response.elapsed_ms, len(result.response.redirect_chain),
             result.document.content_hash, result.document.text_hash, now),
        )
        fetch_id = int(fetch_cursor.lastrowid)
        _sync_fts(connection, page_id=page_id, title=result.document.title, description=result.document.description, h1=result.document.h1, body=result.document.normalized_text)

        new_services = int(final_service_created)
        new_links = 0
        for link in result.links:
            try:
                canonical_link = canonicalize_onion_url(link.url)
            except ValueError:
                continue
            target_service_id, target_created = ensure_service(connection, canonical_link.onion_host)
            new_services += int(target_created)
            if int(row["depth"]) < max_depth:
                evidence_cursor = connection.execute(
                    """INSERT INTO service_evidence(service_id, evidence_type, source_url, source_service_id, anchor_text, observed_at, is_first_discovery)
                       VALUES (?, 'onion_link', ?, ?, ?, ?, ?)""",
                    (target_service_id, final.url, final_service_id, link.anchor_text, now, int(target_created)),
                )
                enqueue_url(connection, canonical_link.url, depth=int(row["depth"]) + 1, priority=max(-100.0, float(row["priority"]) - 1.0), source_evidence_id=int(evidence_cursor.lastrowid))
            existing_link = connection.execute("SELECT id FROM links WHERE source_page_id = ? AND target_url = ?", (page_id, canonical_link.url)).fetchone()
            if existing_link is None:
                connection.execute(
                    """INSERT INTO links(source_page_id, target_url, target_service_id, anchor_text, first_seen_at, last_seen_at, times_seen, last_fetch_id)
                       VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
                    (page_id, canonical_link.url, target_service_id, link.anchor_text, now, now, fetch_id),
                )
                new_links += 1
            else:
                connection.execute(
                    """UPDATE links SET target_service_id = ?, anchor_text = COALESCE(?, anchor_text), last_seen_at = ?,
                       times_seen = times_seen + 1, last_fetch_id = ? WHERE id = ?""",
                    (target_service_id, link.anchor_text, now, fetch_id, existing_link["id"]),
                )

        next_recrawl = to_iso(now_dt + timedelta(hours=24))
        connection.execute(
            """UPDATE services SET last_seen_at = ?, last_online_at = ?, current_status = 'online', current_title = ?,
               current_description = ?, current_content_hash = ?, current_homepage_page_id = CASE WHEN ? THEN ? ELSE current_homepage_page_id END,
               crawl_count = crawl_count + 1, success_count = success_count + 1, consecutive_failures = 0,
               next_recrawl_at = ?, updated_at = ? WHERE id = ?""",
            (now, now, result.document.title, result.document.description, result.document.content_hash, int(final.is_homepage), page_id, next_recrawl, now, task_service_id),
        )
        if final_service_id != task_service_id:
            connection.execute(
                """UPDATE services SET last_seen_at = ?, last_online_at = ?, current_status = 'online', current_title = ?,
                   current_description = ?, current_content_hash = ?, current_homepage_page_id = CASE WHEN ? THEN ? ELSE current_homepage_page_id END,
                   next_recrawl_at = ?, updated_at = ? WHERE id = ?""",
                (now, now, result.document.title, result.document.description, result.document.content_hash, int(final.is_homepage), page_id, next_recrawl, now, final_service_id),
            )
        connection.execute(
            """UPDATE frontier SET state = 'done', next_attempt_at = NULL, last_error_class = NULL,
               lease_owner = NULL, lease_id = NULL, attempt_id = NULL, leased_at = NULL, lease_expires_at = NULL,
               updated_at = ? WHERE id = ?""",
            (now, frontier_id),
        )
        connection.execute("COMMIT")
        return ImportOutcome(result.attempt_id, False, fetch_id=fetch_id, page_id=page_id, new_services=new_services, new_links=new_links)
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
