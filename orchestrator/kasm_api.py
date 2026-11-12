"""Thin client for the Kasm Workspaces API (legacy and v2 generations)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import requests

LOG = logging.getLogger("orchestrator.kasm")


class KasmApiError(RuntimeError):
    """Raised when the Kasm API cannot be read reliably."""


def normalize_name(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


@dataclass
class Session:
    session_id: str
    image_id: str | None = None
    image_name: str | None = None
    friendly_name: str | None = None
    container_id: str | None = None
    container_ip: str | None = None
    docker_network: str | None = None
    state: str = ""
    user_id: str | None = None
    username: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class KasmClient:
    """Read sessions and images from Kasm.

    Supports the legacy Developer API (Kasm <= 1.19.0: POST /api/public/* with
    api_key/api_key_secret in the JSON body) and the newer REST API used by
    later builds (GET /api/session/sessions with HTTP Basic auth). The mode is
    auto-detected once and cached.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_key_secret: str,
        *,
        mode: str = "auto",
        verify_tls: bool = True,
        timeout: float = 20.0,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_key_secret = api_key_secret
        self.requested_mode = mode
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.verify = verify_tls
        self.mode: str | None = None
        if mode in ("legacy", "v2"):
            self.mode = mode

    # -- low level ---------------------------------------------------------
    def _auth(self) -> tuple[str, str]:
        return self.api_key, self.api_key_secret

    def _legacy_payload(self) -> dict[str, str]:
        return {"api_key": self.api_key, "api_key_secret": self.api_key_secret}

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        basic: bool = False,
        params: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        try:
            response = self.session.request(
                method,
                url,
                json=json_body,
                params=params,
                auth=self._auth() if basic else None,
                timeout=self.timeout,
            )
        except requests.exceptions.SSLError as exc:
            raise KasmApiError(
                f"{method} {path} failed during TLS validation: {exc}. "
                "If the Kasm instance uses an internal CA, install that CA or set "
                "KASM_VERIFY_TLS=false for this deployment."
            ) from exc
        except requests.RequestException as exc:
            raise KasmApiError(f"{method} {path} failed: {exc}") from exc

        if response.status_code != 200:
            snippet = response.text[:300].replace("\n", " ")
            raise KasmApiError(
                f"{method} {path} returned HTTP {response.status_code}: {snippet}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise KasmApiError(f"{method} {path} returned invalid JSON") from exc
        if isinstance(payload, dict) and payload.get("error_message"):
            raise KasmApiError(f"{method} {path}: {payload['error_message']}")
        return payload

    # -- mode detection ----------------------------------------------------
    def detect_mode(self) -> str:
        if self.mode:
            return self.mode
        if self.requested_mode == "legacy":
            self.mode = "legacy"
            return self.mode
        if self.requested_mode == "v2":
            self.mode = "v2"
            return self.mode

        try:
            payload = self._request(
                "POST", "/api/public/get_kasms", json_body=self._legacy_payload()
            )
            if isinstance(payload, dict) and "kasms" in payload:
                self.mode = "legacy"
                LOG.info("Kasm API mode: legacy (/api/public/get_kasms)")
                return self.mode
        except KasmApiError as exc:
            LOG.debug("legacy API detection failed: %s", exc)

        try:
            payload = self._request("GET", "/api/session/sessions", basic=True)
            if isinstance(payload, dict) and ("sessions" in payload or "data" in payload):
                self.mode = "v2"
                LOG.info("Kasm API mode: v2 (/api/session/sessions)")
                return self.mode
        except KasmApiError as exc:
            LOG.debug("v2 API detection failed: %s", exc)

        raise KasmApiError(
            "could not detect the Kasm API generation; set KASM_API_MODE=legacy or v2"
        )

    # -- public API --------------------------------------------------------
    def get_sessions(self) -> list[Session]:
        mode = self.detect_mode()
        if mode == "legacy":
            payload = self._request(
                "POST", "/api/public/get_kasms", json_body=self._legacy_payload()
            )
            items = payload.get("kasms", []) if isinstance(payload, dict) else []
        else:
            payload = self._request("GET", "/api/session/sessions", basic=True)
            if isinstance(payload, dict):
                items = payload.get("sessions") or payload.get("data") or []
            else:
                items = payload or []
        return [self._to_session(item, mode) for item in items or [] if isinstance(item, dict)]

    def get_images(self) -> dict[str, Any]:
        """Return a mapping useful for matching sessions to registry entries.

        The result contains ``friendly_to_id`` and ``id_to_friendly``. Image
        listing is optional: failures are logged and return empty maps.
        """
        mode = self.detect_mode()
        images: list[dict[str, Any]] = []
        try:
            if mode == "legacy":
                payload = self._request(
                    "POST", "/api/public/get_images", json_body=self._legacy_payload()
                )
                images = payload.get("images", []) if isinstance(payload, dict) else []
            else:
                page = 0
                while True:
                    payload = self._request(
                        "GET",
                        "/api/admin/catalog/images",
                        basic=True,
                        params={"page": page, "page_size": 100},
                    )
                    if isinstance(payload, dict):
                        batch = payload.get("data") or payload.get("images") or []
                        total = payload.get("total")
                    else:
                        batch, total = payload, None
                    images.extend(batch)
                    if not batch or total is None or len(images) >= int(total):
                        break
                    page += 1
        except KasmApiError as exc:
            LOG.warning("could not list Kasm images (matching by name only): %s", exc)
            return {"friendly_to_id": {}, "id_to_friendly": {}}

        friendly_to_id: dict[str, str] = {}
        id_to_friendly: dict[str, str] = {}
        for image in images:
            if not isinstance(image, dict):
                continue
            image_id = image.get("image_id")
            friendly = image.get("friendly_name")
            if image_id and friendly:
                friendly_to_id.setdefault(normalize_name(friendly), str(image_id))
                id_to_friendly[str(image_id)] = str(friendly)
        LOG.info("Kasm image index: %s image(s)", len(id_to_friendly))
        return {"friendly_to_id": friendly_to_id, "id_to_friendly": id_to_friendly}

    @staticmethod
    def _to_session(item: dict[str, Any], mode: str) -> Session:
        image = item.get("image") if isinstance(item.get("image"), dict) else {}
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        session_id = (
            item.get("kasm_id")
            or item.get("session_id")
            or item.get("id")
            or ""
        )
        return Session(
            session_id=str(session_id),
            image_id=item.get("image_id") or image.get("image_id"),
            image_name=image.get("name") or item.get("image_name"),
            friendly_name=image.get("friendly_name") or item.get("friendly_name"),
            container_id=item.get("container_id"),
            container_ip=item.get("container_ip"),
            docker_network=item.get("docker_network"),
            state=str(
                item.get("operational_status")
                or item.get("observed_state")
                or item.get("status")
                or ""
            ).lower(),
            user_id=item.get("user_id") or user.get("user_id"),
            username=item.get("username") or user.get("username"),
            raw=item,
        )
