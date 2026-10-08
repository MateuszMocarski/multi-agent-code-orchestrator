from config import (
    CONTAINER_ENVIRONMENT,
    DOCKER_COMMAND,
    IMAGE,
    REPO,
)
from docker_runner import run_container


def run_quality_gate(name: str, container_command: list[str]) -> int:
    command = [
        *DOCKER_COMMAND,
        "run",
        "--rm",
        "--network",
        "host",
    ]

    for environment in CONTAINER_ENVIRONMENT:
        command.extend(("-e", environment))

    command.extend(
        (
            "--mount",
            f"type=bind,src={REPO},dst=/workspace,readonly",
            IMAGE,
            *container_command,
        )
    )
    return run_container(f"QUALITY GATE: {name}", command)


def run_pytest() -> int:
    """Run the repository test suite."""
    return run_quality_gate("PYTEST", ["python", "-m", "pytest", "/workspace"])


def run_git_diff_check() -> int:
    """Check the repository diff for whitespace errors."""
    return run_quality_gate(
        "GIT DIFF CHECK",
        ["git", "-C", "/workspace", "diff", "--check"],
    )
