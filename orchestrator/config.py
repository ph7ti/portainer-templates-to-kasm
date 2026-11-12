"""Runtime configuration for the Kasm backend orchestrator."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigError(RuntimeError):
    pass


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on", "sim", "y")


def _int(value: str | None, default: int) -> int:
    if value is None or not value.strip():
        return default
    return int(value)


def _float(value: str | None, default: float) -> float:
    if value is None or not value.strip():
        return default
    return float(value)


@dataclass(frozen=True)
class Settings:
    kasm_url: str
    api_key: str
    api_key_secret: str
    api_mode: str = "auto"
    verify_tls: bool = True
    request_timeout: float = 20.0
    poll_interval: float = 5.0
    retry_interval: float = 30.0
    network: str = "kasm_default_network"
    discover_network: bool = True
    manifest_path: Path = Path("registry/backend_manifest.json")
    state_path: Path = Path(".state/orchestrator.json")
    log_level: str = "INFO"
    dry_run: bool = False
    pull_images: bool = True
    docker_timeout: int = 180
    volume_prefix: str = "kasm-teste-vol-"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        values = dict(os.environ if env is None else env)
        url = (values.get("KASM_URL") or "").strip().rstrip("/")
        api_key = (values.get("KASM_API_KEY") or "").strip()
        api_secret = (values.get("KASM_API_KEY_SECRET") or "").strip()
        missing = [
            name
            for name, value in (
                ("KASM_URL", url),
                ("KASM_API_KEY", api_key),
                ("KASM_API_KEY_SECRET", api_secret),
            )
            if not value
        ]
        if missing:
            raise ConfigError("missing required environment variables: " + ", ".join(missing))

        mode = (values.get("KASM_API_MODE") or "auto").strip().lower()
        if mode not in ("auto", "legacy", "v2"):
            raise ConfigError("KASM_API_MODE must be auto, legacy or v2")

        return cls(
            kasm_url=url,
            api_key=api_key,
            api_key_secret=api_secret,
            api_mode=mode,
            verify_tls=_bool(values.get("KASM_VERIFY_TLS"), True),
            request_timeout=_float(values.get("KASM_REQUEST_TIMEOUT"), 20.0),
            poll_interval=max(1.0, _float(values.get("POLL_INTERVAL"), 5.0)),
            retry_interval=max(1.0, _float(values.get("RETRY_INTERVAL"), 30.0)),
            network=(values.get("DOCKER_NETWORK") or "kasm_default_network").strip(),
            discover_network=_bool(values.get("DOCKER_DISCOVER_NETWORK"), True),
            manifest_path=Path(
                values.get("MANIFEST_PATH") or "registry/backend_manifest.json"
            ),
            state_path=Path(values.get("STATE_PATH") or ".state/orchestrator.json"),
            log_level=(values.get("LOG_LEVEL") or "INFO").upper(),
            dry_run=_bool(values.get("DRY_RUN"), False),
            pull_images=_bool(values.get("PULL_IMAGES"), True),
            docker_timeout=_int(values.get("DOCKER_TIMEOUT"), 180),
            volume_prefix=(values.get("VOLUME_PREFIX") or "kasm-teste-vol-").strip(),
        )
