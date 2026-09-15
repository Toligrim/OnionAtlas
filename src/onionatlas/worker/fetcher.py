from __future__ import annotations

import asyncio
import hashlib
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator
from urllib.parse import urljoin

import httpx

from onionatlas.domain.models import CrawlTask
from onionatlas.domain.results import CrawlResult, DocumentResult, ErrorResult, ResponseResult
from onionatlas.domain.urls import InvalidOnionURL, canonicalize_onion_url
from onionatlas.timeutil import to_iso, utcnow
from onionatlas.worker.parser import parse_document

_REDIRECT_CODES = {301, 302, 303, 307, 308}
_USER_AGENT = "OnionAtlas/0.1 (+research crawler; no JS; HTML/text only)"


class FetchFailure(Exception):
    def __init__(self, error_class: str, message: str) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.message = message[:500]


def _charset_from_content_type(header: str) -> str | None:
    for part in header.split(";")[1:]:
        key, sep, value = part.strip().partition("=")
        if sep and key.lower() == "charset":
            return value.strip().strip('"\'')[:64] or None
    return None


def _mime_type(header: str) -> str:
    return header.split(";", 1)[0].strip().lower()


@asynccontextmanager
async def _owned_client(proxy_url: str) -> AsyncIterator[httpx.AsyncClient]:
    client = httpx.AsyncClient(proxy=proxy_url, follow_redirects=False)
    try:
        yield client
    finally:
        await client.aclose()


async def fetch_task(task: CrawlTask, *, worker_id: str, proxy_url: str = "socks5://127.0.0.1:9050") -> CrawlResult:
    async with _owned_client(proxy_url) as client:
        return await fetch_task_with_client(task, worker_id=worker_id, client=client)


async def fetch_task_with_client(task: CrawlTask, *, worker_id: str, client: httpx.AsyncClient) -> CrawlResult:
    started_at = to_iso(utcnow()) or ""
    started_monotonic = time.monotonic()
    try:
        async with asyncio.timeout(task.policy.total_timeout_ms / 1000):
            canonical = canonicalize_onion_url(task.target_url)
            current_url = canonical.url
            redirect_chain: list[str] = []
            body = b""
            response_status = 0
            response_content_type = ""
            response_charset: str | None = None
            for redirect_number in range(task.policy.max_redirects + 1):
                timeout = httpx.Timeout(timeout=task.policy.total_timeout_ms / 1000, connect=task.policy.connect_timeout_ms / 1000)
                try:
                    async with client.stream(
                        "GET",
                        current_url,
                        headers={"User-Agent": _USER_AGENT, "Accept": "text/html, application/xhtml+xml, text/plain;q=0.9"},
                        timeout=timeout,
                        follow_redirects=False,
                    ) as response:
                        response_status = response.status_code
                        if response.status_code in _REDIRECT_CODES:
                            location = response.headers.get("location")
                            if not location:
                                raise FetchFailure("invalid_redirect", "redirect without Location")
                            if redirect_number >= task.policy.max_redirects:
                                raise FetchFailure("redirect_limit", "maximum redirects exceeded")
                            target = urljoin(current_url, location)
                            try:
                                next_url = canonicalize_onion_url(target).url
                            except InvalidOnionURL as exc:
                                raise FetchFailure("invalid_redirect", "redirect target is outside onion policy") from exc
                            redirect_chain.append(next_url)
                            current_url = next_url
                            continue

                        content_type_header = response.headers.get("content-type", "")
                        response_content_type = _mime_type(content_type_header)
                        if response_content_type not in task.policy.allowed_content_types:
                            raise FetchFailure("disallowed_content_type", response_content_type or "missing Content-Type")
                        response_charset = _charset_from_content_type(content_type_header)
                        content_length = response.headers.get("content-length")
                        if content_length:
                            try:
                                if int(content_length) > task.policy.max_body_bytes:
                                    raise FetchFailure("response_too_large", "Content-Length over limit")
                            except ValueError:
                                pass
                        chunks: list[bytes] = []
                        size = 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > task.policy.max_body_bytes:
                                raise FetchFailure("response_too_large", "body over size limit")
                            chunks.append(chunk)
                        body = b"".join(chunks)
                        break
                except httpx.ConnectTimeout as exc:
                    raise FetchFailure("connect_timeout", "connection timed out") from exc
                except httpx.ReadTimeout as exc:
                    raise FetchFailure("read_timeout", "response read timed out") from exc
                except httpx.ProxyError as exc:
                    raise FetchFailure("tor_unavailable", "Tor SOCKS proxy unavailable") from exc
                except httpx.ConnectError as exc:
                    raise FetchFailure("connect_error", "connection failed") from exc
                except httpx.HTTPError as exc:
                    raise FetchFailure("http_error", type(exc).__name__) from exc
            else:  # pragma: no cover
                raise FetchFailure("redirect_limit", "maximum redirects exceeded")

            parsed = parse_document(
                body,
                base_url=current_url,
                content_type=response_content_type,
                charset=response_charset,
                max_text_bytes=task.policy.max_text_bytes,
                max_links=task.policy.max_links,
            )
            content_hash = "sha256:" + hashlib.sha256(body).hexdigest()
            text_hash = "sha256:" + hashlib.sha256(parsed.normalized_text.encode("utf-8")).hexdigest()
            elapsed_ms = int((time.monotonic() - started_monotonic) * 1000)
            return CrawlResult(
                task_id=task.task_id,
                attempt_id=task.attempt_id,
                lease_id=task.lease_id,
                worker_id=worker_id,
                started_at=started_at,
                finished_at=to_iso(utcnow()) or "",
                success=True,
                request_url=task.target_url,
                response=ResponseResult(
                    final_url=current_url,
                    status_code=response_status,
                    content_type=response_content_type,
                    charset=response_charset,
                    body_bytes=len(body),
                    normalized_text_bytes=len(parsed.normalized_text.encode("utf-8")),
                    elapsed_ms=elapsed_ms,
                    redirect_chain=tuple(redirect_chain),
                ),
                document=DocumentResult(
                    title=parsed.title,
                    description=parsed.description,
                    h1=parsed.h1,
                    normalized_text=parsed.normalized_text,
                    content_hash=content_hash,
                    text_hash=text_hash,
                    requires_javascript=parsed.requires_javascript,
                ),
                links=parsed.links,
            )
    except TimeoutError:
        failure = FetchFailure("total_timeout", "total fetch timeout exceeded")
    except InvalidOnionURL as exc:
        failure = FetchFailure("invalid_target", str(exc))
    except FetchFailure as exc:
        failure = exc
    except Exception as exc:
        failure = FetchFailure("internal_error", type(exc).__name__)

    return CrawlResult(
        task_id=task.task_id,
        attempt_id=task.attempt_id,
        lease_id=task.lease_id,
        worker_id=worker_id,
        started_at=started_at,
        finished_at=to_iso(utcnow()) or "",
        success=False,
        request_url=task.target_url,
        error=ErrorResult(failure.error_class, failure.message),
    )
