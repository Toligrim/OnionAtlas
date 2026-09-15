from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path = Path("onionatlas.sqlite3")
    lease_seconds: int = 300
    worker_batch_size: int = 2
    worker_token: str | None = None
    tor_socks_url: str = "socks5://127.0.0.1:9050"
    control_url: str | None = None
    worker_id: str = "worker-01"
    worker_spool_dir: Path = Path("worker-spool")
    worker_poll_seconds: float = 15.0
    worker_allow_insecure_control: bool = False
    fetch_connect_timeout_seconds: float = 30.0
    fetch_total_timeout_seconds: float = 90.0
    fetch_max_redirects: int = 3
    fetch_max_body_bytes: int = 2 * 1024 * 1024
    fetch_max_text_bytes: int = 1 * 1024 * 1024
    fetch_max_links: int = 500
    crawl_max_depth: int = 3

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_path=Path(os.getenv("ONIONATLAS_DB", "onionatlas.sqlite3")),
            lease_seconds=int(os.getenv("ONIONATLAS_LEASE_SECONDS", "300")),
            worker_batch_size=int(os.getenv("ONIONATLAS_WORKER_BATCH_SIZE", "2")),
            worker_token=os.getenv("ONIONATLAS_WORKER_TOKEN"),
            tor_socks_url=os.getenv("ONIONATLAS_TOR_SOCKS", "socks5://127.0.0.1:9050"),
            control_url=os.getenv("ONIONATLAS_CONTROL_URL"),
            worker_id=os.getenv("ONIONATLAS_WORKER_ID", "worker-01"),
            worker_spool_dir=Path(os.getenv("ONIONATLAS_WORKER_SPOOL", "worker-spool")),
            worker_poll_seconds=float(os.getenv("ONIONATLAS_WORKER_POLL_SECONDS", "15")),
            worker_allow_insecure_control=os.getenv(
                "ONIONATLAS_WORKER_ALLOW_INSECURE_CONTROL", "0"
            ).lower()
            in {"1", "true", "yes"},
            fetch_connect_timeout_seconds=float(
                os.getenv("ONIONATLAS_FETCH_CONNECT_TIMEOUT", "30")
            ),
            fetch_total_timeout_seconds=float(os.getenv("ONIONATLAS_FETCH_TOTAL_TIMEOUT", "90")),
            fetch_max_redirects=int(os.getenv("ONIONATLAS_FETCH_MAX_REDIRECTS", "3")),
            fetch_max_body_bytes=int(
                os.getenv("ONIONATLAS_FETCH_MAX_BODY_BYTES", str(2 * 1024 * 1024))
            ),
            fetch_max_text_bytes=int(
                os.getenv("ONIONATLAS_FETCH_MAX_TEXT_BYTES", str(1 * 1024 * 1024))
            ),
            fetch_max_links=int(os.getenv("ONIONATLAS_FETCH_MAX_LINKS", "500")),
            crawl_max_depth=int(os.getenv("ONIONATLAS_CRAWL_MAX_DEPTH", "3")),
        )
