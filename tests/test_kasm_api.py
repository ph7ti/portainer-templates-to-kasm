"""Tests for the Kasm API client (legacy and v2 response normalization)."""

from __future__ import annotations

import json

import pytest

from orchestrator.kasm_api import KasmApiError, KasmClient, normalize_name


class FakeResponse:
    def __init__(self, status_code: int, payload) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeHttp:
    def __init__(self, routes: dict[tuple[str, str], FakeResponse]) -> None:
        self.routes = routes
        self.calls: list[dict] = []
        self.verify = True

    def request(self, method, url, *, json=None, params=None, auth=None, timeout=None):
        path = url.split("kasm.example.test", 1)[-1]
        self.calls.append(
            {
                "method": method,
                "path": path,
                "json": json,
                "params": params,
                "auth": auth,
            }
        )
        key = (method, path)
        if key not in self.routes:
            return FakeResponse(404, {"error": "not found"})
        return self.routes[key]


def client_with(routes: dict[tuple[str, str], FakeResponse]) -> KasmClient:
    return KasmClient(
        "https://kasm.example.test",
        "key",
        "secret",
        mode="auto",
        timeout=1,
        session=FakeHttp(routes),
    )


def test_legacy_mode_and_normalization() -> None:
    legacy_session = {
        "kasm_id": "abc123",
        "user_id": "user-1",
        "image_id": "img-1",
        "container_id": "cid-1",
        "container_ip": "172.18.0.42",
        "operational_status": "running",
        "image": {
            "image_id": "img-1",
            "name": "kasmweb/chrome:1.19.0",
            "friendly_name": "Teste: Nextcloud",
        },
        "user": {"username": "tester"},
    }
    client = client_with(
        {
            ("POST", "/api/public/get_kasms"): FakeResponse(
                200, {"kasms": [legacy_session]}
            ),
            ("POST", "/api/public/get_images"): FakeResponse(
                200,
                {
                    "images": [
                        {"image_id": "img-1", "friendly_name": "Teste: Nextcloud"},
                        {"image_id": "img-2", "friendly_name": "Teste: Gitea"},
                    ]
                },
            ),
        }
    )
    assert client.detect_mode() == "legacy"
    sessions = client.get_sessions()
    assert len(sessions) == 1
    session = sessions[0]
    assert session.session_id == "abc123"
    assert session.image_id == "img-1"
    assert session.friendly_name == "Teste: Nextcloud"
    assert session.state == "running"
    assert session.username == "tester"
    assert session.container_id == "cid-1"

    images = client.get_images()
    assert images["friendly_to_id"][normalize_name("Teste: Nextcloud")] == "img-1"
    assert images["id_to_friendly"]["img-2"] == "Teste: Gitea"

    # The legacy API receives the key/secret in the body, not Basic auth.
    for call in client.session.calls:
        assert call["json"]["api_key"] == "key"
        assert call["auth"] is None


def test_falls_back_to_v2_basic_auth() -> None:
    v2_session = {
        "session_id": "sess-1",
        "image_id": "img-9",
        "image": {
            "image_id": "img-9",
            "name": "kasmweb/chrome:1.19.0",
            "friendly_name": "Teste: Gitea",
        },
        "container_id": "cid-9",
        "docker_network": "kasm_default_network",
        "observed_state": "running",
        "user_id": "u-1",
        "username": "bob",
    }
    client = client_with(
        {
            ("GET", "/api/session/sessions"): FakeResponse(
                200, {"sessions": [v2_session], "current_time": "now"}
            )
        }
    )
    assert client.detect_mode() == "v2"
    sessions = client.get_sessions()
    assert sessions[0].session_id == "sess-1"
    assert sessions[0].docker_network == "kasm_default_network"
    assert sessions[0].state == "running"
    v2_call = client.session.calls[-1]
    assert v2_call["auth"] == ("key", "secret")


def test_invalid_credentials_raise() -> None:
    client = client_with(
        {
            ("POST", "/api/public/get_kasms"): FakeResponse(
                200, {"error_message": "Unauthorized"}
            ),
            ("GET", "/api/session/sessions"): FakeResponse(
                404, {"error": "not found"}
            ),
        }
    )
    with pytest.raises(KasmApiError):
        client.detect_mode()


def test_http_error_raises() -> None:
    client = client_with(
        {("POST", "/api/public/get_kasms"): FakeResponse(403, {"detail": "no"})}
    )
    with pytest.raises(KasmApiError):
        client.get_sessions()


def test_image_listing_failure_is_non_fatal() -> None:
    client = client_with(
        {
            ("POST", "/api/public/get_kasms"): FakeResponse(200, {"kasms": []}),
            ("POST", "/api/public/get_images"): FakeResponse(403, {"detail": "no"}),
        }
    )
    images = client.get_images()
    assert images == {"friendly_to_id": {}, "id_to_friendly": {}}


def test_legacy_payload_shape() -> None:
    http = FakeHttp(
        {("POST", "/api/public/get_kasms"): FakeResponse(200, {"kasms": []})}
    )
    client = KasmClient(
        "https://kasm.example.test",
        "key",
        "secret",
        mode="legacy",
        session=http,
    )
    client.get_sessions()
    assert http.calls[0]["json"] == {"api_key": "key", "api_key_secret": "secret"}
    assert http.calls[0]["path"] == "/api/public/get_kasms"
