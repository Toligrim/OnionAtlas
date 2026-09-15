from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin

from onionatlas.domain.results import LinkResult
from onionatlas.domain.urls import InvalidOnionURL, canonicalize_onion_url

_WHITESPACE_RE = re.compile(r"\s+")
_IGNORED_TAGS = {"script", "style", "noscript", "svg", "canvas", "template"}


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    title: str | None
    description: str | None
    h1: str | None
    normalized_text: str
    links: tuple[LinkResult, ...]
    requires_javascript: bool


class _DocumentParser(HTMLParser):
    def __init__(self, base_url: str, max_links: int) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.max_links = max(0, max_links)
        self._ignored_depth = 0
        self._title_depth = 0
        self._h1_depth = 0
        self._title_parts: list[str] = []
        self._h1_parts: list[str] = []
        self._text_parts: list[str] = []
        self._description: str | None = None
        self._current_anchor_href: str | None = None
        self._current_anchor_parts: list[str] = []
        self._raw_links: list[tuple[str, str | None]] = []
        self._script_tags = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr_map = {key.lower(): value for key, value in attrs if value is not None}
        if tag in _IGNORED_TAGS:
            self._ignored_depth += 1
            if tag == "script":
                self._script_tags += 1
            return
        if self._ignored_depth:
            return
        if tag == "title":
            self._title_depth += 1
        elif tag == "h1" and not self._h1_parts:
            self._h1_depth += 1
        elif tag == "meta":
            name = (attr_map.get("name") or "").lower()
            if name == "description" and self._description is None:
                self._description = attr_map.get("content")
        elif tag == "a":
            self._current_anchor_href = attr_map.get("href")
            self._current_anchor_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _IGNORED_TAGS:
            if self._ignored_depth:
                self._ignored_depth -= 1
            return
        if self._ignored_depth:
            return
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        elif tag == "h1" and self._h1_depth:
            self._h1_depth -= 1
        elif tag == "a" and self._current_anchor_href is not None:
            anchor = _normalize_text(" ".join(self._current_anchor_parts)) or None
            if len(self._raw_links) < self.max_links:
                self._raw_links.append((self._current_anchor_href, anchor))
            self._current_anchor_href = None
            self._current_anchor_parts = []

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        value = data.strip()
        if not value:
            return
        self._text_parts.append(value)
        if self._title_depth:
            self._title_parts.append(value)
        if self._h1_depth:
            self._h1_parts.append(value)
        if self._current_anchor_href is not None:
            self._current_anchor_parts.append(value)

    def result(self) -> ParsedDocument:
        normalized_text = _normalize_text(" ".join(self._text_parts))
        links: list[LinkResult] = []
        seen: set[str] = set()
        for href, anchor in self._raw_links:
            try:
                absolute = urljoin(self.base_url, href)
                canonical = canonicalize_onion_url(absolute)
            except (InvalidOnionURL, ValueError):
                continue
            if canonical.url in seen:
                continue
            seen.add(canonical.url)
            links.append(LinkResult(canonical.url, _truncate(anchor, 256)))
        return ParsedDocument(
            title=_truncate(_normalize_text(" ".join(self._title_parts)) or None, 512),
            description=_truncate(_normalize_text(self._description) if self._description else None, 1024),
            h1=_truncate(_normalize_text(" ".join(self._h1_parts)) or None, 512),
            normalized_text=normalized_text,
            links=tuple(links),
            requires_javascript=self._script_tags > 0 and len(normalized_text) < 80,
        )


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return _WHITESPACE_RE.sub(" ", value).strip()


def _truncate(value: str | None, max_chars: int) -> str | None:
    if value is None or len(value) <= max_chars:
        return value
    return value[:max_chars]


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def parse_document(
    body: bytes,
    *,
    base_url: str,
    content_type: str,
    charset: str | None,
    max_text_bytes: int,
    max_links: int = 500,
) -> ParsedDocument:
    encoding = charset or "utf-8"
    try:
        text = body.decode(encoding, errors="replace")
    except LookupError:
        text = body.decode("utf-8", errors="replace")
    if content_type == "text/plain":
        normalized = _truncate_utf8(_normalize_text(text), max_text_bytes)
        return ParsedDocument(None, None, None, normalized, (), False)
    parser = _DocumentParser(base_url, max_links=max_links)
    parser.feed(text)
    parsed = parser.result()
    return ParsedDocument(
        title=parsed.title,
        description=parsed.description,
        h1=parsed.h1,
        normalized_text=_truncate_utf8(parsed.normalized_text, max_text_bytes),
        links=parsed.links,
        requires_javascript=parsed.requires_javascript,
    )
