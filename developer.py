from config import (
    CONTAINER_ENVIRONMENT,
    DEVELOPER_CONFIG,
    DOCKER_COMMAND,
    IMAGE,
    INPUT_DIR,
    OPENCODE_COMMAND,
    REPO,
    REVIEW_DIR,
)
from docker_runner import run_container


def run_developer(prompt: str, review_read_only: bool = False) -> int:
    command = [
        *DOCKER_COMMAND,
        "run",
        "--rm",
        "--name",
        "orchestrator-developer",
        "--network",
        "host",
    ]

    for environment in CONTAINER_ENVIRONMENT:
        command.extend(("-e", environment))

    command.extend(
        (
            "--mount",
            f"type=bind,src={REPO},dst=/workspace",
            "--mount",
            f"type=bind,src={INPUT_DIR},dst=/input,readonly",
        )
    )

    if review_read_only:
        command.extend(("--mount", f"type=bind,src={REVIEW_DIR},dst=/review,readonly"))

    command.extend(
        (
            "--mount",
            f"type=bind,src={DEVELOPER_CONFIG},dst=/config/opencode.json,readonly",
            IMAGE,
            *OPENCODE_COMMAND,
            prompt,
        )
    )

    return run_container("DEVELOPER", command)
