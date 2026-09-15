from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


PROTOCOL_VERSION = 1


@dataclass(frozen=True, slots=True)
class FetchPolicy:
    connect_timeout_ms: int = 30_000
    total_timeout_ms: int = 90_000
    max_redirects: int = 3
    max_body_bytes: int = 2 * 1024 * 1024
    max_text_bytes: int = 1 * 1024 * 1024
    max_links: int = 500
    allowed_content_types: tuple[str, ...] = (
        "text/html",
        "application/xhtml+xml",
        "text/plain",
    )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["allowed_content_types"] = list(self.allowed_content_types)
        return payload

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "FetchPolicy":
        return cls(
            connect_timeout_ms=int(value.get("connect_timeout_ms", 30_000)),
            total_timeout_ms=int(value.get("total_timeout_ms", 90_000)),
            max_redirects=int(value.get("max_redirects", 3)),
            max_body_bytes=int(value.get("max_body_bytes", 2 * 1024 * 1024)),
            max_text_bytes=int(value.get("max_text_bytes", 1 * 1024 * 1024)),
            max_links=int(value.get("max_links", 500)),
            allowed_content_types=tuple(
                value.get(
                    "allowed_content_types",
                    ["text/html", "application/xhtml+xml", "text/plain"],
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class CrawlTask:
    task_id: str
    attempt_id: str
    lease_id: str
    lease_expires_at: str
    target_url: str
    depth: int
    policy: FetchPolicy = field(default_factory=FetchPolicy)
    protocol_version: int = PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "lease_id": self.lease_id,
            "lease_expires_at": self.lease_expires_at,
            "target": {"url": self.target_url, "depth": self.depth},
            "policy": self.policy.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CrawlTask":
        version = int(value.get("protocol_version", 0))
        if version != PROTOCOL_VERSION:
            raise ValueError(f"unsupported protocol version: {version}")
        target = value["target"]
        return cls(
            task_id=str(value["task_id"]),
            attempt_id=str(value["attempt_id"]),
            lease_id=str(value["lease_id"]),
            lease_expires_at=str(value["lease_expires_at"]),
            target_url=str(target["url"]),
            depth=int(target.get("depth", 0)),
            policy=FetchPolicy.from_dict(dict(value.get("policy") or {})),
            protocol_version=version,
        )
