"""Build and validate the shared agent image without running an agent task."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import toolchain


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "docker" / "Dockerfile.agent"
BACKENDS = {"codex", "opencode"}


def current_toolchain() -> dict:
    """Read the lock at operation time rather than caching mutable pins."""
    return toolchain.load(toolchain.LOCK_FILE)


def toolchain_identity(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def host_identity() -> tuple[int, int]:
    """Return the Unix identity that owns the local workspace and bind mounts."""
    if not hasattr(os, "getuid") or not hasattr(os, "getgid"):
        raise RuntimeError("Image builds require a Unix host with UID/GID support.")
    uid, gid = os.getuid(), os.getgid()
    if uid == 0 or gid == 0:
        raise RuntimeError("Build the agent image as a non-root host user.")
    return uid, gid


def validate_backend(backend: str) -> str:
    if backend not in BACKENDS:
        raise ValueError("ORCHESTRATOR_AGENT_BACKEND must be one of: opencode, codex")
    return backend


def default_image_name(backend: str, toolchain_data: dict | None = None) -> str:
    validate_backend(backend)
    data = toolchain_data if toolchain_data is not None else current_toolchain()
    python_tag = data["image"]["python_version"].replace(".", "")
    return f"orchestrator-agent:{backend}-py{python_tag}-{data['image']['tag_version']}"


def build_command(
    backend: str,
    image: str,
    context: Path,
    requirements: Path | None = None,
    toolchain_data: dict | None = None,
) -> list[str]:
    validate_backend(backend)
    data = toolchain_data if toolchain_data is not None else current_toolchain()
    identity = toolchain_identity(data)
    uid, gid = host_identity()
    command = [
        "docker",
        "build",
        "--file",
        str(context / "Dockerfile.agent"),
        "--build-arg",
        f"AGENT_BACKEND={backend}",
        "--build-arg",
        f"AGENT_UID={uid}",
        "--build-arg",
        f"AGENT_GID={gid}",
        "--build-arg",
        f"CODEX_PACKAGE={data['tools']['codex']['package']}",
        "--build-arg",
        f"CODEX_VERSION={data['tools']['codex']['version']}",
        "--build-arg",
        f"OPENCODE_PACKAGE={data['tools']['opencode']['package']}",
        "--build-arg",
        f"OPENCODE_VERSION={data['tools']['opencode']['version']}",
        "--build-arg",
        f"PYTEST_VERSION={data['tools']['pytest']['version']}",
        "--build-arg",
        f"NODE_BASE={data['image']['node_base']}",
        "--build-arg",
        f"PYTHON_BASE={data['image']['python_base']}",
        "--build-arg",
        f"PYTHON_VERSION={data['image']['python_version']}",
        "--build-arg",
        f"AGENT_BACKEND_VERSION={data['tools'][backend]['version']}",
        "--build-arg",
        f"AGENT_BACKEND_PACKAGE={data['tools'][backend]['package']}",
        "--build-arg",
        f"TOOLCHAIN_ID={identity}",
        "--tag",
        image,
    ]
    if requirements is not None:
        command.extend(
            ("--secret", f"id=target-requirements,src={requirements}")
        )
    command.append(str(context))
    return command


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def _inspect_image_metadata(backend: str, image: str, toolchain_data: dict | None = None) -> None:
    data = toolchain_data if toolchain_data is not None else current_toolchain()
    identity = toolchain_identity(data)
    template = "|".join((
        '{{index .Config.Labels "org.opencontainers.image.backend"}}',
        '{{index .Config.Labels "org.opencontainers.image.backend-package"}}',
        '{{index .Config.Labels "org.opencontainers.image.backend-version"}}',
        '{{index .Config.Labels "org.opencontainers.image.toolchain-id"}}',
    ))
    try:
        result = subprocess.run(["docker", "image", "inspect", image, "--format", template],
                                check=True, text=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        diagnostic = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeError(f"Cannot inspect image {image}: {diagnostic}") from exc
    fields = result.stdout.strip().split("|")
    if len(fields) == 4 and all(value in ("", "<no value>") for value in fields):
        print(f"Image metadata unavailable for legacy image: {image}")
        return
    expected = [backend, data["tools"][backend]["package"],
                data["tools"][backend]["version"], identity]
    if fields != expected:
        raise RuntimeError(f"Image metadata mismatch for {image}: expected backend/toolchain labels {expected[:3]}")


def build_image(backend: str, image: str, requirements: str | None, toolchain_data: dict | None = None) -> None:
    data = toolchain_data if toolchain_data is not None else current_toolchain()
    validate_backend(backend)
    if not _docker_available():
        raise RuntimeError("Docker CLI not found; install Docker before building images.")
    if not DOCKERFILE.is_file():
        raise RuntimeError(f"Agent Dockerfile not found: {DOCKERFILE}")

    requirements_path = Path(requirements).expanduser() if requirements else None
    if requirements_path is not None:
        if not requirements_path.is_absolute():
            raise RuntimeError("ORCHESTRATOR_PYTHON_REQUIREMENTS must be an absolute path.")
        if not requirements_path.is_file():
            raise RuntimeError(
                f"Python requirements file does not exist: {requirements_path}"
            )

    with tempfile.TemporaryDirectory(prefix="orchestrator-agent-image-") as temp_dir:
        context = Path(temp_dir)
        shutil.copyfile(DOCKERFILE, context / "Dockerfile.agent")
        _run(build_command(backend, image, context, requirements_path, data))
    print(f"Built agent image: {image}")


def _smoke_command(backend: str, toolchain_data: dict | None = None) -> str:
    data = toolchain_data if toolchain_data is not None else current_toolchain()
    uid, gid = host_identity()
    python_version = tuple(int(part) for part in data["image"]["python_version"].split("."))
    pytest_version = data["tools"]["pytest"]["version"]
    common = (
        f"test \"$(id -u)\" = {uid}; test \"$(id -g)\" = {gid}; "
        "git --version; python --version; "
        f"python -c 'import sys; assert sys.version_info[:2] == {python_version}' "
        f"; test \"$(pytest --version)\" = 'pytest {pytest_version}'"
    )
    if backend == "codex":
        return (
            common
            + f"; test \"$(codex --version)\" = 'codex-cli {data['tools']['codex']['version']}'"
            + "; codex --ask-for-approval never exec --sandbox workspace-write "
            "--cd /workspace --help >/dev/null"
            + "; codex --ask-for-approval never exec --sandbox workspace-write "
            "--cd /workspace --add-dir /review --model parser-check-model "
            "--help >/dev/null"
        )
    if backend == "opencode":
        return (
            common
            + "; opencode --version"
            + "; opencode --help >/dev/null"
        )
    raise ValueError("ORCHESTRATOR_AGENT_BACKEND must be one of: opencode, codex")


def check_image(backend: str, image: str, toolchain_data: dict | None = None) -> None:
    data = toolchain_data if toolchain_data is not None else current_toolchain()
    validate_backend(backend)
    if not _docker_available():
        raise RuntimeError("Docker CLI not found; cannot inspect or smoke-check images.")
    _inspect_image_metadata(backend, image, data)
    _run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "/bin/sh",
            image,
            "-ec",
            _smoke_command(backend, data),
        ]
    )
    print(f"Image smoke check passed: {image} ({backend})")


def _mount_run(image: str, mounts: list[str], script: str) -> None:
    _run(
        [
            "docker",
            "run",
            "--rm",
            *mounts,
            "--entrypoint",
            "/bin/sh",
            image,
            "-ec",
            script,
        ]
    )


def check_mounts(backend: str, image: str, toolchain_data: dict | None = None) -> None:
    data = toolchain_data if toolchain_data is not None else current_toolchain()
    validate_backend(backend)
    if not _docker_available():
        raise RuntimeError("Docker CLI not found; cannot run mount smoke checks.")
    _inspect_image_metadata(backend, image, data)
    uid, gid = host_identity()
    with tempfile.TemporaryDirectory(prefix="orchestrator-mount-smoke-") as temp_dir:
        root = Path(temp_dir)
        workspace = root / "workspace"
        input_dir = root / "input"
        review = root / "review"
        codex_state = root / "codex-state"
        for path in (workspace, input_dir, review, codex_state):
            path.mkdir(mode=0o755)
        (input_dir / "task-probe").write_text("task\n", encoding="utf-8")
        for path in (workspace, input_dir, review, codex_state):
            stat = path.stat()
            if stat.st_uid != uid or stat.st_gid != gid:
                raise RuntimeError(
                    f"Mount smoke directory is not owned by host UID:GID {uid}:{gid}: {path}"
                )

        identity_check = (
            f"test \"$(id -u)\" = {uid}; test \"$(id -g)\" = {gid}; "
        )

        _mount_run(
            image,
            [
                "--mount",
                f"type=bind,src={workspace},dst=/workspace",
                "--mount",
                f"type=bind,src={input_dir},dst=/input,readonly",
            ],
            identity_check
            + "printf developer > /workspace/write-probe; "
            "test \"$(cat /workspace/write-probe)\" = developer; "
            "test \"$(cat /input/task-probe)\" = task; "
            "if printf forbidden > /input/write-probe 2>/dev/null; then "
            "echo '/input unexpectedly writable' >&2; exit 1; fi; "
            "rm /workspace/write-probe",
        )
        _mount_run(
            image,
            [
                "--mount",
                f"type=bind,src={workspace},dst=/workspace,readonly",
                "--mount",
                f"type=bind,src={input_dir},dst=/input,readonly",
                "--mount",
                f"type=bind,src={review},dst=/review",
            ],
            identity_check
            + "if printf forbidden > /workspace/readonly-probe 2>/dev/null; then "
            "echo '/workspace unexpectedly writable' >&2; exit 1; fi; "
            "test \"$(cat /input/task-probe)\" = task; "
            "printf reviewer > /review/write-probe; "
            "test \"$(cat /review/write-probe)\" = reviewer; "
            "rm /review/write-probe",
        )
        _mount_run(
            image,
            ["--mount", f"type=bind,src={workspace},dst=/workspace,readonly"],
            identity_check
            + "if printf forbidden > /workspace/quality-probe 2>/dev/null; then "
            "echo 'quality-gate /workspace unexpectedly writable' >&2; exit 1; fi",
        )
        if backend == "codex":
            _mount_run(
                image,
                ["--mount", f"type=bind,src={codex_state},dst=/codex-state"],
                identity_check
                + "printf state > /codex-state/write-probe; "
                "test \"$(cat /codex-state/write-probe)\" = state; "
                "rm /codex-state/write-probe",
            )
    print(f"Mount smoke checks passed: {image}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("build", help="build the selected backend image")
    check = subparsers.add_parser("check", help="run non-inference image checks")
    check.add_argument("image", nargs="?")
    mounts = subparsers.add_parser("mount-check", help="run mount permission checks")
    mounts.add_argument("image", nargs="?")
    args = parser.parse_args(argv)

    try:
        backend = validate_backend(os.environ.get("ORCHESTRATOR_AGENT_BACKEND", "opencode"))
        configured_image = os.environ.get("ORCHESTRATOR_DOCKER_IMAGE")
        if args.action == "build":
            image = configured_image or default_image_name(backend)
            build_image(
                backend,
                image,
                os.environ.get("ORCHESTRATOR_PYTHON_REQUIREMENTS") or None,
            )
        else:
            image = args.image or configured_image
            if not image:
                raise RuntimeError(
                    "Provide an image argument or set ORCHESTRATOR_DOCKER_IMAGE."
                )
            if args.action == "check":
                check_image(backend, image)
            else:
                check_mounts(backend, image)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Image command failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
