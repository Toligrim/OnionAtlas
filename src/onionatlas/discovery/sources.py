from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote_plus, urlsplit

import httpx

_ONION_RE = re.compile(
    r"(?<![a-z2-7])([a-z2-7]{56}\.onion)(?![a-z2-7.])",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class HttpRegexConfig:
    url_template: str
    queries: tuple[str, ...] = ("",)
    max_bytes: int = 2 * 1024 * 1024
    max_candidates: int = 5000


def _validate_source_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("external discovery source must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("external discovery source must not contain userinfo")


async def fetch_http_candidates(config: HttpRegexConfig, *, query: str) -> list[str]:
    url = config.url_template.replace("{query}", quote_plus(query))
    _validate_source_url(url)

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
        async with client.stream(
            "GET",
            url,
            headers={"User-Agent": "OnionAtlas/0.1"},
        ) as response:
            if 300 <= response.status_code < 400:
                raise ValueError("external discovery redirects are not followed")
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if not (content_type.startswith("text/") or "json" in content_type):
                raise ValueError("external discovery source returned non-text content")

            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > config.max_bytes:
                    raise ValueError("external discovery response too large")
                chunks.append(chunk)

    text = b"".join(chunks).decode("utf-8", errors="replace")
    seen: set[str] = set()
    result: list[str] = []
    for match in _ONION_RE.finditer(text):
        host = match.group(1).lower()
        if host in seen:
            continue
        seen.add(host)
        result.append(host)
        if len(result) >= config.max_candidates:
            break
    return result
