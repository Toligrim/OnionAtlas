from __future__ import annotations

import json
import os
from pathlib import Path

from onionatlas.domain.results import CrawlResult


class WorkerSpool:
    def __init__(
        self,
        root: Path,
        *,
        max_results: int = 1000,
        max_bytes: int = 256 * 1024 * 1024,
        max_rejected: int = 100,
    ) -> None:
        self.root = root
        self.pending = root / "results"
        self.rejected = root / "rejected"
        self.max_results = max_results
        self.max_bytes = max_bytes
        self.max_rejected = max_rejected
        self.pending.mkdir(parents=True, exist_ok=True)
        self.rejected.mkdir(parents=True, exist_ok=True)

    def _safe_name(self, attempt_id: str) -> str:
        safe = "".join(ch for ch in attempt_id if ch.isalnum() or ch in {"-", "_"})
        if not safe:
            raise ValueError("invalid attempt id")
        return safe

    def _path(self, attempt_id: str) -> Path:
        return self.pending / f"{self._safe_name(attempt_id)}.json"

    def save(self, result: CrawlResult) -> Path:
        payload = json.dumps(result.to_dict(), ensure_ascii=False, separators=(",", ":"))
        data = payload.encode("utf-8")
        if self.is_full(extra_bytes=len(data)):
            raise OSError("worker spool limit exceeded")
        destination = self._path(result.attempt_id)
        tmp = destination.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, destination)
        return destination

    def items(self) -> list[Path]:
        return sorted(self.pending.glob("*.json"), key=lambda path: path.stat().st_mtime)

    def rejected_items(self) -> list[Path]:
        return sorted(self.rejected.glob("*.json"), key=lambda path: path.stat().st_mtime)

    def load(self, path: Path) -> CrawlResult:
        return CrawlResult.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def acknowledge(self, path: Path) -> None:
        path.unlink(missing_ok=True)

    def reject(self, path: Path) -> Path:
        destination = self.rejected / path.name
        os.replace(path, destination)
        items = self.rejected_items()
        while len(items) > self.max_rejected:
            items.pop(0).unlink(missing_ok=True)
        return destination

    def usage(self) -> tuple[int, int]:
        files = self.items()
        return len(files), sum(path.stat().st_size for path in files)

    def is_full(self, *, extra_bytes: int = 0) -> bool:
        count, size = self.usage()
        return count >= self.max_results or size + extra_bytes > self.max_bytes
