"""Construct agent containers while keeping role mounts backend-independent."""

from agent_runtime import runtime_spec
from config import (
    COMMON_AGENT_ENVIRONMENT,
    DOCKER_COMMAND,
    IMAGE,
    INPUT_DIR,
    REPO,
    REVIEW_DIR,
)


def build_agent_command(
    role: str, prompt: str, *, review_read_only: bool = False
) -> list[str]:
    if role not in {"developer", "reviewer"}:
        raise ValueError(f"Unsupported agent role: {role}")

    spec = runtime_spec(role)
    command = [*DOCKER_COMMAND, "run", "--rm", *spec.docker_options]
    for environment in (*COMMON_AGENT_ENVIRONMENT, *spec.environment):
        command.extend(("-e", environment))

    if role == "developer":
        command.extend(
            (
                "--mount",
                f"type=bind,src={REPO},dst=/workspace",
                "--mount",
                f"type=bind,src={INPUT_DIR},dst=/input,readonly",
            )
        )
        if review_read_only:
            command.extend(
                ("--mount", f"type=bind,src={REVIEW_DIR},dst=/review,readonly")
            )
    else:
        command.extend(
            (
                "--mount",
                f"type=bind,src={REPO},dst=/workspace,readonly",
                "--mount",
                f"type=bind,src={INPUT_DIR},dst=/input,readonly",
                "--mount",
                f"type=bind,src={REVIEW_DIR},dst=/review",
            )
        )

    for mount in spec.mounts:
        command.extend(("--mount", mount))
    command.extend((IMAGE, *spec.command, prompt))
    return command
