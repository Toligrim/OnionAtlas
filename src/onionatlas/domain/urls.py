from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from urllib.parse import SplitResult, parse_qsl, urlencode, urlsplit, urlunsplit

_ONION_V3_RE = re.compile(r"^[a-z2-7]{56}\.onion$")
_DEFAULT_PORTS = {"http": 80, "https": 443}
_ONION_CHECKSUM_PREFIX = b".onion checksum"
_ONION_V3_VERSION = b"\x03"


class InvalidOnionURL(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CanonicalOnionURL:
    url: str
    onion_host: str
    path: str
    query: str
    is_homepage: bool


def _decode_v3_host(host: str) -> bytes | None:
    normalized = host.lower().rstrip(".")
    if not _ONION_V3_RE.fullmatch(normalized):
        return None
    label = normalized.removesuffix(".onion")
    try:
        decoded = base64.b32decode(label.upper(), casefold=False)
    except (ValueError, base64.binascii.Error):
        return None
    if len(decoded) != 35:
        return None
    public_key = decoded[:32]
    checksum = decoded[32:34]
    version = decoded[34:35]
    if version != _ONION_V3_VERSION:
        return None
    expected = hashlib.sha3_256(_ONION_CHECKSUM_PREFIX + public_key + version).digest()[:2]
    if checksum != expected:
        return None
    return decoded


def is_v3_onion_host(host: str | None) -> bool:
    if not host:
        return False
    return _decode_v3_host(host) is not None


def _normalized_netloc(parts: SplitResult, host: str) -> str:
    try:
        port = parts.port
    except ValueError as exc:
        raise InvalidOnionURL("invalid port") from exc
    if parts.username is not None or parts.password is not None:
        raise InvalidOnionURL("userinfo is not allowed")
    if port is None or port == _DEFAULT_PORTS[parts.scheme]:
        return host
    if not 1 <= port <= 65535:
        raise InvalidOnionURL("port out of range")
    return f"{host}:{port}"


def _normalized_query(query: str) -> str:
    if not query:
        return ""
    pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=False)
    return urlencode(pairs, doseq=True)


def canonicalize_onion_url(raw_url: str) -> CanonicalOnionURL:
    value = raw_url.strip()
    if not value:
        raise InvalidOnionURL("empty URL")
    if "://" not in value:
        value = "http://" + value
    parts = urlsplit(value)
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        raise InvalidOnionURL("only http/https onion URLs are allowed")
    host = (parts.hostname or "").lower().rstrip(".")
    if not is_v3_onion_host(host):
        raise InvalidOnionURL("host is not a valid v3 onion hostname")
    netloc = _normalized_netloc(parts._replace(scheme=scheme), host)
    path = parts.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    query = _normalized_query(parts.query)
    canonical = urlunsplit((scheme, netloc, path, query, ""))
    return CanonicalOnionURL(
        url=canonical,
        onion_host=host,
        path=path,
        query=query,
        is_homepage=path == "/" and query == "",
    )
