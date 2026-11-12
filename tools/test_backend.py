"""Smoke test: start a real backend on the Kasm Docker network and resolve it.

Run on the Kasm Docker host as root (or a user in the docker group):

    sudo -S /home/deepseek/kasm-venv/bin/python /tmp/test_backend.py

It creates a temporary container named ``kasm-orch-test`` on
``kasm_default_network``, then starts a second container on the same network
and fetches http://kasm-orch-test/ to prove Docker DNS works. Both containers
(and the test volume) are removed afterwards.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time

import docker

sys.path.insert(0, "/home/deepseek/kasm-test")

from orchestrator.config import Settings
from orchestrator.docker_backend import DockerBackend

APP = {
    "slug": "kasm-orch-test",
    "friendly_name": "Teste: backend smoke",
    "image": "nginx:alpine",
    "port": 80,
    "env_defaults": {},
    "required_env": [],
    "volumes": [{"container": "/data", "bind": "/tmp/unused", "readonly": False}],
    "privileged": False,
    "network_mode": "",
    "command": "",
}


def main() -> int:
    if shutil.which("docker") is None:
        print("docker CLI not found")
        return 1
    settings = Settings(
        kasm_url="https://unused.example",
        api_key="unused",
        api_key_secret="unused",
        network="kasm_default_network",
        state_path="/tmp/kasm-orchestrator-state.json",
        log_level="INFO",
    )
    client = docker.from_env(timeout=180)
    backend = DockerBackend(client, settings)
    try:
        container = backend.ensure_started(APP, "kasm_default_network")
        container.reload()
        print("backend status:", container.status)
        print("backend id:", container.id[:12])
        networks = list(container.attrs["NetworkSettings"]["Networks"])
        print("backend networks:", networks)
        if "kasm_default_network" not in networks:
            print("FAIL: backend is not on kasm_default_network")
            return 1

        time.sleep(2)
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "kasm_default_network",
                "curlimages/curl:latest",
                "-sS",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                "http://kasm-orch-test/",
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        print("curl exit:", result.returncode, "status:", result.stdout.strip())
        print("curl stderr:", result.stderr.strip()[:300])
        if result.returncode != 0 or result.stdout.strip() not in ("200", "301", "302"):
            print("FAIL: backend not reachable by DNS")
            return 1
        print("OK: http://kasm-orch-test/ resolved and answered")
        return 0
    finally:
        try:
            backend.stop_and_remove("kasm-orch-test")
            print("cleanup done")
        except Exception as exc:  # noqa: BLE001
            print("cleanup error:", exc)


if __name__ == "__main__":
    raise SystemExit(main())
