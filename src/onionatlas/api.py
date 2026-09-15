from __future__ import annotations

import hmac
from dataclasses import asdict

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from onionatlas.config import Settings
from onionatlas.domain.models import FetchPolicy
from onionatlas.domain.results import CrawlResult
from onionatlas.frontier import lease_tasks
from onionatlas.importer import ImportRejected, import_result
from onionatlas.storage import connect, migrate
from onionatlas.timeutil import to_iso, utcnow


class HeartbeatRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)
    version: str = Field(default="unknown", max_length=64)
    status: str = Field(default="ready", max_length=32)
    max_concurrency: int = Field(default=1, ge=1, le=64)
    active_tasks: int = Field(default=0, ge=0, le=1024)
    tor_ready: bool = True


class LeaseRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)
    limit: int = Field(default=2, ge=1, le=20)


class ResultEnvelope(BaseModel):
    result: dict


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="OnionAtlas Control Plane", version="0.1.0")

    def require_worker_token(authorization: str | None = Header(default=None)) -> None:
        expected = settings.worker_token
        if not expected:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="worker API token is not configured")
        prefix = "Bearer "
        supplied = authorization[len(prefix):] if authorization and authorization.startswith(prefix) else ""
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid worker token")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        connection = connect(settings.database_path)
        try:
            migrate(connection)
            connection.execute("SELECT 1").fetchone()
        finally:
            connection.close()
        return {"status": "ok"}

    @app.post("/v1/worker/heartbeat", dependencies=[Depends(require_worker_token)])
    def heartbeat(payload: HeartbeatRequest) -> dict[str, str]:
        now = to_iso(utcnow()) or ""
        connection = connect(settings.database_path)
        try:
            migrate(connection)
            connection.execute(
                """
                INSERT INTO workers(id, registered_at, last_heartbeat_at, status, version, current_leases, max_concurrency, capabilities_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    last_heartbeat_at = excluded.last_heartbeat_at,
                    status = excluded.status,
                    version = excluded.version,
                    current_leases = excluded.current_leases,
                    max_concurrency = excluded.max_concurrency,
                    capabilities_json = excluded.capabilities_json
                """,
                (payload.worker_id, now, now, payload.status if payload.tor_ready else "tor_down", payload.version,
                 payload.active_tasks, payload.max_concurrency, '{"tor": true}' if payload.tor_ready else '{"tor": false}'),
            )
        finally:
            connection.close()
        return {"status": "ack"}

    @app.post("/v1/worker/lease", dependencies=[Depends(require_worker_token)])
    def lease(payload: LeaseRequest) -> dict:
        connection = connect(settings.database_path)
        try:
            migrate(connection)
            worker = connection.execute("SELECT status FROM workers WHERE id = ?", (payload.worker_id,)).fetchone()
            if worker is not None and worker["status"] in {"tor_down", "spool_full", "draining"}:
                return {"protocol_version": 1, "tasks": []}
            policy = FetchPolicy(
                connect_timeout_ms=int(settings.fetch_connect_timeout_seconds * 1000),
                total_timeout_ms=int(settings.fetch_total_timeout_seconds * 1000),
                max_redirects=settings.fetch_max_redirects,
                max_body_bytes=settings.fetch_max_body_bytes,
                max_text_bytes=settings.fetch_max_text_bytes,
                max_links=settings.fetch_max_links,
            )
            tasks = lease_tasks(
                connection,
                worker_id=payload.worker_id,
                limit=min(payload.limit, settings.worker_batch_size),
                lease_seconds=settings.lease_seconds,
                policy=policy,
            )
            return {"protocol_version": 1, "tasks": [task.to_dict() for task in tasks]}
        finally:
            connection.close()

    @app.post("/v1/worker/results", dependencies=[Depends(require_worker_token)])
    def results(payload: ResultEnvelope) -> dict:
        try:
            result = CrawlResult.from_dict(payload.result)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"invalid result: {exc}") from exc
        connection = connect(settings.database_path)
        try:
            migrate(connection)
            try:
                outcome = import_result(
                    connection,
                    result,
                    max_depth=settings.crawl_max_depth,
                    max_text_bytes=settings.fetch_max_text_bytes,
                    max_links=settings.fetch_max_links,
                )
            except ImportRejected as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            return {"status": "ack", "outcome": asdict(outcome)}
        finally:
            connection.close()

    return app


app = create_app()
