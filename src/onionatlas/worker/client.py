from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

from onionatlas import __version__
from onionatlas.config import Settings
from onionatlas.domain.models import CrawlTask
from onionatlas.worker.fetcher import fetch_task
from onionatlas.worker.spool import WorkerSpool

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WorkerCycle:
    leased: int
    fetched: int
    delivered: int
    pending: int


def _headers(settings: Settings) -> dict[str, str]:
    if not settings.worker_token:
        raise ValueError("ONIONATLAS_WORKER_TOKEN is required")
    return {"Authorization": f"Bearer {settings.worker_token}"}


async def _deliver_pending(client: httpx.AsyncClient, settings: Settings, spool: WorkerSpool) -> int:
    delivered = 0
    for path in spool.items():
        result = spool.load(path)
        response = await client.post("/v1/worker/results", json={"result": result.to_dict()}, headers=_headers(settings))
        if response.status_code == 409:
            spool.acknowledge(path)
            delivered += 1
            continue
        response.raise_for_status()
        spool.acknowledge(path)
        delivered += 1
    return delivered


async def run_worker_once(settings: Settings) -> WorkerCycle:
    if not settings.control_url:
        raise ValueError("ONIONATLAS_CONTROL_URL is required")
    spool = WorkerSpool(settings.worker_spool_dir)
    async with httpx.AsyncClient(base_url=settings.control_url, timeout=30.0) as control:
        status_value = "spool_full" if spool.is_full() else "ready"
        heartbeat = await control.post(
            "/v1/worker/heartbeat",
            json={"worker_id": settings.worker_id, "version": __version__, "status": status_value,
                  "max_concurrency": settings.worker_batch_size, "active_tasks": 0, "tor_ready": True},
            headers=_headers(settings),
        )
        heartbeat.raise_for_status()
        delivered = await _deliver_pending(control, settings, spool)
        if spool.is_full():
            count, _ = spool.usage()
            return WorkerCycle(0, 0, delivered, count)

        lease_response = await control.post(
            "/v1/worker/lease",
            json={"worker_id": settings.worker_id, "limit": settings.worker_batch_size},
            headers=_headers(settings),
        )
        lease_response.raise_for_status()
        tasks = [CrawlTask.from_dict(item) for item in lease_response.json().get("tasks", [])]
        semaphore = asyncio.Semaphore(max(1, settings.worker_batch_size))

        async def execute(task: CrawlTask):
            async with semaphore:
                result = await fetch_task(task, worker_id=settings.worker_id, proxy_url=settings.tor_socks_url)
                spool.save(result)
                return result

        if tasks:
            await asyncio.gather(*(execute(task) for task in tasks))
        delivered += await _deliver_pending(control, settings, spool)
        pending_after, _ = spool.usage()
        return WorkerCycle(len(tasks), len(tasks), delivered, pending_after)


async def run_worker_forever(settings: Settings) -> None:
    while True:
        try:
            await run_worker_once(settings)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            logger.warning("worker cycle failed: %s: %s", type(exc).__name__, str(exc)[:300])
        await asyncio.sleep(max(1.0, settings.worker_poll_seconds))
