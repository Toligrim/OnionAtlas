from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from onionatlas.config import Settings
from onionatlas.domain.results import CrawlResult, ErrorResult
from onionatlas.frontier import enqueue_url, lease_tasks
from onionatlas.importer import ImportRejected, import_result
from onionatlas.timeutil import to_iso, utcnow
from onionatlas.worker.client import probe_socks_listener, validate_control_url
from onionatlas.worker.spool import WorkerSpool
from tests.helpers import onion_host
from tests.test_importer import success_result

HOST_A = onion_host(1)
HOST_B = onion_host(2)


def test_import_rejects_result_from_non_owner(db) -> None:
    enqueue_url(db, HOST_A)
    task = lease_tasks(db, worker_id="owner", limit=1)[0]
    result = success_result(task)
    object.__setattr__(result, "worker_id", "other")
    with pytest.raises(ImportRejected, match="worker does not own lease"):
        import_result(db, result)


def test_cross_service_redirect_does_not_attach_foreign_homepage(db) -> None:
    enqueue_url(db, HOST_A)
    task = lease_tasks(db, worker_id="w1", limit=1)[0]
    result = success_result(task)
    assert result.response is not None
    object.__setattr__(result.response, "final_url", f"http://{HOST_B}/")
    import_result(db, result)

    source = db.execute(
        "SELECT current_homepage_page_id FROM services WHERE onion_host = ?", (HOST_A,)
    ).fetchone()
    target = db.execute(
        "SELECT current_homepage_page_id FROM services WHERE onion_host = ?", (HOST_B,)
    ).fetchone()
    assert source["current_homepage_page_id"] is None
    assert target["current_homepage_page_id"] is not None


def test_spool_quarantines_rejected_result(tmp_path: Path) -> None:
    spool = WorkerSpool(tmp_path, max_rejected=1)
    now = to_iso(utcnow()) or ""
    result = CrawlResult(
        task_id="frontier:1",
        attempt_id="attempt-1",
        lease_id="lease-1",
        worker_id="worker-1",
        started_at=now,
        finished_at=now,
        success=False,
        request_url=f"http://{HOST_A}/",
        error=ErrorResult("connect_timeout", "timeout"),
    )
    pending = spool.save(result)
    rejected = spool.reject(pending)
    assert not pending.exists()
    assert rejected.exists()
    assert spool.usage()[0] == 0


@pytest.mark.asyncio
async def test_socks_listener_probe() -> None:
    async def handler(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        assert await probe_socks_listener(f"socks5://127.0.0.1:{port}")
    finally:
        server.close()
        await server.wait_closed()


def test_control_url_requires_https_by_default() -> None:
    with pytest.raises(ValueError):
        validate_control_url(Settings(control_url="http://10.0.0.2:8080"))
    validate_control_url(Settings(control_url="http://127.0.0.1:8080"))
    validate_control_url(
        Settings(control_url="http://10.0.0.2:8080", worker_allow_insecure_control=True)
    )
    validate_control_url(Settings(control_url="https://control.example"))
