"""Unit tests for the Portainer -> Kasm converter."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from converter import convert_templates as converter

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "sample_templates.json"
SCHEMA = ROOT / "converter" / "kasm_workspace_1.1.schema.json"


def run_converter(output: Path, *extra: str) -> int:
    return converter.main(
        [
            "--source",
            str(FIXTURE),
            "--output",
            str(output),
            "--cache",
            str(output / ".cache" / "templates.json"),
            "--no-icons",
            "--no-wait-page",
            *extra,
        ]
    )


def workspace_path(output: Path, slug: str) -> Path:
    return output / "1.1" / "workspaces" / slug / "workspace.json"


def test_converts_only_web_containers(tmp_path: Path) -> None:
    assert run_converter(tmp_path) == 0

    expected = {
        "nextcloud",
        "gitea",
        "uptime-kuma",
        "adguard",
        "minio",
        "minecraft-server",
        "many-categories",
        "gitea-2",
        "no-logo-app",
    }
    workspaces = tmp_path / "1.1" / "workspaces"
    assert {path.name for path in workspaces.iterdir() if path.is_dir()} == expected

    manifest = json.loads((tmp_path / "backend_manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["templates"] == 15
    assert manifest["counts"]["converted"] == len(expected)
    assert manifest["counts"]["skipped"] == 6
    reasons = [item["reason"] for item in manifest["skipped"]]
    assert reasons.count("type-3-not-container") == 1
    assert reasons.count("no-tcp-port") == 2
    assert reasons.count("windows-platform") == 1
    assert reasons.count("privileged") == 1
    assert reasons.count("host-network") == 1
    assert manifest["apps"]["nextcloud"]["port"] == 80  # override: TLS container also serves HTTP
    assert manifest["apps"]["gitea"]["required_env"] == ["GITEA_ADMIN_PASSWORD"]
    assert all(v["container"] != "/etc/timezone" for v in manifest["apps"]["gitea"]["volumes"])
    assert manifest["apps"]["adguard"]["port"] == 3000  # override beats 443/80
    assert manifest["apps"]["minio"]["port"] == 9001


def test_workspace_payloads_match_kasm_schema(tmp_path: Path) -> None:
    assert run_converter(tmp_path) == 0
    import jsonschema

    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validator = jsonschema.Draft7Validator(schema)
    for path in (tmp_path / "1.1" / "workspaces").glob("*/workspace.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        errors = list(validator.iter_errors(payload))
        assert not errors, f"{path}: {[error.message for error in errors]}"

    nextcloud = json.loads(workspace_path(tmp_path, "nextcloud").read_text(encoding="utf-8"))
    assert nextcloud["friendly_name"] == "Teste: Nextcloud"
    assert nextcloud["run_config"]["environment"]["LAUNCH_URL"] == "--kiosk http://nextcloud:80"
    assert nextcloud["run_config"]["shm_size"] == "512m"
    assert nextcloud["compatibility"][0]["image"] == "kasmweb/chrome:1.19.0"
    assert nextcloud["compatibility"][0]["version"] == "1.19.x"
    assert nextcloud["architecture"] == ["amd64"]
    assert "nextcloud" in nextcloud["notes"]
    assert nextcloud["image_src"] == "default-icon.png"


def test_registry_listing_and_versions(tmp_path: Path) -> None:
    assert run_converter(tmp_path) == 0
    listing = json.loads((tmp_path / "1.1" / "list.json").read_text(encoding="utf-8"))
    assert listing["workspacecount"] == 9
    assert len(listing["workspaces"]) == 9
    assert all("sha" in item and len(item["sha"]) == 40 for item in listing["workspaces"])
    assert all(item["compatibility"][0]["version"] == "1.19.x" for item in listing["workspaces"])
    assert (tmp_path / "versions.txt").read_text(encoding="utf-8") == "1.1\n"
    assert (tmp_path / "list.json").is_file()
    assert (tmp_path / "1.1" / "icons" / "default-icon.png").is_file()


def test_idempotent_second_run(tmp_path: Path) -> None:
    assert run_converter(tmp_path) == 0
    first = {
        path.relative_to(tmp_path): hashlib.sha1(path.read_bytes()).hexdigest()
        for path in sorted(tmp_path.rglob("*"))
        if path.is_file() and ".cache" not in path.parts
    }
    assert run_converter(tmp_path) == 0
    second = {
        path.relative_to(tmp_path): hashlib.sha1(path.read_bytes()).hexdigest()
        for path in sorted(tmp_path.rglob("*"))
        if path.is_file() and ".cache" not in path.parts
    }
    # list.json and backend_manifest.json embed timestamps: compare the stable tree.
    for volatile in (
        Path("1.1/list.json"),
        Path("list.json"),
        Path("backend_manifest.json"),
    ):
        first.pop(volatile)
        second.pop(volatile)
    assert first == second
    assert len(list((tmp_path / "1.1" / "workspaces").iterdir())) == 9


@pytest.mark.parametrize(
    ("ports", "override", "expected"),
    [
        (["443:443/tcp"], None, 443),
        (["80:80/tcp", "443:443/tcp"], None, 443),
        (["53:53/tcp", "80:80/tcp"], None, 80),
        (["3000:3000/tcp", "222:22/tcp"], None, 3000),
        (["9000/tcp", "9001/tcp"], 9001, 9001),
        (["69/udp"], None, None),
        (["bad"], None, None),
        (["8080:8080/tcp"], None, 8080),
    ],
)
def test_choose_container_port(ports, override, expected) -> None:
    port, _ = converter.choose_container_port(ports, override)
    assert port == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Nextcloud (container)", "nextcloud"),
        ("Uptime Kuma (container)", "uptime-kuma"),
        ("Gitea", "gitea"),
        ("Café & Bar", "cafe-and-bar"),
        ("  weird   name!! ", "weird-name"),
        ("123", "123"),
    ],
)
def test_normalize_slug(title, expected) -> None:
    assert converter.normalize_slug(title) == expected


def test_https_port_uses_tls_and_ignores_self_signed_errors() -> None:
    settings = converter.parse_args(["--output", "unused", "--no-wait-page"])
    app = converter.build_app(
        {
            "id": 99,
            "type": 1,
            "title": "Code Server (container)",
            "description": "Web IDE",
            "categories": ["Development"],
            "image": "linuxserver/code-server:latest",
            "ports": ["8443:8443/tcp"],
        },
        "code-server",
        8443,
        settings,
    )
    assert app["target_url"] == "https://code-server:8443"
    assert app["launch_url"] == "--kiosk --ignore-certificate-errors https://code-server:8443"
    assert app["notes"].find("https://code-server:8443") >= 0


def test_wait_page_url_is_percent_encoded_and_default() -> None:
    settings = converter.parse_args(["--output", "unused"])
    app = converter.build_app(
        {
            "id": 100,
            "type": 1,
            "title": "Code Server (container)",
            "description": "Web IDE",
            "categories": ["Development"],
            "image": "linuxserver/code-server:latest",
            "ports": ["8443:8443/tcp"],
        },
        "code-server",
        8443,
        settings,
    )
    assert app["wait_url"] == (
        "http://kasm-wait:8080/wait?target=https%3A%2F%2Fcode-server%3A8443"
    )
    assert app["launch_url"] == (
        "--kiosk --ignore-certificate-errors "
        "http://kasm-wait:8080/wait?target=https%3A%2F%2Fcode-server%3A8443"
    )
    assert "página de espera" in app["notes"]


def test_wait_page_default_in_full_conversion(tmp_path: Path) -> None:
    assert (
        converter.main(
            [
                "--source",
                str(FIXTURE),
                "--output",
                str(tmp_path),
                "--cache",
                str(tmp_path / ".cache" / "templates.json"),
                "--no-icons",
            ]
        )
        == 0
    )
    payload = json.loads(workspace_path(tmp_path, "nextcloud").read_text(encoding="utf-8"))
    assert payload["run_config"]["environment"]["LAUNCH_URL"] == (
        "--kiosk http://kasm-wait:8080/wait?target=http%3A%2F%2Fnextcloud%3A80"
    )


def test_clean_volumes_strips_readonly_and_system_paths() -> None:
    volumes = converter.clean_volumes(
        [
            {"container": "/data", "bind": "/srv/data"},
            {"container": "/etc/timezone:ro", "bind": "/etc/timezone"},
            {"container": "/var/run/docker.sock", "bind": "/var/run/docker.sock"},
            {"container": "relative", "bind": "/tmp/x"},
            {"container": "/config", "bind": ""},
        ]
    )
    assert volumes == [{"container": "/data", "bind": "/srv/data", "readonly": False}]


def test_detect_image_extension() -> None:
    assert converter.detect_image_extension(b"\x89PNG\r\n\x1a\nxxx") == ".png"
    assert converter.detect_image_extension(b"<svg xmlns='...'></svg>") == ".svg"
    assert converter.detect_image_extension(b"not an image") is None
