from __future__ import annotations

import http.client

import pytest

from bp_transcriber.history import HistoryStore
from bp_transcriber.server import AppServer


@pytest.fixture
def server(tmp_path, result):
    ui = tmp_path / "ui"
    (ui / "js").mkdir(parents=True)
    (ui / "_dev_probe.html").write_text("<html>probe</html>", encoding="utf-8")
    (ui / "js" / "app.js").write_text("console.log('привет')", encoding="utf-8")
    (ui / "font.woff2").write_bytes(b"wOF2")
    (ui / "icon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")

    history = HistoryStore(tmp_path / "history")
    pending = history.new_entry()
    pending.preview_path.write_bytes(bytes(range(256)) * 4)  # 1024 байта
    tid = history.save_result(result, "/x/a.wav", pending=pending)
    no_preview = history.save_result(result, "/x/b.wav")

    srv = AppServer(ui, history, preferred_port=None).start()
    yield srv, tid, no_preview, ui
    srv.stop()


def request(srv, path, headers=None, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
    hdrs = dict(headers or {})
    conn.putrequest("GET", path, skip_host=True)
    conn.putheader("Host", host or f"127.0.0.1:{srv.port}")
    for k, v in hdrs.items():
        conn.putheader(k, v)
    conn.endheaders()
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp, body


def test_static_mimetypes(server):
    srv = server[0]
    resp, body = request(srv, "/js/app.js")
    assert resp.status == 200 and "привет".encode() in body
    assert resp.getheader("Content-Type") == "text/javascript; charset=UTF-8"
    assert request(srv, "/font.woff2")[0].getheader("Content-Type") == "font/woff2"
    assert request(srv, "/icon.svg")[0].getheader("Content-Type").startswith("image/svg+xml")
    assert request(srv, "/missing.css")[0].status == 404


def test_path_traversal_blocked(server):
    srv = server[0]
    resp, body = request(srv, "/../secret.txt")
    assert resp.status in (403, 404) and body != b"secret"
    resp, body = request(srv, "/%2e%2e/secret.txt")
    assert resp.status in (403, 404) and body != b"secret"


def test_media_requires_key(server):
    srv, tid, _, _ = server
    url = srv.media_url(tid)
    assert url == f"http://127.0.0.1:{srv.port}/media/{tid}?k={srv.key}"
    assert request(srv, f"/media/{tid}")[0].status == 403
    assert request(srv, f"/media/{tid}?k=wrong")[0].status == 403
    resp, body = request(srv, f"/media/{tid}?k={srv.key}")
    assert resp.status == 200 and len(body) == 1024
    assert resp.getheader("Content-Type") == "audio/mp4"
    assert resp.getheader("Accept-Ranges") == "bytes"


def test_media_range_206(server):
    srv, tid, _, _ = server
    resp, body = request(srv, f"/media/{tid}?k={srv.key}", {"Range": "bytes=0-1"})
    assert resp.status == 206 and body == bytes([0, 1])
    assert resp.getheader("Content-Range") == "bytes 0-1/1024"
    resp, body = request(srv, f"/media/{tid}?k={srv.key}", {"Range": "bytes=1000-"})
    assert resp.status == 206 and len(body) == 24
    assert resp.getheader("Content-Range") == "bytes 1000-1023/1024"


def test_media_missing(server):
    srv, _, no_preview, _ = server
    assert srv.media_url(no_preview) is None
    assert request(srv, f"/media/{no_preview}?k={srv.key}")[0].status == 404
    assert request(srv, f"/media/zzz?k={srv.key}")[0].status == 404


def test_foreign_host_rejected(server):
    srv = server[0]
    assert request(srv, "/", host="evil.example:80")[0].status == 403
    assert request(srv, "/", host=f"localhost:{srv.port}")[0].status == 200


def test_preferred_port_fallback(tmp_path, server):
    srv = server[0]
    other = AppServer(tmp_path / "ui", srv.history, preferred_port=srv.port).start()
    try:
        assert other.port != srv.port
    finally:
        other.stop()
