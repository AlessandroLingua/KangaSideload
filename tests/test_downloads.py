"""Download degli IPA dei tool jailbreak (rete simulata)."""
import io
import zipfile

import pytest

from kanga.core import downloads, store
from kanga.core.jailbreak_catalog import find_tool


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Payload/X.app/Info.plist", b"x")
    return buf.getvalue()


class FakeResponse:
    def __init__(self, body: bytes, content_type="application/octet-stream"):
        self.body = body
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(body))}
        self.url = "https://example.com/file"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self.body), 7):
            yield self.body[i:i + 7]


def test_download_dir_is_outside_the_project(appdata):
    assert downloads.download_dir() == store.base_dir() / "downloads"


def test_direct_download_gets_ipa_extension():
    assert downloads._direct_dest_name(find_tool("phoenix")) == "phoenix.ipa"
    assert downloads._direct_dest_name(find_tool("etasonjb")) == "etasonjb-etasonJB-RC4.ipa"


def test_fetch_direct_tool(appdata, monkeypatch):
    body = _zip_bytes()
    monkeypatch.setattr(downloads.requests, "get", lambda *a, **k: FakeResponse(body))
    seen = []
    path = downloads.fetch_tool_ipa(find_tool("phoenix"), progress_cb=lambda pct, _n: seen.append(pct))
    assert path == store.base_dir() / "downloads" / "phoenix.ipa"
    assert path.read_bytes() == body and seen[-1] == 100
    # seconda volta: già in cache, nessun download
    monkeypatch.setattr(downloads.requests, "get", lambda *a, **k: pytest.fail("non deve riscaricare"))
    assert downloads.fetch_tool_ipa(find_tool("phoenix")) == path


def test_non_ipa_download_is_rejected(appdata, monkeypatch):
    monkeypatch.setattr(downloads.requests, "get", lambda *a, **k: FakeResponse(b"<?php echo 1;"))
    with pytest.raises(downloads.DownloadError, match="non è un IPA"):
        downloads.fetch_tool_ipa(find_tool("phoenix"))
    assert not any((store.base_dir() / "downloads").iterdir())  # niente file parziali


def test_html_page_is_rejected(appdata, monkeypatch):
    monkeypatch.setattr(downloads.requests, "get", lambda *a, **k: FakeResponse(b"<html>", "text/html"))
    with pytest.raises(downloads.DownloadError, match="HTML"):
        downloads.fetch_tool_ipa(find_tool("phoenix"))
