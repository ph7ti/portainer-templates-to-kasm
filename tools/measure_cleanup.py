"""Measure session/backend/volume lifecycle timings for one Kasm workspace.

Run on the Kasm host before clicking the workspace (with --wait-session) or
right after closing it. Uses the legacy Kasm API plus the docker CLI.

    KASM_URL=... KASM_API_KEY=... KASM_API_KEY_SECRET=... \
      python tools/measure_cleanup.py --friendly "Teste: Gitea" --slug gitea \
        --wait-session --timeout 300
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def normalized(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def kasm_config() -> tuple[str, str, str]:
    url = os.environ.get("KASM_URL", "").rstrip("/")
    key = os.environ.get("KASM_API_KEY", "")
    secret = os.environ.get("KASM_API_KEY_SECRET", "")
    if not all((url, key, secret)):
        raise SystemExit("set KASM_URL, KASM_API_KEY and KASM_API_KEY_SECRET")
    return url, key, secret


def fetch_sessions(url: str, key: str, secret: str) -> list[dict]:
    response = requests.post(
        url + "/api/public/get_kasms",
        json={"api_key": key, "api_key_secret": secret},
        verify=False,
        timeout=20,
    )
    response.raise_for_status()
    return response.json().get("kasms", [])


def docker_inspect(container: str) -> dict | None:
    result = subprocess.run(
        ["docker", "inspect", container], capture_output=True, text=True
    )
    if result.returncode != 0:
        return None
    data = json.loads(result.stdout)
    return data[0] if data else None


def volumes_for(slug: str) -> list[str]:
    result = subprocess.run(
        ["docker", "volume", "ls", "--filter", f"label=kasm-teste.app={slug}", "-q"],
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--friendly", default="Teste: Nextcloud")
    parser.add_argument("--slug", default="nextcloud")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--wait-session", action="store_true")
    parser.add_argument("--poll", type=float, default=1.0)
    args = parser.parse_args()
    wanted = normalized(args.friendly)

    url, key, secret = kasm_config()
    start = time.time()
    session_seen = container_seen = session_gone = container_gone = volume_gone = None
    seen_any = False
    deadline = start + args.timeout

    print(f"start {time.strftime('%H:%M:%S')} friendly={args.friendly!r} slug={args.slug}")
    while time.time() < deadline:
        now = time.time()
        try:
            sessions = fetch_sessions(url, key, secret)
        except Exception as exc:  # noqa: BLE001
            print("api error:", exc)
            time.sleep(args.poll)
            continue
        matching = [
            item
            for item in sessions
            if normalized((item.get("image") or {}).get("friendly_name")) == wanted
        ]
        info = docker_inspect(args.slug)
        if matching and session_seen is None:
            session_seen = now - start
            seen_any = True
            print(f"session seen at +{session_seen:.1f}s")
        if info and container_seen is None:
            container_seen = now - start
            print(
                f"container seen at +{container_seen:.1f}s "
                f"(started={info.get('State', {}).get('StartedAt')})"
            )
        if seen_any and not matching and session_gone is None:
            session_gone = now - start
            print(f"session gone at +{session_gone:.1f}s")
        if container_seen is not None and info is None and container_gone is None:
            container_gone = now - start
            print(f"container gone at +{container_gone:.1f}s")
        volumes = volumes_for(args.slug)
        if (
            container_gone is not None
            and container_seen is not None
            and not volumes
            and volume_gone is None
        ):
            volume_gone = now - start
            print(f"volumes gone at +{volume_gone:.1f}s")
        print(
            f"+{now-start:6.1f}s sessions={len(sessions)} matching={len(matching)} "
            f"container={'yes' if info else 'no'} volumes={len(volumes)}"
        )
        if session_gone is not None and container_gone is not None and not volumes:
            break
        if not args.wait_session and not seen_any and now - start > 20:
            print("no matching session and --wait-session not set; giving up")
            break
        time.sleep(args.poll)

    print("\nSUMMARY")
    print("session_seen_seconds:", session_seen)
    print("container_seen_seconds:", container_seen)
    print("session_gone_seconds:", session_gone)
    print("container_gone_seconds:", container_gone)
    print("volume_gone_seconds:", volume_gone)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
