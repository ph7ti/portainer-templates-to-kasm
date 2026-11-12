"""Create Kasm image entries for every workspace in the generated registry.

Kasm's registry import exposes the workspaces but does not install them as
launchable images automatically in this build. This helper reads the local
registry listing and calls the legacy Dev API ``/api/public/create_image`` for
each missing friendly_name. It is idempotent and safe to re-run.

Usage:
    python tools/publish_images_to_kasm.py [--dry-run] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / ".secrets.json"
LISTING = ROOT / "registry" / "1.1" / "list.json"


def post(base: str, path: str, body: dict, ctx, timeout: float = 90):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "kasm-publish-images"},
        method="POST",
    )
    with urllib.request.urlopen(request, context=ctx, timeout=timeout) as response:
        return json.loads(response.read() or b"{}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--secrets", default=str(SECRETS))
    parser.add_argument(
        "--update",
        action="store_true",
        help="update existing images when run_config/categories differ",
    )
    args = parser.parse_args()

    secrets_path = Path(args.secrets)
    if secrets_path.is_file():
        secrets = json.loads(secrets_path.read_text(encoding="utf-8"))
    else:
        secrets = {
            "kasm_url": os.environ.get("KASM_URL", ""),
            "api_key": os.environ.get("KASM_API_KEY", ""),
            "api_key_secret": os.environ.get("KASM_API_KEY_SECRET", ""),
        }
        if not all(secrets.values()):
            print(
                "provide --secrets or KASM_URL/KASM_API_KEY/KASM_API_KEY_SECRET",
                file=sys.stderr,
            )
            return 2
    listing = json.loads(LISTING.read_text(encoding="utf-8"))
    base = secrets["kasm_url"].rstrip("/")
    auth = {
        "api_key": secrets["api_key"],
        "api_key_secret": secrets["api_key_secret"],
    }
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    existing = {
        image.get("friendly_name"): image
        for image in post(base, "/api/public/get_images", auth, ctx).get("images", [])
    }
    print(f"existing images: {len(existing)}")

    registry_url = listing["list_url"].rstrip("/") + "/"
    workspaces = listing["workspaces"]
    if args.limit:
        workspaces = workspaces[: args.limit]

    def target_payload(entry: dict) -> dict:
        return {
            "friendly_name": entry["friendly_name"],
            "description": entry["description"],
            "image_src": f"{registry_url}1.1/icons/{entry['image_src']}",
            "categories": "\n".join(entry.get("categories") or []),
            "name": entry["compatibility"][0]["image"],
            "docker_registry": "https://index.docker.io/v1/",
            "enabled": True,
            "cores": 2,
            "memory_bytes": 3 * 1024 * 1024 * 1024,
            "gpu_count": 0,
            "cpu_allocation_method": "Inherit",
            "run_config": json.dumps(entry.get("run_config") or {}),
            "exec_config": json.dumps({}),
            "volume_mappings": json.dumps({}),
            "notes": entry.get("notes") or "",
            "restrict_to_network": False,
            "restrict_network_names": [],
            "restrict_to_server": False,
            "uncompressed_size_mb": 2500,
            "x_res": 1920,
            "y_res": 1080,
        }

    created = updated = skipped = failed = 0
    for index, entry in enumerate(workspaces, start=1):
        name = entry["friendly_name"]
        target = target_payload(entry)
        current = existing.get(name)
        if current is not None:
            current_run = current.get("run_config") or {}
            wanted_run = entry.get("run_config") or {}
            current_cats = current.get("categories") or []
            wanted_cats = entry.get("categories") or []
            if not args.update or (current_run == wanted_run and current_cats == wanted_cats):
                skipped += 1
                continue
            target["image_id"] = current["image_id"]
            endpoint = "/api/public/update_image"
            action = "update"
        else:
            endpoint = "/api/public/create_image"
            action = "create"

        if args.dry_run:
            print(f"[dry-run] {action} {name}")
            if action == "create":
                created += 1
            else:
                updated += 1
            continue
        body = dict(auth)
        body["target_image"] = target
        for attempt in (1, 2):
            try:
                result = post(base, endpoint, body, ctx)
                if result.get("error_message"):
                    raise RuntimeError(result["error_message"])
                if action == "create":
                    created += 1
                else:
                    updated += 1
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 2:
                    failed += 1
                    print(f"FAILED {action} {name}: {exc}")
                else:
                    time.sleep(2)
        if index % 25 == 0:
            print(
                f"{index}/{len(workspaces)} created={created} updated={updated} "
                f"skipped={skipped} failed={failed}"
            )
        if args.delay:
            time.sleep(args.delay)

    print(
        f"done: created={created} updated={updated} skipped={skipped} failed={failed}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
