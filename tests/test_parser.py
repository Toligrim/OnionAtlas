from __future__ import annotations

from onionatlas.worker.parser import parse_document

from tests.helpers import onion_host

HOST_A = onion_host(1)
HOST_B = onion_host(2)


def test_html_parser_extracts_text_metadata_and_onion_links() -> None:
    body = f'''<html><head><title> Example  Site </title><meta name="description" content="A test page"></head><body><h1>Hello world</h1><script>secret()</script><p>Visible text</p><a href="/relative">Relative</a><a href="http://{HOST_B}/next#fragment">Other <b>service</b></a><a href="https://example.com/">Clearnet ignored</a></body></html>'''.encode()
    parsed = parse_document(body, base_url=f"http://{HOST_A}/", content_type="text/html", charset="utf-8", max_text_bytes=1024)
    assert parsed.title == "Example Site"
    assert parsed.description == "A test page"
    assert parsed.h1 == "Hello world"
    assert "secret" not in parsed.normalized_text
    assert "Visible text" in parsed.normalized_text
    assert [link.url for link in parsed.links] == [f"http://{HOST_A}/relative", f"http://{HOST_B}/next"]
    assert parsed.links[1].anchor_text == "Other service"


def test_parser_truncates_text_by_utf8_bytes() -> None:
    parsed = parse_document("<p>абвгдежз</p>".encode(), base_url=f"http://{HOST_A}/", content_type="text/html", charset="utf-8", max_text_bytes=7)
    assert len(parsed.normalized_text.encode("utf-8")) <= 7


def test_parser_caps_links() -> None:
    links = "".join(f'<a href="/p/{i}">{i}</a>' for i in range(20))
    parsed = parse_document(links.encode(), base_url=f"http://{HOST_A}/", content_type="text/html", charset="utf-8", max_text_bytes=4096, max_links=5)
    assert len(parsed.links) == 5
