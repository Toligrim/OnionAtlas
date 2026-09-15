from __future__ import annotations

import pytest

from onionatlas.discovery.sources import HttpRegexConfig, _validate_source_url


def test_external_source_requires_https() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        _validate_source_url("http://example.com/list")
    _validate_source_url("https://example.com/list")


def test_external_source_rejects_userinfo() -> None:
    with pytest.raises(ValueError, match="userinfo"):
        _validate_source_url("https://user:pass@example.com/list")


def test_candidate_limit_is_bounded_in_config() -> None:
    config = HttpRegexConfig("https://example.com/list", max_candidates=25)
    assert config.max_candidates == 25
