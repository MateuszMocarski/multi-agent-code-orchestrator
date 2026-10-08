from config import (
    CONTAINER_ENVIRONMENT,
    DEVELOPER_CONFIG,
    DOCKER_COMMAND,
    IMAGE,
    INPUT_DIR,
    OPENCODE_COMMAND,
    REPO,
)
from docker_runner import run_container


def run_developer(prompt: str) -> int:
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
            "--mount",
            f"type=bind,src={DEVELOPER_CONFIG},dst=/config/opencode.json,readonly",
            IMAGE,
            *OPENCODE_COMMAND,
            prompt,
        )
    )

    return run_container("DEVELOPER", command)
