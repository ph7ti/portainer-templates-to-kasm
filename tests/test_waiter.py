"""Tests for the waiting-page server."""

from __future__ import annotations

import threading
import urllib.error
import urllib.request

import pytest

from waiter import wait_server


def test_page_template_formats_target_and_polling_script() -> None:
    page = wait_server.PAGE.format(target="http://nextcloud:80")
    assert "Preparando seu aplicativo" in page
    assert "http://nextcloud:80" not in page  # target is read from location.search
    assert "fetch(target" in page
    assert "no-cors" in page
    assert "location.replace(target)" in page
    assert "kasm-wait" not in page


@pytest.fixture
def server():
    httpd = wait_server.ThreadingHTTPServer(("127.0.0.1", 0), wait_server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        thread.join(timeout=2)


def test_health_and_wait_routes(server: str) -> None:
    with urllib.request.urlopen(server + "/healthz", timeout=5) as response:
        assert response.status == 200
        assert response.read() == b"ok"

    with urllib.request.urlopen(
        server + "/wait?target=http://nextcloud:80", timeout=5
    ) as response:
        body = response.read().decode("utf-8")
        assert response.status == 200
        assert "Preparando" in body
        assert "fetch(target" in body


@pytest.mark.parametrize(
    "query",
    [
        "",
        "?target=javascript:alert(1)",
        "?target=http://bad%20host:80",
        "?target=ftp://nextcloud:21",
    ],
)
def test_rejects_invalid_targets(server: str, query: str) -> None:
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(server + "/wait" + query, timeout=5)
    assert exc.value.code == 400
