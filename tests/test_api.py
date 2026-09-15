from __future__ import annotations

from pathlib import Path
from fastapi.testclient import TestClient
from onionatlas.api import create_app
from onionatlas.config import Settings
from onionatlas.frontier import enqueue_url
from onionatlas.storage import connect, migrate
from tests.helpers import onion_host

HOST = onion_host(1)


def test_worker_api_auth_lease_and_health(tmp_path: Path) -> None:
    db_path = tmp_path / "api.sqlite3"
    connection = connect(db_path); migrate(connection); enqueue_url(connection, HOST); connection.close()
    client = TestClient(create_app(Settings(database_path=db_path, worker_token="secret", worker_batch_size=2)))
    assert client.get("/healthz").status_code == 200
    assert client.post("/v1/worker/lease", json={"worker_id": "w1", "limit": 1}).status_code == 401
    headers = {"Authorization": "Bearer secret"}
    heartbeat = client.post("/v1/worker/heartbeat", headers=headers, json={"worker_id": "w1", "version": "test", "max_concurrency": 2, "tor_ready": True})
    assert heartbeat.status_code == 200
    lease = client.post("/v1/worker/lease", headers=headers, json={"worker_id": "w1", "limit": 1})
    assert lease.status_code == 200
    assert len(lease.json()["tasks"]) == 1
    assert lease.json()["tasks"][0]["target"]["url"] == f"http://{HOST}/"
