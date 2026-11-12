"""Unit/integration tests for the orchestrator using fake Docker and Kasm APIs."""

from __future__ import annotations

import json
from pathlib import Path

import docker
import pytest

from orchestrator.config import ConfigError, Settings
from orchestrator.docker_backend import BackendError, DockerBackend
from orchestrator.kasm_api import KasmApiError, Session, normalize_name
from orchestrator.reconciler import Reconciler


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeContainer:
    def __init__(self, name: str, labels: dict, status: str = "running") -> None:
        self.name = name
        self.labels = labels
        self.status = status
        self.stopped = False
        self.removed = False

    def reload(self) -> None:
        return None

    def stop(self, timeout: int = 5) -> None:
        self.stopped = True
        self.status = "exited"

    def remove(self, force: bool = False, v: bool = False) -> None:
        self.removed = True


class FakeVolumesCollection:
    def __init__(self) -> None:
        self.items: dict[str, object] = {}

    def get(self, name: str):
        if name not in self.items:
            raise docker.errors.NotFound("volume not found")
        return self.items[name]

    def create(self, name: str, labels: dict | None = None):
        volume = type("Volume", (), {})()
        volume.name = name
        volume.labels = labels or {}
        volume.removed = False
        volume.remove = lambda: setattr(volume, "removed", True)
        self.items[name] = volume
        return volume

    def list(self, filters: dict | None = None):
        labels = (filters or {}).get("label")
        result = []
        for volume in self.items.values():
            if not labels:
                result.append(volume)
                continue
            key, _, value = labels.partition("=")
            if volume.labels.get(key) == value:
                result.append(volume)
        return result


class FakeImages:
    def __init__(self, present: set[str] | None = None) -> None:
        self.present = set(present or ())
        self.pulled: list[str] = []
        self.pull_attempts = 0
        self.fail_pull = False

    def get(self, name: str):
        if name not in self.present:
            raise docker.errors.ImageNotFound(name)
        return object()

    def pull(self, name: str):
        self.pull_attempts += 1
        if self.fail_pull:
            raise docker.errors.APIError("pull failed")
        self.pulled.append(name)
        self.present.add(name)
        return object()


class FakeContainers:
    def __init__(self) -> None:
        self.by_name: dict[str, FakeContainer] = {}
        self.run_calls: list[dict] = []

    def get(self, name: str):
        if name not in self.by_name:
            raise docker.errors.NotFound("container not found")
        return self.by_name[name]

    def list(self, all: bool = False, filters: dict | None = None):
        labels = (filters or {}).get("label")
        result = []
        for container in self.by_name.values():
            if not labels:
                result.append(container)
                continue
            key, _, value = labels.partition("=")
            if container.labels.get(key) == value:
                result.append(container)
        return result

    def run(self, **kwargs):
        self.run_calls.append(kwargs)
        container = FakeContainer(
            kwargs["name"],
            kwargs.get("labels", {}),
            "running",
        )
        self.by_name[kwargs["name"]] = container
        return container


class FakeApi:
    def __init__(self) -> None:
        self.networks: dict[str, dict] = {}

    def inspect_container(self, container_id: str):
        if container_id not in self.networks:
            raise docker.errors.NotFound("container not found")
        return {"NetworkSettings": {"Networks": {name: {} for name in self.networks[container_id]}}}


class FakeDocker:
    def __init__(self, present_images: set[str] | None = None) -> None:
        self.containers = FakeContainers()
        self.images = FakeImages(present_images)
        self.volumes = FakeVolumesCollection()
        self.api = FakeApi()


class FakeKasm:
    def __init__(self, sessions: list[Session] | None = None) -> None:
        self.sessions = list(sessions or [])
        self.error: Exception | None = None
        self.images = {"friendly_to_id": {}, "id_to_friendly": {}}

    def get_sessions(self) -> list[Session]:
        if self.error:
            raise self.error
        return list(self.sessions)

    def get_images(self):
        return self.images


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_settings(tmp_path: Path, **overrides) -> Settings:
    defaults = dict(
        kasm_url="https://kasm.example.test",
        api_key="key",
        api_key_secret="secret",
        network="kasm_default_network",
        manifest_path=tmp_path / "backend_manifest.json",
        state_path=tmp_path / "state.json",
        poll_interval=1.0,
        retry_interval=30.0,
        request_timeout=5.0,
        log_level="DEBUG",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def make_app(
    slug: str = "nextcloud",
    friendly_name: str = "Teste: Nextcloud",
    *,
    image: str = "linuxserver/nextcloud:latest",
    port: int = 443,
    env_defaults: dict | None = None,
    volumes: list | None = None,
    required_env: list | None = None,
) -> dict:
    return {
        "slug": slug,
        "friendly_name": friendly_name,
        "image": image,
        "port": port,
        "env_defaults": env_defaults or {"PUID": "1000"},
        "required_env": required_env or [],
        "volumes": volumes or [],
        "privileged": False,
        "network_mode": "",
        "command": "",
    }


def make_session(
    session_id: str,
    friendly_name: str = "Teste: Nextcloud",
    *,
    image_id: str = "img-1",
    container_id: str | None = None,
    docker_network: str | None = None,
) -> Session:
    return Session(
        session_id=session_id,
        image_id=image_id,
        friendly_name=friendly_name,
        container_id=container_id,
        docker_network=docker_network,
        state="running",
    )


def make_reconciler(tmp_path: Path, kasm: FakeKasm, docker_client: FakeDocker, apps, **settings):
    settings = make_settings(tmp_path, **settings)
    backend = DockerBackend(docker_client, settings)
    return settings, backend, Reconciler(kasm, backend, apps, settings)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_starts_backend_for_active_session(tmp_path: Path) -> None:
    apps = {"nextcloud": make_app()}
    kasm = FakeKasm([make_session("s1")])
    docker_client = FakeDocker(present_images={"linuxserver/nextcloud:latest"})
    settings, backend, reconciler = make_reconciler(tmp_path, kasm, docker_client, apps)

    summary = reconciler.run_once()

    assert summary.started == 1
    assert summary.active_apps == 1
    assert summary.matched == 1
    container = docker_client.containers.by_name["nextcloud"]
    call = docker_client.containers.run_calls[0]
    assert call["name"] == "nextcloud"
    assert call["image"] == "linuxserver/nextcloud:latest"
    assert call["network"] == "kasm_default_network"
    assert call["environment"] == {"PUID": "1000"}
    assert call["restart_policy"]["Name"] == "no"
    assert call["labels"]["kasm-teste.app"] == "nextcloud"
    assert container.status == "running"
    state = json.loads(settings.state_path.read_text(encoding="utf-8"))
    assert state["active"] == {"nextcloud": ["s1"]}


def test_keeps_backend_while_another_session_exists(tmp_path: Path) -> None:
    apps = {"nextcloud": make_app()}
    kasm = FakeKasm([make_session("s1"), make_session("s2")])
    docker_client = FakeDocker(present_images={"linuxserver/nextcloud:latest"})
    _, backend, reconciler = make_reconciler(tmp_path, kasm, docker_client, apps)

    first = reconciler.run_once()
    assert first.started == 1
    assert len(docker_client.containers.by_name) == 1
    assert len(docker_client.containers.run_calls) == 1

    kasm.sessions = [make_session("s2")]
    second = reconciler.run_once()
    assert second.started == 0
    assert "nextcloud" in docker_client.containers.by_name
    assert not docker_client.containers.by_name["nextcloud"].removed


def test_removes_backend_when_last_session_ends(tmp_path: Path) -> None:
    apps = {"nextcloud": make_app()}
    kasm = FakeKasm([make_session("s1")])
    docker_client = FakeDocker(present_images={"linuxserver/nextcloud:latest"})
    _, backend, reconciler = make_reconciler(tmp_path, kasm, docker_client, apps)

    reconciler.run_once()
    container = docker_client.containers.by_name["nextcloud"]
    kasm.sessions = []
    summary = reconciler.run_once()

    assert summary.removed == 1
    assert container.stopped
    assert container.removed


def test_ignores_unrelated_sessions(tmp_path: Path) -> None:
    apps = {"nextcloud": make_app()}
    kasm = FakeKasm([make_session("s1", "Outro Workspace")])
    docker_client = FakeDocker()
    _, _, reconciler = make_reconciler(tmp_path, kasm, docker_client, apps)

    summary = reconciler.run_once()
    assert summary.matched == 0
    assert summary.started == 0
    assert not docker_client.containers.by_name


def test_api_error_keeps_existing_backend(tmp_path: Path) -> None:
    apps = {"nextcloud": make_app()}
    kasm = FakeKasm([make_session("s1")])
    docker_client = FakeDocker(present_images={"linuxserver/nextcloud:latest"})
    _, _, reconciler = make_reconciler(tmp_path, kasm, docker_client, apps)
    reconciler.run_once()
    container = docker_client.containers.by_name["nextcloud"]

    kasm.error = KasmApiError("down")
    with pytest.raises(KasmApiError):
        reconciler.run_once()
    assert not container.removed


def test_discovers_ephemeral_network_from_container(tmp_path: Path) -> None:
    apps = {"nextcloud": make_app()}
    kasm = FakeKasm([make_session("s1", container_id="cid-1")])
    docker_client = FakeDocker(present_images={"linuxserver/nextcloud:latest"})
    docker_client.api.networks["cid-1"] = ["bridge", "kasm_default_network", "kasm_sidecar_network"]
    settings, _, reconciler = make_reconciler(
        tmp_path, kasm, docker_client, apps, network="kasm_default_network"
    )

    reconciler.run_once()
    assert docker_client.containers.run_calls[0]["network"] == "kasm_default_network"


def test_uses_session_docker_network_when_present(tmp_path: Path) -> None:
    apps = {"nextcloud": make_app()}
    kasm = FakeKasm([make_session("s1", docker_network="session-net-1")])
    docker_client = FakeDocker(present_images={"linuxserver/nextcloud:latest"})
    _, _, reconciler = make_reconciler(tmp_path, kasm, docker_client, apps)

    reconciler.run_once()
    assert docker_client.containers.run_calls[0]["network"] == "session-net-1"


def test_retry_backoff_after_pull_failure(tmp_path: Path) -> None:
    apps = {"nextcloud": make_app()}
    kasm = FakeKasm([make_session("s1")])
    docker_client = FakeDocker()
    docker_client.images.fail_pull = True
    clock = [0.0]
    settings = make_settings(tmp_path)
    backend = DockerBackend(docker_client, settings)
    reconciler = Reconciler(kasm, backend, apps, settings, clock=lambda: clock[0])

    first = reconciler.run_once()
    assert first.started == 0
    assert docker_client.images.pull_attempts == 1

    clock[0] = 5.0
    reconciler.run_once()
    assert docker_client.images.pull_attempts == 1  # still inside backoff

    clock[0] = 31.0
    reconciler.run_once()
    # Once the backoff expires the pull is attempted again.
    assert docker_client.images.pull_attempts == 2


def test_refuses_unmanaged_container_name(tmp_path: Path) -> None:
    apps = {"nextcloud": make_app()}
    kasm = FakeKasm([make_session("s1")])
    docker_client = FakeDocker(present_images={"linuxserver/nextcloud:latest"})
    docker_client.containers.by_name["nextcloud"] = FakeContainer("nextcloud", {})
    settings = make_settings(tmp_path)
    backend = DockerBackend(docker_client, settings)

    with pytest.raises(BackendError):
        backend.ensure_started(apps["nextcloud"], "kasm_default_network")
    assert not docker_client.containers.by_name["nextcloud"].removed


def test_cleanup_orphans_on_first_run(tmp_path: Path) -> None:
    apps = {"nextcloud": make_app()}
    kasm = FakeKasm([])
    docker_client = FakeDocker()
    labels = {"kasm-teste.managed": "true", "kasm-teste.app": "oldapp"}
    orphan = FakeContainer("oldapp", labels, "exited")
    docker_client.containers.by_name["oldapp"] = orphan
    _, _, reconciler = make_reconciler(tmp_path, kasm, docker_client, apps)

    summary = reconciler.run_once()
    assert summary.orphans >= 1
    assert orphan.removed


def test_creates_and_removes_named_volumes(tmp_path: Path) -> None:
    apps = {
        "nextcloud": make_app(
            volumes=[{"container": "/config", "bind": "/srv/config", "readonly": False}]
        )
    }
    kasm = FakeKasm([make_session("s1")])
    docker_client = FakeDocker(present_images={"linuxserver/nextcloud:latest"})
    settings, backend, reconciler = make_reconciler(tmp_path, kasm, docker_client, apps)

    reconciler.run_once()
    call = docker_client.containers.run_calls[0]
    volume_name, mapping = next(
        (name, value)
        for name, value in call["volumes"].items()
        if value["bind"] == "/config"
    )
    assert mapping["mode"] == "rw"
    assert volume_name.startswith("kasm-teste-vol-nextcloud-")
    assert volume_name in docker_client.volumes.items

    kasm.sessions = []
    reconciler.run_once()
    assert docker_client.volumes.items[volume_name].removed


def test_dry_run_does_not_touch_docker(tmp_path: Path) -> None:
    apps = {"nextcloud": make_app()}
    kasm = FakeKasm([make_session("s1")])
    docker_client = FakeDocker(present_images={"linuxserver/nextcloud:latest"})
    settings, backend, reconciler = make_reconciler(
        tmp_path, kasm, docker_client, apps, dry_run=True
    )

    summary = reconciler.run_once()
    assert summary.active_apps == 1
    assert not docker_client.containers.by_name
    assert not docker_client.containers.run_calls


def test_image_index_matches_by_id_even_if_name_changed(tmp_path: Path) -> None:
    apps = {"nextcloud": make_app()}
    kasm = FakeKasm([make_session("s1", "Nome alterado no Kasm", image_id="img-42")])
    kasm.images = {
        "friendly_to_id": {normalize_name("Teste: Nextcloud"): "img-42"},
        "id_to_friendly": {"img-42": "Teste: Nextcloud"},
    }
    docker_client = FakeDocker(present_images={"linuxserver/nextcloud:latest"})
    _, _, reconciler = make_reconciler(tmp_path, kasm, docker_client, apps)
    reconciler.refresh_image_index()

    summary = reconciler.run_once()
    assert summary.matched == 1
    assert summary.started == 1


def test_config_requires_credentials() -> None:
    with pytest.raises(ConfigError):
        Settings.from_env({})
    settings = Settings.from_env(
        {
            "KASM_URL": "https://kasm.example/",
            "KASM_API_KEY": "k",
            "KASM_API_KEY_SECRET": "s",
            "POLL_INTERVAL": "2",
            "DOCKER_DISCOVER_NETWORK": "false",
        }
    )
    assert settings.kasm_url == "https://kasm.example"
    assert settings.poll_interval == 2
    assert settings.discover_network is False
