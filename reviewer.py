from config import REVIEW_DIR, REVIEW_FILE
from container_command import build_agent_command
from docker_runner import run_container


def run_reviewer(prompt: str) -> int:
    """Clear stale output, then run the read-only workspace reviewer."""
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    REVIEW_FILE.unlink(missing_ok=True)

    command = build_agent_command("reviewer", prompt)
    return run_container("REVIEWER", command)


def read_verdict() -> str:
    """Read the required first-line verdict without accepting near matches."""
    if not REVIEW_FILE.exists():
        raise RuntimeError(f"Reviewer finished, but {REVIEW_FILE} does not exist.")

    lines = REVIEW_FILE.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise RuntimeError("Reviewer feedback file is empty.")

    first_line = lines[0]
    verdicts = {
        "VERDICT: APPROVED": "APPROVED",
        "VERDICT: CHANGES_REQUESTED": "CHANGES_REQUESTED",
    }
    try:
        return verdicts[first_line]
    except KeyError as exc:
        raise RuntimeError(f"Invalid reviewer verdict: {first_line!r}") from exc
