from __future__ import annotations

import sqlite3
from collections.abc import Iterable


MIGRATIONS: tuple[tuple[int, str, str], ...] = (
    (
        1,
        "initial_schema",
        r"""
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY,
            onion_host TEXT NOT NULL UNIQUE,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT,
            last_online_at TEXT,
            last_offline_at TEXT,
            current_status TEXT NOT NULL DEFAULT 'unknown',
            current_title TEXT,
            current_description TEXT,
            current_homepage_page_id INTEGER,
            current_content_hash TEXT,
            crawl_count INTEGER NOT NULL DEFAULT 0,
            success_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            next_recrawl_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(current_homepage_page_id) REFERENCES pages(id) DEFERRABLE INITIALLY DEFERRED
        );

        CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY,
            service_id INTEGER NOT NULL,
            canonical_url TEXT NOT NULL UNIQUE,
            path TEXT,
            query TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT,
            last_fetch_at TEXT,
            current_status_code INTEGER,
            current_content_type TEXT,
            current_title TEXT,
            current_description TEXT,
            current_h1 TEXT,
            current_text TEXT,
            current_text_hash TEXT,
            current_body_bytes INTEGER,
            current_requires_javascript INTEGER NOT NULL DEFAULT 0,
            is_homepage INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(service_id) REFERENCES services(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_pages_service ON pages(service_id);

        CREATE TABLE IF NOT EXISTS page_revisions (
            id INTEGER PRIMARY KEY,
            page_id INTEGER NOT NULL,
            observed_at TEXT NOT NULL,
            text_hash TEXT NOT NULL,
            title TEXT,
            description TEXT,
            h1 TEXT,
            normalized_text TEXT NOT NULL,
            supersedes_revision_id INTEGER,
            FOREIGN KEY(page_id) REFERENCES pages(id) ON DELETE CASCADE,
            FOREIGN KEY(supersedes_revision_id) REFERENCES page_revisions(id)
        );

        CREATE TABLE IF NOT EXISTS fetches (
            id INTEGER PRIMARY KEY,
            page_id INTEGER,
            service_id INTEGER NOT NULL,
            task_id TEXT NOT NULL,
            attempt_id TEXT NOT NULL UNIQUE,
            lease_id TEXT,
            worker_id TEXT,
            requested_url TEXT NOT NULL,
            final_url TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status_code INTEGER,
            content_type TEXT,
            charset TEXT,
            body_bytes INTEGER,
            normalized_text_bytes INTEGER,
            elapsed_ms INTEGER,
            redirect_count INTEGER NOT NULL DEFAULT 0,
            content_hash TEXT,
            text_hash TEXT,
            success INTEGER NOT NULL,
            error_class TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(page_id) REFERENCES pages(id),
            FOREIGN KEY(service_id) REFERENCES services(id)
        );

        CREATE INDEX IF NOT EXISTS idx_fetches_service_created
            ON fetches(service_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_fetches_page_created
            ON fetches(page_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY,
            source_page_id INTEGER NOT NULL,
            target_url TEXT NOT NULL,
            target_service_id INTEGER,
            anchor_text TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            times_seen INTEGER NOT NULL DEFAULT 1,
            last_fetch_id INTEGER,
            UNIQUE(source_page_id, target_url),
            FOREIGN KEY(source_page_id) REFERENCES pages(id) ON DELETE CASCADE,
            FOREIGN KEY(target_service_id) REFERENCES services(id),
            FOREIGN KEY(last_fetch_id) REFERENCES fetches(id)
        );

        CREATE INDEX IF NOT EXISTS idx_links_target_service ON links(target_service_id);

        CREATE TABLE IF NOT EXISTS service_evidence (
            id INTEGER PRIMARY KEY,
            service_id INTEGER NOT NULL,
            evidence_type TEXT NOT NULL,
            source_url TEXT,
            source_service_id INTEGER,
            discovery_source_id INTEGER,
            discovery_run_id INTEGER,
            query_text TEXT,
            anchor_text TEXT,
            snippet TEXT,
            observed_at TEXT NOT NULL,
            is_first_discovery INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT,
            FOREIGN KEY(service_id) REFERENCES services(id) ON DELETE CASCADE,
            FOREIGN KEY(source_service_id) REFERENCES services(id)
        );

        CREATE INDEX IF NOT EXISTS idx_service_evidence_service
            ON service_evidence(service_id, observed_at DESC);

        CREATE TABLE IF NOT EXISTS frontier (
            id INTEGER PRIMARY KEY,
            target_url TEXT NOT NULL UNIQUE,
            service_id INTEGER NOT NULL,
            state TEXT NOT NULL,
            priority REAL NOT NULL DEFAULT 0,
            depth INTEGER NOT NULL DEFAULT 0,
            first_discovered_at TEXT NOT NULL,
            last_enqueued_at TEXT NOT NULL,
            next_attempt_at TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            lease_owner TEXT,
            lease_id TEXT,
            attempt_id TEXT,
            leased_at TEXT,
            lease_expires_at TEXT,
            last_error_class TEXT,
            source_evidence_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(service_id) REFERENCES services(id),
            FOREIGN KEY(source_evidence_id) REFERENCES service_evidence(id)
        );

        CREATE INDEX IF NOT EXISTS idx_frontier_due
            ON frontier(state, next_attempt_at, priority DESC, id);
        CREATE INDEX IF NOT EXISTS idx_frontier_lease_expiry ON frontier(lease_expires_at);
        CREATE INDEX IF NOT EXISTS idx_frontier_service ON frontier(service_id);

        CREATE TABLE IF NOT EXISTS workers (
            id TEXT PRIMARY KEY,
            name TEXT,
            registered_at TEXT NOT NULL,
            last_heartbeat_at TEXT NOT NULL,
            status TEXT NOT NULL,
            version TEXT,
            capabilities_json TEXT,
            current_leases INTEGER NOT NULL DEFAULT 0,
            max_concurrency INTEGER NOT NULL DEFAULT 1,
            metadata_json TEXT
        );

        CREATE TABLE IF NOT EXISTS discovery_sources (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            config_json TEXT,
            last_run_at TEXT,
            next_run_at TEXT,
            cooldown_until TEXT,
            state TEXT NOT NULL DEFAULT 'active',
            low_novelty_streak INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS discovery_runs (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            query_text TEXT,
            topic TEXT,
            raw_candidates INTEGER NOT NULL DEFAULT 0,
            valid_candidates INTEGER NOT NULL DEFAULT 0,
            unique_candidates INTEGER NOT NULL DEFAULT 0,
            new_services INTEGER NOT NULL DEFAULT 0,
            known_services INTEGER NOT NULL DEFAULT 0,
            rejected_candidates INTEGER NOT NULL DEFAULT 0,
            novelty_rate REAL,
            success INTEGER,
            error_class TEXT,
            error_message TEXT,
            elapsed_ms INTEGER,
            FOREIGN KEY(source_id) REFERENCES discovery_sources(id)
        );

        CREATE TABLE IF NOT EXISTS discovery_candidates (
            id INTEGER PRIMARY KEY,
            run_id INTEGER NOT NULL,
            raw_value TEXT NOT NULL,
            canonical_url TEXT,
            service_id INTEGER,
            is_valid INTEGER NOT NULL,
            is_new INTEGER NOT NULL DEFAULT 0,
            reject_reason TEXT,
            metadata_json TEXT,
            FOREIGN KEY(run_id) REFERENCES discovery_runs(id) ON DELETE CASCADE,
            FOREIGN KEY(service_id) REFERENCES services(id)
        );

        CREATE TABLE IF NOT EXISTS crawl_runs (
            id INTEGER PRIMARY KEY,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            mode TEXT,
            worker_count INTEGER NOT NULL DEFAULT 0,
            scheduled_targets INTEGER NOT NULL DEFAULT 0,
            successful_fetches INTEGER NOT NULL DEFAULT 0,
            failed_fetches INTEGER NOT NULL DEFAULT 0,
            new_services INTEGER NOT NULL DEFAULT 0,
            new_pages INTEGER NOT NULL DEFAULT 0,
            new_links INTEGER NOT NULL DEFAULT 0
        );
        """,
    ),
    (
        2,
        "fts5_pages",
        r"""
        CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
            page_id UNINDEXED,
            title,
            description,
            h1,
            body,
            tokenize = 'unicode61'
        );
        """,
    ),
)


def _iter_migrations() -> Iterable[tuple[int, str, str]]:
    return MIGRATIONS


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    applied = {
        row["version"]
        for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")
    }
    for version, name, sql in _iter_migrations():
        if version in applied:
            continue
        escaped_name = name.replace("'", "''")
        script = (
            "BEGIN IMMEDIATE;\n"
            + sql
            + f"\nINSERT OR IGNORE INTO schema_migrations(version, name) VALUES ({version}, '{escaped_name}');\n"
            + "COMMIT;"
        )
        try:
            connection.executescript(script)
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
