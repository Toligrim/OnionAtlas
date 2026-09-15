from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .models import PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class LinkResult:
    url: str
    anchor_text: str | None = None


@dataclass(frozen=True, slots=True)
class DocumentResult:
    title: str | None
    description: str | None
    h1: str | None
    normalized_text: str
    content_hash: str
    text_hash: str
    requires_javascript: bool = False


@dataclass(frozen=True, slots=True)
class ResponseResult:
    final_url: str
    status_code: int
    content_type: str
    charset: str | None
    body_bytes: int
    normalized_text_bytes: int
    elapsed_ms: int
    redirect_chain: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ErrorResult:
    error_class: str
    message: str


@dataclass(frozen=True, slots=True)
class CrawlResult:
    task_id: str
    attempt_id: str
    lease_id: str
    worker_id: str
    started_at: str
    finished_at: str
    success: bool
    request_url: str
    response: ResponseResult | None = None
    document: DocumentResult | None = None
    links: tuple[LinkResult, ...] = ()
    error: ErrorResult | None = None
    protocol_version: int = PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "protocol_version": self.protocol_version,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "lease_id": self.lease_id,
            "worker_id": self.worker_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "success": self.success,
            "request": {"url": self.request_url},
        }
        if self.response is not None:
            response = asdict(self.response)
            response["redirect_chain"] = list(self.response.redirect_chain)
            payload["response"] = response
        if self.document is not None:
            payload["document"] = asdict(self.document)
        payload["links"] = [asdict(link) for link in self.links]
        if self.error is not None:
            payload["error"] = {"class": self.error.error_class, "message": self.error.message}
        return payload

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CrawlResult":
        version = int(value.get("protocol_version", 0))
        if version != PROTOCOL_VERSION:
            raise ValueError(f"unsupported protocol version: {version}")
        success = bool(value["success"])
        response_value = value.get("response")
        document_value = value.get("document")
        error_value = value.get("error")
        response = None
        if response_value is not None:
            response = ResponseResult(
                final_url=str(response_value["final_url"]),
                status_code=int(response_value["status_code"]),
                content_type=str(response_value["content_type"]),
                charset=(str(response_value["charset"]) if response_value.get("charset") is not None else None),
                body_bytes=int(response_value["body_bytes"]),
                normalized_text_bytes=int(response_value.get("normalized_text_bytes", 0)),
                elapsed_ms=int(response_value["elapsed_ms"]),
                redirect_chain=tuple(response_value.get("redirect_chain") or ()),
            )
        document = None
        if document_value is not None:
            document = DocumentResult(
                title=document_value.get("title"),
                description=document_value.get("description"),
                h1=document_value.get("h1"),
                normalized_text=str(document_value.get("normalized_text", "")),
                content_hash=str(document_value["content_hash"]),
                text_hash=str(document_value["text_hash"]),
                requires_javascript=bool(document_value.get("requires_javascript", False)),
            )
        error = None
        if error_value is not None:
            error = ErrorResult(str(error_value["class"]), str(error_value.get("message", "")))
        links = tuple(LinkResult(str(item["url"]), item.get("anchor_text")) for item in value.get("links") or ())
        if success and (response is None or document is None):
            raise ValueError("successful result requires response and document")
        if not success and error is None:
            raise ValueError("failed result requires error")
        return cls(
            task_id=str(value["task_id"]),
            attempt_id=str(value["attempt_id"]),
            lease_id=str(value.get("lease_id") or ""),
            worker_id=str(value["worker_id"]),
            started_at=str(value["started_at"]),
            finished_at=str(value["finished_at"]),
            success=success,
            request_url=str(value["request"]["url"]),
            response=response,
            document=document,
            links=links,
            error=error,
            protocol_version=version,
        )
