#!/usr/bin/env python3
"""Kasm backend orchestrator.

Polls the Kasm API and keeps one Docker backend container per registry
workspace alive while that workspace has active sessions. Run once with
``--once`` for a reconciliation pass, or continuously as a service.

Required environment variables:
    KASM_URL, KASM_API_KEY, KASM_API_KEY_SECRET

Optional: POLL_INTERVAL, DOCKER_NETWORK, MANIFEST_PATH, STATE_PATH, DRY_RUN.
See ``.env.example`` and ``README.md``.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import signal
import sys
import threading
from pathlib import Path

import docker

from .config import ConfigError, Settings
from .docker_backend import DockerBackend
from .kasm_api import KasmApiError, KasmClient
from .reconciler import Reconciler

LOG = logging.getLogger("orchestrator")


def load_manifest(path: Path) -> dict[str, dict]:
    if not path.is_file():
        raise ConfigError(f"backend manifest not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    apps = data.get("apps")
    if not isinstance(apps, dict) or not apps:
        raise ConfigError(f"invalid backend manifest (missing apps): {path}")
    for slug, app in apps.items():
        if not isinstance(app, dict) or not app.get("image") or not app.get("port"):
            raise ConfigError(f"invalid app entry {slug!r} in {path}")
    return apps


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Kasm backend orchestrator")
    parser.add_argument("--once", action="store_true", help="run one reconciliation and exit")
    parser.add_argument("--dry-run", action="store_true", help="log actions without changing Docker")
    parser.add_argument("--manifest", help="override MANIFEST_PATH")
    parser.add_argument("--log-level", help="override LOG_LEVEL")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    if args.manifest:
        settings = dataclasses.replace(settings, manifest_path=Path(args.manifest))
    if args.log_level:
        settings = dataclasses.replace(settings, log_level=args.log_level.upper())
    if args.dry_run:
        settings = dataclasses.replace(settings, dry_run=True)

    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    if not settings.verify_tls:
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    try:
        apps = load_manifest(settings.manifest_path)
    except (ConfigError, json.JSONDecodeError) as exc:
        LOG.error("%s", exc)
        return 2

    LOG.info(
        "starting orchestrator: kasm=%s network=%s apps=%s dry_run=%s",
        settings.kasm_url,
        settings.network,
        len(apps),
        settings.dry_run,
    )
    kasm = KasmClient(
        settings.kasm_url,
        settings.api_key,
        settings.api_key_secret,
        mode=settings.api_mode,
        verify_tls=settings.verify_tls,
        timeout=settings.request_timeout,
    )
    try:
        kasm.detect_mode()
    except KasmApiError as exc:
        LOG.error("cannot authenticate against Kasm: %s", exc)
        return 3

    try:
        docker_client = docker.from_env(timeout=settings.docker_timeout)
        docker_client.ping()
    except Exception as exc:  # noqa: BLE001 - docker-py raises several classes
        LOG.error("cannot connect to the Docker daemon: %s", exc)
        return 4

    backend = DockerBackend(docker_client, settings)
    reconciler = Reconciler(kasm, backend, apps, settings)
    try:
        reconciler.refresh_image_index()
    except KasmApiError as exc:
        LOG.warning("image index unavailable; matching sessions by friendly name: %s", exc)

    if args.once:
        try:
            summary = reconciler.run_once()
        except KasmApiError as exc:
            LOG.error("reconciliation failed: %s", exc)
            return 1
        LOG.info("reconcile: %s", summary.as_dict())
        return 0

    stop_event = threading.Event()

    def request_stop(signum, _frame):  # noqa: ANN001
        LOG.info("received signal %s; stopping", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    reconciler.run_forever(stop_event)
    LOG.info("orchestrator stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
