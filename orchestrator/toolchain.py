"""Pinned image tool versions and explicit registry maintenance commands."""

import json
import os
import re
import stat
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK_FILE = ROOT / "toolchain.json"
_NUMERIC = r"(?:0|[1-9]\d*)"
_BUILD_IDENTIFIER = r"[0-9A-Za-z-]+"
_PRERELEASE_IDENTIFIER = r"(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
SEMVER = re.compile(
    rf"^({_NUMERIC})\.({_NUMERIC})\.({_NUMERIC})"
    rf"(?:-({_PRERELEASE_IDENTIFIER}(?:\.{_PRERELEASE_IDENTIFIER})*))?"
    rf"(?:\+({_BUILD_IDENTIFIER}(?:\.{_BUILD_IDENTIFIER})*))?$"
)


def load(path: Path = LOCK_FILE) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read toolchain file {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("tools"), dict):
        raise RuntimeError(f"Unsupported or invalid toolchain file: {path}")
    for name in ("codex", "opencode", "pytest"):
        entry = data["tools"].get(name)
        if (not isinstance(entry, dict) or not entry.get("package")
                or not stable_version(entry.get("version", ""))):
            raise RuntimeError(f"Invalid {name} package/version in {path}")
    image = data.get("image")
    if not isinstance(image, dict) or not all(
        image.get(k) for k in ("tag_version", "python_base", "python_version", "node_base")
    ):
        raise RuntimeError(f"Invalid image metadata in {path}")
    return data


def stable_version(value: str) -> bool:
    if not isinstance(value, str):
        return False
    match = SEMVER.fullmatch(value)
    return bool(match) and match.group(4) is None


def compare_stable_versions(left: str, right: str) -> int:
    """Compare stable exact SemVer versions, returning -1, 0, or 1."""
    if not stable_version(left) or not stable_version(right):
        raise ValueError("Only stable exact semantic versions can be compared")
    left_parts = tuple(int(part) for part in left.split("+", 1)[0].split("."))
    right_parts = tuple(int(part) for part in right.split("+", 1)[0].split("."))
    return (left_parts > right_parts) - (left_parts < right_parts)


def update_state(pinned: str, candidate: str) -> str:
    """Classify a registry candidate without treating lower tags as upgrades."""
    comparison = compare_stable_versions(pinned, candidate)
    return "UPDATE AVAILABLE" if comparison < 0 else "CURRENT" if comparison == 0 else "PIN AHEAD"


def latest_stable(
    package: str,
    registry: str = "https://registry.npmjs.org",
    timeout: float = 8.0,
) -> str:
    is_pypi = registry.rstrip("/").endswith("/pypi")
    if is_pypi:
        url = f"{registry.rstrip('/')}/{urllib.parse.quote(package)}/json"
    else:
        url = f"{registry.rstrip('/')}/{urllib.parse.quote(package, safe='@')}"
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.npm.install-v1+json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Could not query package registry for {package}: {exc}. "
            "Check network access and retry."
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Package registry returned an invalid response for {package}")
    tags = payload.get("dist-tags", {})
    versions = payload.get("versions", {})
    if not isinstance(tags, dict) or not isinstance(versions, dict):
        raise RuntimeError(f"Package registry returned invalid release metadata for {package}")
    candidate = payload.get("info", {}).get("version") if is_pypi else tags.get("latest")
    exists = candidate in versions if not is_pypi else bool(candidate)
    if not stable_version(candidate) or not exists:
        # Registries define latest as stable. Verify the release exists and isn't a prerelease.
        raise RuntimeError(f"Registry returned no valid stable latest release for {package}")
    return candidate


def versions_report(data: dict | None = None) -> str:
    data = data or load()
    lines = ["Pinned toolchain:"]
    for key in ("codex", "opencode", "pytest"):
        item = data["tools"][key]
        lines.append(f"  {key}: {item['package']} {item['version']}")
    lines.append(
        f"  Python image: {data['image']['python_base']} "
        f"(runtime {data['image']['python_version']})"
    )
    lines.append(f"  Node image: {data['image']['node_base']}")
    return "\n".join(lines)


def check_updates(data: dict | None = None, query=latest_stable) -> list[tuple[str, str, str]]:
    data = data or load()
    results = []
    for name in ("codex", "opencode", "pytest"):
        item = data["tools"][name]
        latest = query(item["package"], item["registry"])
        results.append((name, item["version"], latest))
    return results


def update(
    path: Path = LOCK_FILE,
    selected: tuple[str, ...] = ("codex", "opencode", "pytest"),
    query=latest_stable,
) -> list[tuple[str, str, str, str]]:
    data = load(path)
    outcomes = []
    changed = False
    for name in selected:
        if name not in data["tools"]:
            raise ValueError(f"Unknown toolchain component: {name}")
        item = data["tools"][name]
        candidate = query(item["package"], item["registry"])
        if not stable_version(candidate):
            raise RuntimeError(f"Registry candidate for {name} is not a stable exact version: {candidate}")
        pinned = item["version"]
        comparison = compare_stable_versions(pinned, candidate)
        if comparison < 0:
            item["version"] = candidate
            changed = True
        state = update_state(pinned, candidate)
        outcomes.append((name, pinned, candidate, state))
        if comparison > 0:
            continue
    if not changed:
        return outcomes
    rendered = json.dumps(data, indent=2, sort_keys=False) + "\n"
    # Validate the complete candidate before atomically replacing the tracked lock.
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=".toolchain.", delete=False
    ) as handle:
        temp = Path(handle.name)
        handle.write(rendered)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        load(temp)
        os.chmod(temp, stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    return outcomes
