#!/usr/bin/env python3
"""Convert Portainer app templates into a Kasm Workspaces registry (schema 1.1).

For every Portainer template of type 1 (single container) that exposes a TCP
port, this script generates:

    registry/1.1/workspaces/<slug>/workspace.json
    registry/1.1/workspaces/<slug>/<slug>.<png|svg|...>
    registry/1.1/icons/<slug>.<ext>
    registry/1.1/list.json
    registry/backend_manifest.json

The workspace launches ``kasmweb/chrome`` in kiosk mode against
``http://<slug>:<port>``. A separate orchestrator (see ``orchestrator/``)
starts and removes the Docker backend named ``<slug>`` on the Kasm network.

The script is idempotent: files are written atomically and existing icons are
reused. Run ``--help`` for the available options.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import io
import json
import logging
import os
import re
import shutil
import struct
import sys
import unicodedata
import urllib.error
import urllib.request
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger("convert_templates")

DEFAULT_SOURCE = (
    "https://raw.githubusercontent.com/ph7ti/portainer-templates-to-kasm/main/templates.json"
)
SCHEMA_VERSION = "1.1"
SCHEMA_PATH = Path(__file__).with_name("kasm_workspace_1.1.schema.json")
MAX_ICON_BYTES = 5 * 1024 * 1024
ICON_EXTENSIONS = (".png", ".svg", ".jpg", ".jpeg", ".gif", ".webp")

# Preferred container ports for web UIs. The first port present wins.
WEB_PORT_PRIORITY = (
    443,
    80,
    8443,
    8080,
    3000,
    3001,
    8000,
    8096,
    8123,
    8181,
    9000,
    9001,
    9090,
    8081,
    8888,
)

SYSTEM_VOLUME_PATHS = {
    "/etc/timezone",
    "/etc/localtime",
    "/var/run/docker.sock",
    "/run/docker.sock",
}


class ConversionError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def normalize_slug(title: str) -> str:
    """Return a DNS-friendly, lowercase slug derived from a Portainer title."""
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"\s*\((?:container|stack)\)\s*$", "", text, flags=re.I)
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return (text or "app")[:50].strip("-") or "app"


def clean_title(title: str) -> str:
    return re.sub(r"\s*\((?:container|stack)\)\s*$", "", title.strip(), flags=re.I).strip()


def parse_port_entry(entry: str) -> tuple[int | None, int, str] | None:
    """Parse ``host:container/proto``, ``container/proto`` or ``container``."""
    match = re.fullmatch(r"\s*(?:(\d+):)?(\d+)(?:/(tcp|udp))?\s*", str(entry))
    if not match:
        return None
    host = int(match.group(1)) if match.group(1) else None
    container = int(match.group(2))
    proto = (match.group(3) or "tcp").lower()
    if not 0 < container < 65536:
        return None
    return host, container, proto


def choose_container_port(
    ports: list[str], override: int | None = None
) -> tuple[int | None, list[tuple[int | None, int, str]]]:
    """Pick the container port that most likely serves the web UI."""
    parsed = [p for p in (parse_port_entry(entry) for entry in ports or []) if p]
    tcp = [container for _, container, proto in parsed if proto == "tcp"]
    if override is not None:
        return override, parsed
    if not tcp:
        return None, parsed
    for candidate in WEB_PORT_PRIORITY:
        if candidate in tcp:
            return candidate, parsed
    return tcp[0], parsed


def clean_categories(categories: list[str] | None) -> list[str]:
    result: list[str] = []
    for item in categories or []:
        value = re.sub(r"<[^>]+>", "", str(item)).strip()
        if value and value not in result:
            result.append(value)
        if len(result) == 3:
            break
    return result or ["Other"]


def clean_description(description: str, app_name: str) -> str:
    text = re.sub(r"\s+", " ", (description or "").strip())
    if not text:
        text = f"{app_name} em execução isolada no Kasm Workspaces."
    suffix = (
        " Esta é uma demonstração efêmera: o navegador roda isolado no Kasm e o "
        "serviço de backend é criado e removido automaticamente quando a sessão "
        "inicia e termina."
    )
    text = text[:1400].rstrip()
    return text + suffix


def clean_env(env: list[dict[str, Any]] | None) -> tuple[list[dict[str, Any]], dict[str, str]]:
    entries: list[dict[str, Any]] = []
    defaults: dict[str, str] = {}
    for item in env or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        entry: dict[str, Any] = {
            "name": name,
            "required": "default" not in item,
        }
        if "default" in item and item["default"] is not None:
            value = str(item["default"])
            entry["default"] = value
            defaults[name] = value
        if item.get("label"):
            entry["label"] = str(item["label"])[:200]
        entries.append(entry)
    return entries, defaults


def clean_volumes(volumes: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in volumes or []:
        container = str(item.get("container") or "").strip()
        bind = str(item.get("bind") or "").strip()
        readonly = bool(item.get("readonly"))
        if container.endswith(":ro"):
            container = container[:-3]
            readonly = True
        elif container.endswith(":rw"):
            container = container[:-3]
        if not container.startswith("/"):
            continue
        if container in SYSTEM_VOLUME_PATHS or bind in SYSTEM_VOLUME_PATHS:
            continue
        if not bind:
            continue
        result.append(
            {
                "container": container,
                "bind": bind,
                "readonly": readonly,
            }
        )
    return result


def _candidate_logo_url(logo: str | None) -> str | None:
    if not logo:
        return None
    url = str(logo).strip()
    if not url.lower().startswith(("http://", "https://")):
        return None
    # Portainer often points at GitHub's HTML blob page.
    if "github.com/" in url and "/blob/" in url:
        url = url.replace("github.com/", "raw.githubusercontent.com/", 1)
        url = url.replace("/blob/", "/", 1)
    return url


def detect_image_extension(data: bytes, content_type: str = "") -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    head = data[:1024].lstrip().lower()
    if head.startswith(b"<?xml") or head.startswith(b"<svg") or b"<svg" in head:
        return ".svg"
    lowered = content_type.lower()
    for mime, ext in (
        ("image/png", ".png"),
        ("image/jpeg", ".jpg"),
        ("image/svg", ".svg"),
        ("image/gif", ".gif"),
        ("image/webp", ".webp"),
    ):
        if mime in lowered:
            return ext
    return None


def _fetch_limited(url: str, timeout: float) -> tuple[bytes, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; kasm-registry-converter/1.0)",
            "Accept": "image/*,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get("Content-Type", "")
        data = response.read(MAX_ICON_BYTES + 1)
    if len(data) > MAX_ICON_BYTES:
        raise ConversionError("icon larger than 5 MB")
    return data, content_type


def find_existing_icon(directory: Path, slug: str) -> str | None:
    for extension in ICON_EXTENSIONS:
        candidate = directory / f"{slug}{extension}"
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate.name
    return None


# ---------------------------------------------------------------------------
# Default icon (generated locally, no external dependency)
# ---------------------------------------------------------------------------


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def make_default_icon_png(size: int = 128) -> bytes:
    """Draw a simple browser-window glyph and return PNG bytes."""
    width = height = size
    margin = size // 6
    border = max(2, size // 40)
    bar = max(10, size // 7)
    rows = []
    for y in range(height):
        row = bytearray([0])  # filter type 0
        for x in range(width):
            in_window = margin <= x < width - margin and margin <= y < height - margin
            if not in_window:
                color = (15, 23, 42, 255)  # slate-900 background
            elif (
                x < margin + border
                or x >= width - margin - border
                or y < margin + border
                or y >= height - margin - border
            ):
                color = (148, 163, 184, 255)  # slate-400 border
            elif y < margin + bar:
                color = (56, 189, 248, 255)  # sky-400 title bar
            else:
                color = (241, 245, 249, 255)  # slate-100 content
            row.extend(color)
        rows.append(bytes(row))
    raw = b"".join(rows)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw, 9))
        + _png_chunk(b"IEND", b"")
    )


# ---------------------------------------------------------------------------
# Source loading
# ---------------------------------------------------------------------------


def fetch_templates(
    source: str, cache_path: Path, refresh: bool, timeout: float
) -> tuple[dict[str, Any], str]:
    """Load templates.json from a path or URL, with a local cache fallback."""
    if not re.match(r"^https?://", source, flags=re.I):
        path = Path(source)
        if not path.is_file():
            raise ConversionError(f"source file not found: {source}")
        return json.loads(path.read_text(encoding="utf-8")), str(path)

    if cache_path.is_file() and not refresh:
        try:
            LOG.info("using cached templates: %s", cache_path)
            return json.loads(cache_path.read_text(encoding="utf-8")), source
        except (OSError, json.JSONDecodeError):
            LOG.warning("cache is invalid, downloading again")

    LOG.info("downloading templates: %s", source)
    try:
        request = urllib.request.Request(
            source, headers={"User-Agent": "kasm-registry-converter/1.0"}
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        if cache_path.is_file():
            LOG.warning("download failed (%s); using cached copy", exc)
            return json.loads(cache_path.read_text(encoding="utf-8")), source
        raise ConversionError(f"could not download templates: {exc}") from exc

    data = json.loads(raw.decode("utf-8"))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(raw)
    return data, source


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------


def build_app(
    template: dict[str, Any],
    slug: str,
    port: int,
    settings: argparse.Namespace,
) -> dict[str, Any]:
    title = str(template.get("title") or "").strip()
    display = clean_title(title) or slug
    image = str(template.get("image") or "").strip()
    env_entries, env_defaults = clean_env(template.get("env"))
    categories = clean_categories(template.get("categories"))
    volumes = clean_volumes(template.get("volumes"))

    target_url = f"http://{slug}:{port}"
    notes = (
        f"Teste efêmero do template Portainer '{title}'. "
        f"Backend Docker esperado: contêiner '{slug}' (imagem {image}), na rede "
        f"{settings.network}, escutando na porta interna {port}. "
        f"URL alvo: {target_url}. O orquestrador inicia o backend quando uma "
        f"sessão desta workspace é detectada e o remove quando a última sessão "
        f"termina. Se a página exibir erro de conexão nos primeiros segundos, "
        f"aguarde alguns instantes e recarregue."
    )

    return {
        "slug": slug,
        "source_id": template.get("id"),
        "source_title": title,
        "friendly_name": f"Teste: {display}",
        "description": clean_description(str(template.get("description") or ""), display),
        "image": image,
        "image_src": f"{slug}.png",
        "categories": categories,
        "architecture": ["amd64"],
        "port": port,
        "target_url": target_url,
        "launch_url": f"--kiosk {target_url}",
        "notes": notes,
        "env": env_entries,
        "env_defaults": env_defaults,
        "required_env": [entry["name"] for entry in env_entries if entry["required"]],
        "volumes": volumes,
        "restart_policy": str(template.get("restart_policy") or "no"),
        "privileged": bool(template.get("privileged")),
        "network_mode": str(template.get("network") or ""),
        "command": str(template.get("command") or ""),
        "hostname": str(template.get("hostname") or ""),
        "labels": template.get("labels") or [],
        "logo_url": _candidate_logo_url(template.get("logo")),
        "platform": template.get("platform"),
    }


def download_icon(app: dict[str, Any], directory: Path, timeout: float) -> tuple[str, str]:
    """Download one icon. Returns (slug, filename) or raises."""
    url = app["logo_url"]
    if not url:
        raise ConversionError("template has no usable logo URL")
    data, content_type = _fetch_limited(url, timeout)
    extension = detect_image_extension(data, content_type)
    if not extension:
        raise ConversionError(f"not a recognized image ({content_type or 'unknown'})")
    filename = f"{app['slug']}{extension}"
    target = directory / filename
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, target)
    return app["slug"], filename


def write_atomic(path: Path, data: str | bytes) -> bool:
    """Write text/bytes atomically. Returns True when content changed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if path.is_file() and path.read_bytes() == raw:
        return False
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(raw)
    os.replace(temporary, path)
    return True


def workspace_json(app: dict[str, Any], settings: argparse.Namespace) -> dict[str, Any]:
    return {
        "friendly_name": app["friendly_name"],
        "description": app["description"],
        "image_src": app["image_src"],
        "categories": app["categories"],
        "architecture": app["architecture"],
        "docker_registry": "https://index.docker.io/v1/",
        "compatibility": [
            {
                "version": settings.kasm_version_label,
                "image": f"{settings.browser_image}:{settings.browser_version}",
                "uncompressed_size_mb": settings.browser_size_mb,
            }
        ],
        "run_config": {
            "environment": {"LAUNCH_URL": app["launch_url"]},
            "shm_size": "512m",
        },
        "notes": app["notes"],
    }


def manifest_entry(app: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": app["slug"],
        "source_id": app["source_id"],
        "source_title": app["source_title"],
        "friendly_name": app["friendly_name"],
        "image": app["image"],
        "container_name": app["slug"],
        "port": app["port"],
        "launch_url": app["launch_url"],
        "target_url": app["target_url"],
        "env": app["env"],
        "env_defaults": app["env_defaults"],
        "required_env": app["required_env"],
        "volumes": app["volumes"],
        "restart_policy": app["restart_policy"],
        "privileged": app["privileged"],
        "network_mode": app["network_mode"],
        "command": app["command"],
        "hostname": app["hostname"],
        "labels": app["labels"],
        "image_src": app["image_src"],
    }


# ---------------------------------------------------------------------------
# Registry artifacts
# ---------------------------------------------------------------------------


def sha1_workspace(directory: Path, image_src: str) -> str:
    digest = hashlib.sha1()
    for name in ("workspace.json", image_src):
        path = directory / name
        if path.is_file():
            digest.update(name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def build_registry_listing(
    output: Path, apps: list[dict[str, Any]], settings: argparse.Namespace
) -> dict[str, Any]:
    version_dir = output / SCHEMA_VERSION
    workspaces_dir = version_dir / "workspaces"
    entries = []
    for app in apps:
        directory = workspaces_dir / app["slug"]
        workspace = json.loads((directory / "workspace.json").read_text(encoding="utf-8"))
        workspace["sha"] = sha1_workspace(directory, app["image_src"])
        entries.append(workspace)

    listing = {
        "name": settings.registry_name,
        "workspacecount": len(entries),
        "icon": settings.registry_icon,
        "description": settings.registry_description,
        "list_url": settings.registry_url,
        "contact_url": settings.registry_contact,
        "modified": int(datetime.now(timezone.utc).timestamp() * 1000),
        "workspaces": entries,
        "channels": [],
        "default_channel": None,
    }
    text = json.dumps(listing, ensure_ascii=False, separators=(",", ":"))
    write_atomic(version_dir / "list.json", text)
    write_atomic(output / "list.json", text)
    write_atomic(
        version_dir / "versions.json",
        json.dumps({"versions": [settings.kasm_version_label]}, ensure_ascii=False),
    )
    write_atomic(output / "versions.txt", f"{SCHEMA_VERSION}\n")
    write_atomic(
        output / "index.html",
        "<!doctype html><meta charset=\"utf-8\">"
        f"<title>{settings.registry_name}</title>"
        "<meta http-equiv=\"refresh\" content=\"0; url=./1.1/\">"
        "<p>Redirecionando para o catálogo <a href=\"./1.1/\">1.1</a>.</p>\n",
    )
    return listing


def prune_stale(workspaces_root: Path, icons_root: Path, keep_slugs: set[str]) -> int:
    removed = 0
    if workspaces_root.is_dir():
        for directory in workspaces_root.iterdir():
            if directory.is_dir() and directory.name not in keep_slugs:
                shutil.rmtree(directory)
                removed += 1
    if icons_root.is_dir():
        for icon in icons_root.iterdir():
            if icon.is_file() and icon.stem not in keep_slugs:
                icon.unlink()
                removed += 1
    return removed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert Portainer templates (type 1) to a Kasm 1.1 registry.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="URL or path to templates.json")
    parser.add_argument("--output", default="registry", help="registry output directory")
    parser.add_argument("--cache", default=".cache/templates.json", help="templates.json cache")
    parser.add_argument("--refresh", action="store_true", help="force re-download")
    parser.add_argument("--offline", action="store_true", help="never use the network")
    parser.add_argument("--no-icons", action="store_true", help="use the default icon only")
    parser.add_argument("--icon-timeout", type=float, default=12.0)
    parser.add_argument("--icon-workers", type=int, default=8)
    parser.add_argument("--kasm-version", default="1.19.0", help="installed Kasm version")
    parser.add_argument(
        "--kasm-version-label",
        default="",
        help="compatibility label (default: <major>.<minor>.x)",
    )
    parser.add_argument("--browser-image", default="kasmweb/chrome")
    parser.add_argument("--browser-version", default="", help="browser image tag (default: Kasm version)")
    parser.add_argument("--browser-size-mb", type=int, default=2500)
    parser.add_argument("--network", default="kasm_default_network")
    parser.add_argument("--allow-privileged", action="store_true")
    parser.add_argument("--allow-host-network", action="store_true")
    parser.add_argument("--max-apps", type=int, default=0, help="limit for pilot runs")
    parser.add_argument("--prune", action="store_true", help="remove stale workspace folders")
    parser.add_argument("--registry-name", default="Portainer Test Apps (Kasm)")
    parser.add_argument("--registry-icon", default="")
    parser.add_argument(
        "--registry-description",
        default="Aplicações do catálogo Portainer para teste efêmero e isolado no Kasm.",
    )
    parser.add_argument("--registry-url", default="")
    parser.add_argument("--registry-contact", default="https://github.com/ph7ti/portainer-templates-to-kasm")
    parser.add_argument("--port-overrides", default=str(Path(__file__).with_name("port_overrides.json")))
    parser.add_argument("--schema", default=str(SCHEMA_PATH))
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    if not args.kasm_version_label:
        parts = re.findall(r"\d+", args.kasm_version)
        args.kasm_version_label = f"{parts[0]}.{parts[1]}.x" if len(parts) >= 2 else "1.19.x"
    if not args.browser_version:
        args.browser_version = args.kasm_version
    return args


def load_port_overrides(path: str) -> dict[str, int]:
    if not path or not Path(path).is_file():
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {str(key): int(value) for key, value in data.items()}


def load_validator(schema_path: str):
    try:
        import jsonschema
    except ImportError:
        LOG.warning("jsonschema is not installed; skipping schema validation")
        return None
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    return jsonschema.Draft7Validator(schema)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(message)s",
    )
    if args.offline:
        args.source = str(args.cache)
        args.no_icons = True

    output = Path(args.output).resolve()
    workspace_root = output / SCHEMA_VERSION / "workspaces"
    icons_root = output / SCHEMA_VERSION / "icons"
    workspace_root.mkdir(parents=True, exist_ok=True)
    icons_root.mkdir(parents=True, exist_ok=True)

    overrides = load_port_overrides(args.port_overrides)
    templates, source_label = fetch_templates(
        args.source, Path(args.cache), args.refresh, timeout=args.icon_timeout
    )
    if not isinstance(templates, dict) or not isinstance(templates.get("templates"), list):
        raise ConversionError("invalid templates.json: missing templates array")
    version = str(templates.get("version", ""))
    if version not in ("3", "3.0"):
        raise ConversionError(f"unsupported templates version: {version!r}")

    apps: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    used_slugs: set[str] = set()
    skip_counts: dict[str, int] = {}

    def skip(template: dict[str, Any], reason: str) -> None:
        skipped.append(
            {
                "id": template.get("id"),
                "title": template.get("title"),
                "reason": reason,
            }
        )
        skip_counts[reason] = skip_counts.get(reason, 0) + 1

    for template in templates["templates"]:
        template_type = template.get("type")
        if template_type != 1:
            skip(template, f"type-{template_type}-not-container")
            continue
        if str(template.get("platform") or "linux").lower() == "windows":
            skip(template, "windows-platform")
            continue
        if template.get("privileged") and not args.allow_privileged:
            skip(template, "privileged")
            continue
        if str(template.get("network") or "").lower() == "host" and not args.allow_host_network:
            skip(template, "host-network")
            continue
        image = str(template.get("image") or "").strip()
        if not image:
            skip(template, "missing-image")
            continue

        title = str(template.get("title") or "").strip()
        base_slug = normalize_slug(title)
        slug = base_slug
        suffix = 2
        while slug in used_slugs:
            slug = f"{base_slug}-{suffix}"
            suffix += 1
        port, parsed_ports = choose_container_port(template.get("ports") or [], overrides.get(base_slug))
        if port is None:
            skip(template, "no-tcp-port")
            continue

        used_slugs.add(slug)
        apps.append(build_app(template, slug, port, args))
        if args.max_apps and len(apps) >= args.max_apps:
            LOG.info("stopping after --max-apps=%s", args.max_apps)
            break

    default_icon = make_default_icon_png()
    icon_failures: list[tuple[str, str]] = []
    converted = 0
    unchanged = 0
    errors: list[str] = []
    validator = load_validator(args.schema)

    # Icons: reuse an existing file, otherwise download concurrently.
    for app in apps:
        directory = workspace_root / app["slug"]
        directory.mkdir(parents=True, exist_ok=True)
        existing = find_existing_icon(directory, app["slug"])
        if existing:
            app["image_src"] = existing
        elif args.no_icons or not app["logo_url"]:
            write_atomic(directory / "default-icon.png", default_icon)
            app["image_src"] = "default-icon.png"
        else:
            app["image_src"] = ""  # filled below

    pending = [app for app in apps if not app["image_src"]]
    if pending:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, args.icon_workers)
        ) as pool:
            future_map = {
                pool.submit(download_icon, app, workspace_root / app["slug"], args.icon_timeout): app
                for app in pending
            }
            for future in concurrent.futures.as_completed(future_map):
                app = future_map[future]
                try:
                    _, filename = future.result()
                    app["image_src"] = filename
                except Exception as exc:  # noqa: BLE001 - fall back for every icon error
                    LOG.warning("icon failed for %s: %s", app["slug"], exc)
                    icon_failures.append((app["slug"], str(exc)))
                    write_atomic(workspace_root / app["slug"] / "default-icon.png", default_icon)
                    app["image_src"] = "default-icon.png"

    for app in apps:
        directory = workspace_root / app["slug"]
        payload = workspace_json(app, args)
        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        if write_atomic(directory / "workspace.json", text):
            converted += 1
        else:
            unchanged += 1
        if validator is not None:
            for error in sorted(validator.iter_errors(payload), key=lambda item: item.path):
                errors.append(f"{app['slug']}: {error.message}")
        icon_file = directory / app["image_src"]
        if icon_file.is_file():
            write_atomic(icons_root / app["image_src"], icon_file.read_bytes())
        else:
            errors.append(f"{app['slug']}: missing icon {app['image_src']}")

    listing = build_registry_listing(output, apps, args)
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": source_label,
        "kasm": {
            "version": args.kasm_version,
            "version_label": args.kasm_version_label,
            "schema_version": SCHEMA_VERSION,
            "browser_image": f"{args.browser_image}:{args.browser_version}",
            "network": args.network,
            "registry_url": args.registry_url,
        },
        "counts": {
            "templates": len(templates["templates"]),
            "converted": len(apps),
            "skipped": len(skipped),
            "icon_failures": len(icon_failures),
            "validation_errors": len(errors),
        },
        "apps": {app["slug"]: manifest_entry(app) for app in apps},
        "skipped": skipped,
    }
    write_atomic(
        output / "backend_manifest.json",
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    )

    removed = prune_stale(workspace_root, icons_root, used_slugs) if args.prune else 0

    LOG.info("-" * 72)
    LOG.info("templates: %s | convertidos: %s | workspaces: %s", len(templates["templates"]), len(apps), listing["workspacecount"])
    LOG.info("workspace.json alterados: %s | inalterados: %s", converted, unchanged)
    LOG.info("skips: %s", ", ".join(f"{key}={value}" for key, value in sorted(skip_counts.items())))
    if icon_failures:
        LOG.warning("ícones com fallback: %s", len(icon_failures))
    if removed:
        LOG.info("diretórios antigos removidos: %s", removed)
    if errors:
        for message in errors[:20]:
            LOG.error("schema: %s", message)
        LOG.error("validação falhou com %s erro(s)", len(errors))
        return 1
    LOG.info("registry gerado em: %s", output)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ConversionError as exc:
        LOG.error("%s", exc)
        raise SystemExit(2)
