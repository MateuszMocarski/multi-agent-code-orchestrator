"""Backend-specific configuration and command construction for agent runtimes."""

import os
from dataclasses import dataclass
from pathlib import Path

from .config import BACKEND, PROJECT_DIR, REPO


@dataclass(frozen=True)
class RuntimeSpec:
    docker_options: tuple[str, ...]
    environment: tuple[str, ...]
    mounts: tuple[str, ...]
    command: tuple[str, ...]


def _configured_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else None


def validate_opencode_configuration() -> None:
    for name in ("ORCHESTRATOR_DEVELOPER_CONFIG", "ORCHESTRATOR_REVIEWER_CONFIG"):
        path = _configured_path(name)
        if path is None:
            raise RuntimeError(f"Missing required environment variable: {name}")
        if not path.is_absolute():
            raise RuntimeError(f"{name} must be an absolute path.")
        if not path.is_file():
            raise RuntimeError(f"Configured file does not exist: {name}={path}")


def validate_codex_configuration() -> None:
    state_dir = _configured_path("ORCHESTRATOR_CODEX_STATE_DIR")
    if state_dir is None:
        raise RuntimeError(
            "Missing required environment variable: ORCHESTRATOR_CODEX_STATE_DIR"
        )
    if not state_dir.is_absolute():
        raise RuntimeError("ORCHESTRATOR_CODEX_STATE_DIR must be an absolute path.")
    if not state_dir.is_dir():
        raise RuntimeError(
            f"Codex state directory does not exist or is not a directory: {state_dir}"
        )
    for protected_path in (PROJECT_DIR, REPO):
        if protected_path is None:
            continue
        try:
            state_dir.resolve().relative_to(protected_path.resolve())
        except ValueError:
            continue
        raise RuntimeError(
            "ORCHESTRATOR_CODEX_STATE_DIR must be outside the orchestrator source "
            "and target repository."
        )


def _codex_model(role: str) -> str | None:
    name = (
        "ORCHESTRATOR_DEVELOPER_MODEL"
        if role == "developer"
        else "ORCHESTRATOR_REVIEWER_MODEL"
    )
    return os.environ.get(name) or None


def runtime_spec(role: str) -> RuntimeSpec:
    if BACKEND == "opencode":
        config_name = (
            "ORCHESTRATOR_DEVELOPER_CONFIG"
            if role == "developer"
            else "ORCHESTRATOR_REVIEWER_CONFIG"
        )
        config_path = _configured_path(config_name)
        if config_path is None:
            raise RuntimeError(f"Missing required environment variable: {config_name}")
        return RuntimeSpec(
            docker_options=("--network", "host"),
            environment=("OPENCODE_CONFIG=/config/opencode.json",),
            mounts=(
                f"type=bind,src={config_path},dst=/config/opencode.json,readonly",
            ),
            command=("opencode", "run", "--standalone", "--auto"),
        )

    if BACKEND == "codex":
        state_dir = _configured_path("ORCHESTRATOR_CODEX_STATE_DIR")
        if state_dir is None:
            raise RuntimeError(
                "Missing required environment variable: ORCHESTRATOR_CODEX_STATE_DIR"
            )
        command = [
            "codex",
            "--ask-for-approval",
            "never",
            "exec",
            "--sandbox",
            "workspace-write",
            "--cd",
            "/workspace",
        ]
        if role == "reviewer":
            command.extend(("--add-dir", "/review"))
        model = _codex_model(role)
        if model:
            command.extend(("--model", model))
        return RuntimeSpec(
            docker_options=(),
            environment=("CODEX_HOME=/codex-state",),
            mounts=(f"type=bind,src={state_dir},dst=/codex-state",),
            command=tuple(command),
        )

    raise RuntimeError(f"Unsupported agent backend: {BACKEND}")
