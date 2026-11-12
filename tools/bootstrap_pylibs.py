"""Bootstrap a local pure-Python library directory from PyPI wheels.

The DSH sandbox on this Windows host blocks pip's temporary file handling, so
this script downloads wheels with urllib and extracts them into vendor/pylibs.
It is a development convenience only; production installs use requirements.txt.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor"
WHEELS = VENDOR / "wheels"
PYLIBS = VENDOR / "pylibs"
TOP_LEVEL = ["jsonschema", "pytest", "requests", "docker"]

PY_VERSION = f"{sys.version_info.major}.{sys.version_info.minor}"
PY_FULL = ".".join(str(x) for x in sys.version_info[:3])
IS_WINDOWS = os.name == "nt"

_seen: dict[str, str] = {}


def fetch_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "kasm-bootstrap"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read())


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def marker_ok(marker: str) -> bool:
    marker = marker.strip()
    if not marker:
        return True
    # Extras are not installed by this bootstrap.
    if "extra" in marker:
        return False
    parts = re.split(r"\s+or\s+", marker)
    for part in parts:
        if all(check_clause(clause) for clause in re.split(r"\s+and\s+", part)):
            return True
    return False


def check_clause(clause: str) -> bool:
    clause = clause.strip().strip("()")
    if not clause:
        return True
    match = re.match(
        r'(python_version|python_full_version|sys_platform|platform_system|os_name)\s*(==|!=|>=|<=|>|<)\s*["\']([^"\']+)["\']',
        clause,
    )
    if not match:
        return True
    key, op, expected = match.groups()
    actual = {
        "python_version": PY_VERSION,
        "python_full_version": PY_FULL,
        "sys_platform": "win32" if IS_WINDOWS else "linux",
        "platform_system": "Windows" if IS_WINDOWS else "Linux",
        "os_name": "nt" if IS_WINDOWS else "posix",
    }.get(key, "")
    try:
        if key.startswith("python"):
            a, b = tuple(int(x) for x in actual.split(".")), tuple(
                int(x) for x in expected.split(".")
            )
        else:
            a, b = actual, expected
    except ValueError:
        return True
    return {
        "==": a == b,
        "!=": a != b,
        ">=": a >= b,
        "<=": a <= b,
        ">": a > b,
        "<": a < b,
    }[op]


def pick_wheel(files: list[dict]) -> dict | None:
    candidates = []
    for item in files:
        filename = item.get("filename", "")
        if not filename.endswith(".whl") or item.get("yanked"):
            continue
        match = re.search(r"-(\d[^-]*)-(.+)\.whl$", filename)
        if not match:
            continue
        tags = match.group(2)
        if "win_amd64" in tags or "none-any" in tags:
            score = 0
            if "cp313-cp313-win_amd64" in tags:
                score = 5
            elif "abi3-win_amd64" in tags:
                score = 4
            elif "cp313" in tags and "win_amd64" in tags:
                score = 3
            elif "none-any" in tags:
                score = 2
            elif "win_amd64" in tags:
                score = 1
            candidates.append((score, item))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return candidates[0][1]


def wheel_metadata(path: Path) -> tuple[str, list[str]]:
    with zipfile.ZipFile(path) as archive:
        names = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
        if not names:
            return "", []
        text = archive.read(names[0]).decode("utf-8", "replace")
    requires = []
    for line in text.splitlines():
        if line.startswith("Requires-Dist:"):
            requires.append(line.split(":", 1)[1].strip())
    return text, requires


def requirement_name(requirement: str) -> str:
    match = re.match(r"([A-Za-z0-9_.\-]+)", requirement.strip())
    return normalize(match.group(1)) if match else ""


def requirement_marker(requirement: str) -> str:
    if ";" not in requirement:
        return ""
    return requirement.split(";", 1)[1].strip()


def ensure_package(name: str) -> None:
    name = normalize(name)
    if name in _seen:
        return
    _seen[name] = "pending"
    data = fetch_json(f"https://pypi.org/pypi/{name}/json")
    info = data["info"]
    version = info["version"]
    wheel = pick_wheel(data["urls"])
    if not wheel:
        raise RuntimeError(f"no compatible wheel for {name}=={version}")
    filename = wheel["filename"]
    target = WHEELS / filename
    if not target.exists():
        print(f"download {filename}")
        req = urllib.request.Request(wheel["url"], headers={"User-Agent": "kasm-bootstrap"})
        with urllib.request.urlopen(req, timeout=180) as response:
            target.write_bytes(response.read())
    _, requires = wheel_metadata(target)
    for requirement in requires:
        marker = requirement_marker(requirement)
        if not marker_ok(marker):
            continue
        dep = requirement_name(requirement)
        if dep and dep != name:
            ensure_package(dep)
    _seen[name] = version


def extract_all() -> None:
    PYLIBS.mkdir(parents=True, exist_ok=True)
    for wheel in sorted(WHEELS.glob("*.whl")):
        with zipfile.ZipFile(wheel) as archive:
            for member in archive.infolist():
                if member.filename.startswith((".dist-info/",)) or "/.data/" in member.filename:
                    continue
                archive.extract(member, PYLIBS)
        print(f"extract {wheel.name}")


def main() -> None:
    WHEELS.mkdir(parents=True, exist_ok=True)
    for package in TOP_LEVEL:
        ensure_package(package)
    extract_all()
    (VENDOR / "bootstrap.json").write_text(
        json.dumps({"packages": _seen}, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(f"\n{len(list(WHEELS.glob('*.whl')))} wheels, {len(_seen)} packages")
    print(f"run with: PYTHONPATH={PYLIBS}")


if __name__ == "__main__":
    main()
