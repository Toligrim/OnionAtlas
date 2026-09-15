from __future__ import annotations

import sqlite3

from onionatlas.domain.results import CrawlResult, DocumentResult, ErrorResult, LinkResult, ResponseResult
from onionatlas.frontier import enqueue_url, lease_tasks
from onionatlas.graph import service_neighbors
from onionatlas.importer import import_result
from onionatlas.search import search_pages
from onionatlas.timeutil import to_iso, utcnow
from tests.helpers import onion_host

HOST_A = onion_host(1)
HOST_B = onion_host(2)


def success_result(task, *, text: str = "privacy hosting", links=()) -> CrawlResult:
    now = to_iso(utcnow()) or ""
    return CrawlResult(task_id=task.task_id, attempt_id=task.attempt_id, lease_id=task.lease_id, worker_id="w1", started_at=now, finished_at=now, success=True, request_url=task.target_url,
        response=ResponseResult(final_url=task.target_url, status_code=200, content_type="text/html", charset="utf-8", body_bytes=len(text), normalized_text_bytes=len(text), elapsed_ms=10),
        document=DocumentResult(title="Example privacy service", description="desc", h1="Welcome", normalized_text=text, content_hash="sha256:body-" + text, text_hash="sha256:text-" + text), links=tuple(links))


def test_import_success_indexes_and_discovers_links(db: sqlite3.Connection) -> None:
    enqueue_url(db, HOST_A, priority=100); task = lease_tasks(db, worker_id="w1", limit=1)[0]
    outcome = import_result(db, success_result(task, links=[LinkResult(f"http://{HOST_B}/", "other")]))
    assert not outcome.duplicate and outcome.new_services == 1 and outcome.new_links == 1
    states = {r["target_url"]: r["state"] for r in db.execute("SELECT target_url, state FROM frontier")}
    assert states[f"http://{HOST_A}/"] == "done" and states[f"http://{HOST_B}/"] == "queued"
    hits = search_pages(db, "privacy"); assert len(hits) == 1 and hits[0].onion_host == HOST_A
    assert [(n.onion_host, n.direction) for n in service_neighbors(db, HOST_A)] == [(HOST_B, "outgoing")]


def test_import_is_idempotent(db: sqlite3.Connection) -> None:
    enqueue_url(db, HOST_A); task = lease_tasks(db, worker_id="w1", limit=1)[0]; result = success_result(task)
    assert import_result(db, result).duplicate is False; assert import_result(db, result).duplicate is True
    assert db.execute("SELECT COUNT(*) FROM fetches").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM page_revisions").fetchone()[0] == 1


def test_changed_text_creates_revision_on_recrawl(db: sqlite3.Connection) -> None:
    enqueue_url(db, HOST_A); task1 = lease_tasks(db, worker_id="w1", limit=1)[0]; import_result(db, success_result(task1, text="version one"))
    enqueue_url(db, HOST_A, requeue_existing=True); task2 = lease_tasks(db, worker_id="w1", limit=1)[0]; import_result(db, success_result(task2, text="version two"))
    assert db.execute("SELECT COUNT(*) FROM page_revisions").fetchone()[0] == 2
    assert search_pages(db, '"version two"') and not search_pages(db, '"version one"')


def test_failure_schedules_retry(db: sqlite3.Connection) -> None:
    enqueue_url(db, HOST_A); task = lease_tasks(db, worker_id="w1", limit=1)[0]; now = to_iso(utcnow()) or ""
    result = CrawlResult(task_id=task.task_id, attempt_id=task.attempt_id, lease_id=task.lease_id, worker_id="w1", started_at=now, finished_at=now, success=False, request_url=task.target_url, error=ErrorResult("connect_timeout", "timeout"))
    import_result(db, result); row = db.execute("SELECT state, next_attempt_at FROM frontier").fetchone(); assert row["state"] == "retry" and row["next_attempt_at"] is not None
    service = db.execute("SELECT current_status, failure_count FROM services").fetchone(); assert service["current_status"] == "timeout" and service["failure_count"] == 1


def test_max_depth_stores_edge_but_does_not_expand_frontier(db: sqlite3.Connection) -> None:
    enqueue_url(db, HOST_A); task = lease_tasks(db, worker_id="w1", limit=1)[0]
    import_result(db, success_result(task, links=[LinkResult(f"http://{HOST_B}/", "other")]), max_depth=0)
    assert db.execute("SELECT COUNT(*) FROM links").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM services").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM frontier").fetchone()[0] == 1
