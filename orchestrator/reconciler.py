"""Reconciliation loop between Kasm sessions and Docker backend containers."""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .docker_backend import DockerBackend
from .kasm_api import KasmApiError, KasmClient, Session, normalize_name

LOG = logging.getLogger("orchestrator.reconciler")


@dataclass
class RunSummary:
    sessions: int
    matched: int
    active_apps: int
    started: int
    removed: int
    orphans: int

    def as_dict(self) -> dict[str, int]:
        return {
            "sessions": self.sessions,
            "matched": self.matched,
            "active_apps": self.active_apps,
            "started": self.started,
            "removed": self.removed,
            "orphans": self.orphans,
        }


class Reconciler:
    def __init__(
        self,
        kasm: KasmClient,
        backend: DockerBackend,
        apps: dict[str, dict[str, Any]],
        settings: Settings,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.kasm = kasm
        self.backend = backend
        self.settings = settings
        self.apps = apps
        self.apps_by_friendly = {
            normalize_name(app.get("friendly_name")): app for app in apps.values()
        }
        self.id_to_app: dict[str, dict[str, Any]] = {}
        self._retry_after: dict[str, float] = {}
        self._clock = clock
        self._lock = threading.Lock()
        self._first_run = True

    # -- matching ----------------------------------------------------------
    def refresh_image_index(self) -> None:
        info = self.kasm.get_images()
        friendly_to_id = info.get("friendly_to_id", {})
        self.id_to_app = {}
        for app in self.apps.values():
            image_id = friendly_to_id.get(normalize_name(app.get("friendly_name")))
            if image_id:
                self.id_to_app[str(image_id)] = app
        LOG.info(
            "matched %s/%s registry workspaces to Kasm image ids",
            len(self.id_to_app),
            len(self.apps),
        )

    def match_session(self, session: Session) -> dict[str, Any] | None:
        if session.image_id and session.image_id in self.id_to_app:
            return self.id_to_app[session.image_id]
        return self.apps_by_friendly.get(normalize_name(session.friendly_name))

    # -- one reconciliation pass ------------------------------------------
    def run_once(self) -> RunSummary:
        with self._lock:
            try:
                sessions = self.kasm.get_sessions()
            except KasmApiError:
                raise

            active: dict[str, set[str]] = {}
            networks: dict[str, str] = {}
            matched = 0
            for session in sessions:
                app = self.match_session(session)
                if app is None:
                    continue
                matched += 1
                slug = app["slug"]
                active.setdefault(slug, set()).add(session.session_id)
                networks.setdefault(slug, self.backend.resolve_network(session))

            started = 0
            for slug, session_ids in active.items():
                app = self.apps[slug]
                now = self._clock()
                retry_at = self._retry_after.get(slug)
                if retry_at is not None and retry_at > now:
                    LOG.debug(
                        "backend %s still in retry backoff (%.1fs left)",
                        slug,
                        retry_at - now,
                    )
                    continue
                try:
                    before = self.backend.get_container(slug)
                    container = self.backend.ensure_started(app, networks.get(slug))
                    if container is not None and (
                        before is None or getattr(before, "status", "") != "running"
                    ):
                        started += 1
                    self._retry_after.pop(slug, None)
                    LOG.debug("backend %s active for %s session(s)", slug, len(session_ids))
                except Exception as exc:  # noqa: BLE001 - one app must not kill the loop
                    self._retry_after[slug] = now + self.settings.retry_interval
                    LOG.error(
                        "failed to start backend %s; retrying in %.0fs: %s",
                        slug,
                        self.settings.retry_interval,
                        exc,
                    )

            removed = 0
            for container in self.backend.list_managed():
                slug = self.backend.app_of(container)
                if slug and slug not in active:
                    if self.backend.stop_and_remove(slug):
                        removed += 1

            orphans = 0
            if self._first_run:
                orphans = self.backend.cleanup_orphans(set(active))
                self._first_run = False

            self._write_state(active, networks)
            return RunSummary(
                sessions=len(sessions),
                matched=matched,
                active_apps=len(active),
                started=started,
                removed=removed,
                orphans=orphans,
            )

    # -- persistence -------------------------------------------------------
    def _write_state(self, active: dict[str, set[str]], networks: dict[str, str]) -> None:
        path = Path(self.settings.state_path)
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "active": {slug: sorted(ids) for slug, ids in sorted(active.items())},
            "networks": dict(sorted(networks.items())),
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            temporary.replace(path)
        except OSError as exc:
            LOG.warning("could not write state file %s: %s", path, exc)

    # -- loop --------------------------------------------------------------
    def run_forever(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            started_at = time.monotonic()
            try:
                summary = self.run_once()
                LOG.info("reconcile: %s", summary.as_dict())
            except KasmApiError as exc:
                LOG.error("Kasm API unavailable (%s); keeping current backends", exc)
            except Exception:  # noqa: BLE001 - a bad pass must not stop the service
                LOG.exception("unexpected error during reconciliation")
            elapsed = time.monotonic() - started_at
            stop_event.wait(max(1.0, self.settings.poll_interval - elapsed))
