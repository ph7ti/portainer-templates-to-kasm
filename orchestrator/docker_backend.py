"""Docker lifecycle for the per-application backend containers."""

from __future__ import annotations

import logging
from typing import Any

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound

from .config import Settings
from .kasm_api import Session

LOG = logging.getLogger("orchestrator.docker")

LABEL_MANAGED = "kasm-teste.managed"
LABEL_APP = "kasm-teste.app"
LABEL_IMAGE = "kasm-teste.image"
LABEL_PORT = "kasm-teste.port"
LABEL_VOLUME = "kasm-teste.volume"


class BackendError(RuntimeError):
    pass


class DockerBackend:
    def __init__(self, client: Any, settings: Settings) -> None:
        self.client = client
        self.settings = settings
        self._network_cache: dict[str, str] = {}

    # -- queries -----------------------------------------------------------
    def get_container(self, slug: str):
        try:
            return self.client.containers.get(slug)
        except NotFound:
            return None

    def list_managed(self) -> list[Any]:
        try:
            return self.client.containers.list(
                all=True, filters={"label": f"{LABEL_MANAGED}=true"}
            )
        except DockerException as exc:
            LOG.warning("could not list managed containers: %s", exc)
            return []

    @staticmethod
    def app_of(container: Any) -> str | None:
        labels = getattr(container, "labels", None) or {}
        return labels.get(LABEL_APP)

    # -- network discovery -------------------------------------------------
    def resolve_network(self, session: Session) -> str:
        if session.docker_network:
            return session.docker_network
        if not self.settings.discover_network or not session.container_id:
            return self.settings.network
        cached = self._network_cache.get(session.session_id)
        if cached:
            return cached
        try:
            data = self.client.api.inspect_container(session.container_id)
        except (NotFound, DockerException) as exc:
            LOG.debug("could not inspect session %s container: %s", session.session_id, exc)
            return self.settings.network

        networks = list(((data or {}).get("NetworkSettings") or {}).get("Networks") or {})
        filtered = [name for name in networks if name not in ("bridge", "host", "none")]
        if self.settings.network in filtered:
            chosen = self.settings.network
        else:
            kasm_networks = [
                name
                for name in filtered
                if name.startswith("kasm") and "sidecar" not in name
            ]
            chosen = (kasm_networks or filtered or [self.settings.network])[0]
        self._network_cache[session.session_id] = chosen
        LOG.info(
            "session %s container network: %s (networks=%s)",
            session.session_id,
            chosen,
            ",".join(networks),
        )
        return chosen

    # -- lifecycle ---------------------------------------------------------
    def ensure_started(self, app: dict[str, Any], network: str | None = None):
        slug = app["slug"]
        network = network or self.settings.network
        existing = self.get_container(slug)
        if existing is not None:
            try:
                existing.reload()
            except (NotFound, DockerException):
                existing = None
            if existing is not None:
                labels = getattr(existing, "labels", None) or {}
                if labels.get(LABEL_MANAGED) != "true" or labels.get(LABEL_APP) != slug:
                    raise BackendError(
                        f"container name {slug!r} is already used by a container "
                        "not managed by this orchestrator; refusing to touch it"
                    )
            if existing is not None and existing.status == "running":
                return existing
            if existing is not None:
                LOG.info("removing stopped backend %s before recreating", slug)
                self._remove_container(existing)
                self._remove_volumes(slug)

        if self.settings.dry_run:
            LOG.info("[dry-run] would start backend %s from %s on %s", slug, app["image"], network)
            return None

        self._ensure_image(app["image"])
        volumes = self._ensure_volumes(app)
        env = dict(app.get("env_defaults") or {})
        missing = [name for name in app.get("required_env") or [] if name not in env]
        if missing:
            LOG.warning(
                "backend %s has required env vars without defaults: %s",
                slug,
                ", ".join(missing),
            )

        labels = {
            LABEL_MANAGED: "true",
            LABEL_APP: slug,
            LABEL_IMAGE: app["image"],
            LABEL_PORT: str(app.get("port") or ""),
        }
        kwargs: dict[str, Any] = {
            "image": app["image"],
            "name": slug,
            "detach": True,
            "environment": env,
            "volumes": volumes,
            "restart_policy": {"Name": "no", "MaximumRetryCount": 0},
            "labels": labels,
            "privileged": bool(app.get("privileged")),
        }
        if app.get("command"):
            kwargs["command"] = app["command"]
        if str(app.get("network_mode") or "").lower() == "host":
            LOG.warning("backend %s uses host networking; the browser cannot resolve it", slug)
            kwargs["network_mode"] = "host"
        else:
            kwargs["network"] = network

        try:
            container = self.client.containers.run(**kwargs)
        except (ImageNotFound, APIError, DockerException) as exc:
            raise BackendError(f"could not start {slug} ({app['image']}): {exc}") from exc
        LOG.info(
            "started backend %s (%s) on network %s target http://%s:%s",
            slug,
            app["image"],
            network,
            slug,
            app.get("port"),
        )
        return container

    def _ensure_image(self, image: str) -> None:
        try:
            self.client.images.get(image)
            return
        except ImageNotFound:
            pass
        if not self.settings.pull_images:
            raise BackendError(f"image {image} is not present and PULL_IMAGES is false")
        LOG.info("pulling image %s (this may take a while)", image)
        try:
            self.client.images.pull(image)
        except (APIError, DockerException) as exc:
            raise BackendError(f"could not pull {image}: {exc}") from exc

    def _ensure_volumes(self, app: dict[str, Any]) -> dict[str, Any]:
        mounts: dict[str, Any] = {}
        slug = app["slug"]
        for index, volume in enumerate(app.get("volumes") or []):
            name = f"{self.settings.volume_prefix}{slug}-{index}"
            try:
                self.client.volumes.get(name)
            except NotFound:
                try:
                    self.client.volumes.create(
                        name=name,
                        labels={LABEL_MANAGED: "true", LABEL_APP: slug, LABEL_VOLUME: "true"},
                    )
                except (APIError, DockerException) as exc:
                    raise BackendError(f"could not create volume {name}: {exc}") from exc
            # docker-py expects {volume_name_or_host_path: {"bind": container_path, ...}}
            mounts[name] = {
                "bind": volume["container"],
                "mode": "ro" if volume.get("readonly") else "rw",
            }
        return mounts

    def stop_and_remove(self, slug: str) -> bool:
        container = self.get_container(slug)
        if container is None:
            self._remove_volumes(slug)
            return False
        labels = getattr(container, "labels", None) or {}
        if labels.get(LABEL_MANAGED) != "true" or labels.get(LABEL_APP) != slug:
            LOG.warning("refusing to remove unmanaged container named %s", slug)
            return False
        if self.settings.dry_run:
            LOG.info("[dry-run] would remove backend %s", slug)
            return True
        self._remove_container(container)
        self._remove_volumes(slug)
        LOG.info("removed backend %s", slug)
        return True

    def _remove_container(self, container: Any) -> None:
        try:
            container.stop(timeout=5)
        except (NotFound, APIError, DockerException) as exc:
            LOG.debug("stop failed for %s: %s", getattr(container, "name", "?"), exc)
        try:
            container.remove(force=True, v=True)
        except (NotFound, APIError, DockerException) as exc:
            LOG.warning("could not remove container %s: %s", getattr(container, "name", "?"), exc)

    def _remove_volumes(self, slug: str) -> None:
        if self.settings.dry_run:
            return
        try:
            volumes = self.client.volumes.list(
                filters={"label": f"{LABEL_APP}={slug}"}
            )
        except (APIError, DockerException) as exc:
            LOG.warning("could not list volumes for %s: %s", slug, exc)
            return
        for volume in volumes:
            try:
                volume.remove()
            except (NotFound, APIError, DockerException) as exc:
                LOG.warning("could not remove volume %s: %s", getattr(volume, "name", "?"), exc)

    def cleanup_orphans(self, active_slugs: set[str]) -> int:
        removed = 0
        if self.settings.dry_run:
            candidates = [
                self.app_of(container) for container in self.list_managed()
            ]
            LOG.info("[dry-run] would clean orphan backends: %s", candidates)
            return 0
        for container in self.list_managed():
            slug = self.app_of(container)
            if slug and slug not in active_slugs:
                LOG.info("removing orphan backend %s", slug)
                self._remove_container(container)
                self._remove_volumes(slug)
                removed += 1
        try:
            volumes = self.client.volumes.list(
                filters={"label": f"{LABEL_MANAGED}=true"}
            )
        except (APIError, DockerException):
            volumes = []
        for volume in volumes:
            labels = getattr(volume, "labels", None) or {}
            if labels.get(LABEL_APP) not in active_slugs:
                try:
                    volume.remove()
                    removed += 1
                except (NotFound, APIError, DockerException):
                    pass
        return removed
