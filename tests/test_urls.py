from __future__ import annotations

import pytest

from onionatlas.domain.urls import InvalidOnionURL, canonicalize_onion_url, is_v3_onion_host

from tests.helpers import onion_host


HOST = onion_host(1)


def test_v3_host_validation() -> None:
    assert len(HOST.removesuffix(".onion")) == 56
    assert is_v3_onion_host(HOST)
    assert not is_v3_onion_host("abcdefghijklmnop.onion")
    assert not is_v3_onion_host("a" * 56 + ".onion")


def test_canonicalization_removes_fragment_and_default_port() -> None:
    result = canonicalize_onion_url(f"HTTP://{HOST}:80/path?a=1#fragment")
    assert result.url == f"http://{HOST}/path?a=1"
    assert result.onion_host == HOST
    assert result.path == "/path"


def test_canonicalization_accepts_bare_host() -> None:
    result = canonicalize_onion_url(HOST)
    assert result.url == f"http://{HOST}/"
    assert result.is_homepage


def test_rejects_non_onion_and_userinfo() -> None:
    with pytest.raises(InvalidOnionURL):
        canonicalize_onion_url("https://example.com/")
    with pytest.raises(InvalidOnionURL):
        canonicalize_onion_url(f"http://user:pass@{HOST}/")


def test_syntactically_valid_but_bad_checksum_is_rejected() -> None:
    assert not is_v3_onion_host("a" * 56 + ".onion")
