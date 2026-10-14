from .container_command import build_agent_command
from .docker_runner import run_container


def run_developer(prompt: str, review_read_only: bool = False) -> int:
    command = build_agent_command(
        "developer", prompt, review_read_only=review_read_only
    )
    return run_container("DEVELOPER", command)
