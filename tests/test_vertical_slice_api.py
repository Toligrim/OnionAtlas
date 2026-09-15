from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from onionatlas.api import create_app
from onionatlas.config import Settings
from onionatlas.domain.models import CrawlTask
from onionatlas.domain.results import CrawlResult, DocumentResult, LinkResult, ResponseResult
from onionatlas.frontier import enqueue_url
from onionatlas.search import search_pages
from onionatlas.storage import connect, migrate
from onionatlas.timeutil import to_iso, utcnow
from tests.helpers import onion_host


HOST_A = onion_host(101)
HOST_B = onion_host(102)


def _result_for(task: CrawlTask) -> CrawlResult:
    now = to_iso(utcnow()) or ""
    text = "OnionAtlas vertical slice searchable phrase"
    return CrawlResult(
        task_id=task.task_id,
        attempt_id=task.attempt_id,
        lease_id=task.lease_id,
        worker_id="worker-e2e",
        started_at=now,
        finished_at=now,
        success=True,
        request_url=task.target_url,
        response=ResponseResult(
            final_url=task.target_url,
            status_code=200,
            content_type="text/html",
            charset="utf-8",
            body_bytes=len(text.encode("utf-8")),
            normalized_text_bytes=len(text.encode("utf-8")),
            elapsed_ms=25,
        ),
        document=DocumentResult(
            title="Vertical Slice",
            description="Integration fixture",
            h1="OnionAtlas",
            normalized_text=text,
            content_hash="sha256:vertical-body",
            text_hash="sha256:vertical-text",
        ),
        links=(LinkResult(f"http://{HOST_B}/", "discovered service"),),
    )


def test_api_vertical_slice_closes_discovery_loop(tmp_path: Path) -> None:
    db_path = tmp_path / "vertical.sqlite3"
    connection = connect(db_path)
    migrate(connection)
    enqueue_url(connection, HOST_A, priority=100)
    connection.close()

    settings = Settings(
        database_path=db_path,
        worker_token="integration-secret",
        worker_batch_size=2,
        crawl_max_depth=3,
    )
    client = TestClient(create_app(settings))
    headers = {"Authorization": "Bearer integration-secret"}

    heartbeat = client.post(
        "/v1/worker/heartbeat",
        headers=headers,
        json={
            "worker_id": "worker-e2e",
            "version": "test",
            "status": "ready",
            "max_concurrency": 2,
            "active_tasks": 0,
            "tor_ready": True,
        },
    )
    assert heartbeat.status_code == 200

    lease = client.post(
        "/v1/worker/lease",
        headers=headers,
        json={"worker_id": "worker-e2e", "limit": 1},
    )
    assert lease.status_code == 200
    tasks = lease.json()["tasks"]
    assert len(tasks) == 1
    task = CrawlTask.from_dict(tasks[0])
    assert task.target_url == f"http://{HOST_A}/"

    result = _result_for(task)
    delivered = client.post(
        "/v1/worker/results",
        headers=headers,
        json={"result": result.to_dict()},
    )
    assert delivered.status_code == 200
    assert delivered.json()["outcome"]["duplicate"] is False

    duplicate = client.post(
        "/v1/worker/results",
        headers=headers,
        json={"result": result.to_dict()},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["outcome"]["duplicate"] is True

    connection = connect(db_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM fetches").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM links").fetchone()[0] == 1

        source_state = connection.execute(
            "SELECT state FROM frontier WHERE target_url = ?", (f"http://{HOST_A}/",)
        ).fetchone()[0]
        discovered_state = connection.execute(
            "SELECT state FROM frontier WHERE target_url = ?", (f"http://{HOST_B}/",)
        ).fetchone()[0]
        assert source_state == "done"
        assert discovered_state == "queued"

        hits = search_pages(connection, '"searchable phrase"')
        assert len(hits) == 1
        assert hits[0].onion_host == HOST_A
    finally:
        connection.close()
