from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import timedelta

from onionatlas.discovery.sources import (
    HttpRegexConfig,
    _validate_source_url,
    fetch_http_candidates,
)
from onionatlas.domain.urls import InvalidOnionURL, canonicalize_onion_url
from onionatlas.frontier import enqueue_url
from onionatlas.storage.repositories import ensure_service, get_service
from onionatlas.timeutil import to_iso, utcnow


@dataclass(frozen=True, slots=True)
class DiscoveryOutcome:
    run_id: int
    raw: int
    valid: int
    unique: int
    new: int
    known: int
    rejected: int
    novelty_rate: float


def ensure_http_source(
    connection: sqlite3.Connection,
    *,
    name: str,
    url_template: str,
    queries: list[str] | tuple[str, ...] = ("",),
    interval_seconds: int = 6 * 3600,
    max_candidates: int = 5000,
) -> int:
    _validate_source_url(url_template.replace("{query}", "probe"))
    now = to_iso(utcnow()) or ""
    config = json.dumps(
        {
            "url_template": url_template,
            "queries": list(queries) or [""],
            "interval_seconds": max(300, interval_seconds),
            "max_bytes": 2 * 1024 * 1024,
            "max_candidates": max(1, min(max_candidates, 50000)),
        },
        separators=(",", ":"),
    )
    connection.execute(
        """
        INSERT INTO discovery_sources(
            name, type, enabled, config_json, next_run_at, state, created_at, updated_at
        ) VALUES (?, 'http_regex', 1, ?, ?, 'active', ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            type = 'http_regex',
            config_json = excluded.config_json,
            enabled = 1,
            updated_at = excluded.updated_at
        """,
        (name, config, now, now, now),
    )
    return int(
        connection.execute(
            "SELECT id FROM discovery_sources WHERE name = ?", (name,)
        ).fetchone()[0]
    )


def process_candidates(
    connection: sqlite3.Connection,
    *,
    source_id: int,
    candidates: list[str],
    query_text: str | None = None,
    topic: str | None = None,
    interval_seconds: int = 6 * 3600,
) -> DiscoveryOutcome:
    started_dt = utcnow()
    started = to_iso(started_dt) or ""
    connection.execute("BEGIN IMMEDIATE")
    try:
        run_cursor = connection.execute(
            """
            INSERT INTO discovery_runs(source_id, started_at, query_text, topic)
            VALUES (?, ?, ?, ?)
            """,
            (source_id, started, query_text, topic),
        )
        run_id = int(run_cursor.lastrowid)
        raw_count = len(candidates)
        valid_count = 0
        rejected = 0
        new_count = 0
        known_count = 0
        unique_urls: dict[str, str] = {}

        for raw in candidates:
            try:
                canonical = canonicalize_onion_url(raw)
            except InvalidOnionURL as exc:
                rejected += 1
                connection.execute(
                    """
                    INSERT INTO discovery_candidates(
                        run_id, raw_value, is_valid, is_new, reject_reason
                    ) VALUES (?, ?, 0, 0, ?)
                    """,
                    (run_id, raw[:2048], str(exc)[:256]),
                )
                continue
            valid_count += 1
            unique_urls.setdefault(canonical.url, raw)

        for canonical_url, raw in unique_urls.items():
            canonical = canonicalize_onion_url(canonical_url)
            existed = get_service(connection, canonical.onion_host) is not None
            service_id, created = ensure_service(connection, canonical.onion_host)
            now = to_iso(utcnow()) or ""
            evidence_cursor = connection.execute(
                """
                INSERT INTO service_evidence(
                    service_id, evidence_type, discovery_source_id, discovery_run_id,
                    query_text, observed_at, is_first_discovery
                ) VALUES (?, 'external_index', ?, ?, ?, ?, ?)
                """,
                (service_id, source_id, run_id, query_text, now, int(created)),
            )
            enqueue_url(
                connection,
                canonical.url,
                depth=0,
                priority=25.0,
                source_evidence_id=int(evidence_cursor.lastrowid),
            )
            is_new = created and not existed
            if is_new:
                new_count += 1
            else:
                known_count += 1
            connection.execute(
                """
                INSERT INTO discovery_candidates(
                    run_id, raw_value, canonical_url, service_id, is_valid, is_new
                ) VALUES (?, ?, ?, ?, 1, ?)
                """,
                (run_id, raw[:2048], canonical.url, service_id, int(is_new)),
            )

        unique_count = len(unique_urls)
        novelty = (new_count / unique_count) if unique_count else 0.0
        finished_dt = utcnow()
        elapsed_ms = int((finished_dt - started_dt).total_seconds() * 1000)
        connection.execute(
            """
            UPDATE discovery_runs
            SET finished_at = ?, raw_candidates = ?, valid_candidates = ?,
                unique_candidates = ?, new_services = ?, known_services = ?,
                rejected_candidates = ?, novelty_rate = ?, success = 1, elapsed_ms = ?
            WHERE id = ?
            """,
            (
                to_iso(finished_dt),
                raw_count,
                valid_count,
                unique_count,
                new_count,
                known_count,
                rejected,
                novelty,
                elapsed_ms,
                run_id,
            ),
        )

        source = connection.execute(
            "SELECT low_novelty_streak FROM discovery_sources WHERE id = ?", (source_id,)
        ).fetchone()
        streak = int(source["low_novelty_streak"] if source else 0)
        if unique_count >= 10 and novelty < 0.05:
            streak += 1
        else:
            streak = 0
        cooldown_seconds = 24 * 3600 if streak >= 2 else max(300, interval_seconds)
        state = "cooling" if streak >= 2 else "active"
        next_run = to_iso(finished_dt + timedelta(seconds=cooldown_seconds))
        connection.execute(
            """
            UPDATE discovery_sources
            SET last_run_at = ?, next_run_at = ?, cooldown_until = ?, state = ?,
                low_novelty_streak = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                to_iso(finished_dt),
                next_run,
                next_run if state == "cooling" else None,
                state,
                streak,
                to_iso(finished_dt),
                source_id,
            ),
        )
        connection.execute("COMMIT")
        return DiscoveryOutcome(
            run_id,
            raw_count,
            valid_count,
            unique_count,
            new_count,
            known_count,
            rejected,
            novelty,
        )
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


async def run_due_sources(
    connection: sqlite3.Connection,
    *,
    limit: int = 5,
) -> list[DiscoveryOutcome]:
    now = to_iso(utcnow()) or ""
    rows = connection.execute(
        """
        SELECT *
        FROM discovery_sources
        WHERE enabled = 1
          AND type = 'http_regex'
          AND (next_run_at IS NULL OR next_run_at <= ?)
          AND (cooldown_until IS NULL OR cooldown_until <= ?)
        ORDER BY COALESCE(next_run_at, created_at) ASC
        LIMIT ?
        """,
        (now, now, max(1, limit)),
    ).fetchall()

    outcomes: list[DiscoveryOutcome] = []
    for row in rows:
        config_data = json.loads(row["config_json"] or "{}")
        config = HttpRegexConfig(
            url_template=str(config_data["url_template"]),
            queries=tuple(config_data.get("queries") or [""]),
            max_bytes=int(config_data.get("max_bytes", 2 * 1024 * 1024)),
            max_candidates=int(config_data.get("max_candidates", 5000)),
        )
        interval = int(config_data.get("interval_seconds", 6 * 3600))
        for query in config.queries:
            query_started = time.monotonic()
            try:
                candidates = await fetch_http_candidates(config, query=query)
                outcomes.append(
                    process_candidates(
                        connection,
                        source_id=int(row["id"]),
                        candidates=candidates,
                        query_text=query or None,
                        interval_seconds=interval,
                    )
                )
            except Exception as exc:
                finished = utcnow()
                connection.execute(
                    """
                    UPDATE discovery_sources
                    SET last_run_at = ?, next_run_at = ?, state = 'error', updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        to_iso(finished),
                        to_iso(finished + timedelta(minutes=30)),
                        to_iso(finished),
                        row["id"],
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO discovery_runs(
                        source_id, started_at, finished_at, query_text, success,
                        error_class, error_message, elapsed_ms
                    ) VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                    """,
                    (
                        row["id"],
                        now,
                        to_iso(finished),
                        query or None,
                        type(exc).__name__,
                        str(exc)[:500],
                        int((time.monotonic() - query_started) * 1000),
                    ),
                )
                break
    return outcomes
