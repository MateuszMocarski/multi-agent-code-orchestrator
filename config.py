import os
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent


def environment_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else None


REPO = environment_path("ORCHESTRATOR_TARGET_REPO")
DEVELOPER_CONFIG = environment_path("ORCHESTRATOR_DEVELOPER_CONFIG")
REVIEWER_CONFIG = environment_path("ORCHESTRATOR_REVIEWER_CONFIG")

INPUT_DIR = PROJECT_DIR / "input"
TASK_FILE = INPUT_DIR / "task.md"
REVIEW_DIR = PROJECT_DIR / "review"
REVIEW_FILE = REVIEW_DIR / "reviewer-feedback.md"
PROMPTS_DIR = PROJECT_DIR / "prompts"
DEVELOPER_PROMPT = PROMPTS_DIR / "developer.txt"
DEVELOPER_FIX_PROMPT = PROMPTS_DIR / "developer_fix.txt"
REVIEWER_PROMPT = PROMPTS_DIR / "reviewer.txt"
REVIEWER_FIX_OUTPUT_PROMPT = PROMPTS_DIR / "reviewer_fix_output.txt"
MAX_REVIEW_ROUNDS = int(os.environ.get("ORCHESTRATOR_MAX_REVIEW_ROUNDS", "4"))

IMAGE = os.environ.get("ORCHESTRATOR_DOCKER_IMAGE")
DOCKER_COMMAND = ("docker",)
OPENCODE_COMMAND = ("opencode", "run", "--standalone", "--auto")
CONTAINER_ENVIRONMENT = (
    "OPENCODE_CONFIG=/config/opencode.json",
    "PYTHONDONTWRITEBYTECODE=1",
    "PYTEST_ADDOPTS=-p no:cacheprovider",
)


def validate_runtime_configuration() -> None:
    required_paths = {
        "ORCHESTRATOR_TARGET_REPO": REPO,
        "ORCHESTRATOR_DEVELOPER_CONFIG": DEVELOPER_CONFIG,
        "ORCHESTRATOR_REVIEWER_CONFIG": REVIEWER_CONFIG,
    }
    missing = [name for name, path in required_paths.items() if path is None]
    if not IMAGE:
        missing.append("ORCHESTRATOR_DOCKER_IMAGE")
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    invalid = [
        name
        for name, path in required_paths.items()
        if path is not None and not path.exists()
    ]
    if invalid:
        raise RuntimeError(f"Configured paths do not exist: {', '.join(invalid)}")

    if MAX_REVIEW_ROUNDS < 1:
        raise RuntimeError("ORCHESTRATOR_MAX_REVIEW_ROUNDS must be at least 1.")

    if not TASK_FILE.exists():
        raise RuntimeError(f"Task file does not exist: {TASK_FILE}")
