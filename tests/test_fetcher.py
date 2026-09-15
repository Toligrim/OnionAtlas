from __future__ import annotations

import httpx
import pytest

from onionatlas.domain.models import CrawlTask, FetchPolicy
from onionatlas.worker.fetcher import fetch_task_with_client

from tests.helpers import onion_host

HOST_A = onion_host(1)
HOST_B = onion_host(2)


def task(url: str, **policy_overrides: int) -> CrawlTask:
    policy = FetchPolicy(**policy_overrides)
    return CrawlTask(task_id="frontier:1", attempt_id="attempt-1", lease_id="lease-1", lease_expires_at="2099-01-01T00:00:00Z", target_url=url, depth=0, policy=policy)


@pytest.mark.asyncio
async def test_fetcher_returns_structured_success() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        html = f'<title>Test</title><p>Hello</p><a href="http://{HOST_B}/">B</a>'
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=html)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_task_with_client(task(f"http://{HOST_A}/"), worker_id="w1", client=client)
    assert result.success
    assert result.response is not None and result.response.status_code == 200
    assert result.document is not None and result.document.title == "Test"
    assert [link.url for link in result.links] == [f"http://{HOST_B}/"]


@pytest.mark.asyncio
async def test_fetcher_rejects_clearnet_redirect() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com/"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_task_with_client(task(f"http://{HOST_A}/"), worker_id="w1", client=client)
    assert not result.success
    assert result.error is not None and result.error.error_class == "invalid_redirect"


@pytest.mark.asyncio
async def test_fetcher_rejects_disallowed_content_type() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_task_with_client(task(f"http://{HOST_A}/"), worker_id="w1", client=client)
    assert not result.success
    assert result.error is not None and result.error.error_class == "disallowed_content_type"


@pytest.mark.asyncio
async def test_fetcher_enforces_body_limit() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"x" * 20)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_task_with_client(task(f"http://{HOST_A}/", max_body_bytes=10), worker_id="w1", client=client)
    assert not result.success
    assert result.error is not None and result.error.error_class == "response_too_large"
