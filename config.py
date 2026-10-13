import os
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent


def environment_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else None


REPO = environment_path("ORCHESTRATOR_TARGET_REPO")
DEVELOPER_CONFIG = environment_path("ORCHESTRATOR_DEVELOPER_CONFIG")
REVIEWER_CONFIG = environment_path("ORCHESTRATOR_REVIEWER_CONFIG")
RUNTIME_DIR = environment_path("ORCHESTRATOR_RUNTIME_DIR")

INPUT_DIR = RUNTIME_DIR / "input" if RUNTIME_DIR else None
TASK_FILE = INPUT_DIR / "task.md" if INPUT_DIR else None
REVIEW_DIR = RUNTIME_DIR / "review" if RUNTIME_DIR else None
REVIEW_FILE = REVIEW_DIR / "reviewer-feedback.md" if REVIEW_DIR else None
OUTPUT_DIR = RUNTIME_DIR / "output" if RUNTIME_DIR else None
LOGS_DIR = RUNTIME_DIR / "logs" if RUNTIME_DIR else None
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
        "ORCHESTRATOR_RUNTIME_DIR": RUNTIME_DIR,
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

    for name, path in (
        ("ORCHESTRATOR_TARGET_REPO", REPO),
        ("ORCHESTRATOR_DEVELOPER_CONFIG", DEVELOPER_CONFIG),
        ("ORCHESTRATOR_REVIEWER_CONFIG", REVIEWER_CONFIG),
    ):
        if path is not None and not path.is_absolute():
            raise RuntimeError(f"{name} must be an absolute path.")
    if REPO is not None and not REPO.is_dir():
        raise RuntimeError("ORCHESTRATOR_TARGET_REPO must be a directory.")
    for name, path in (
        ("ORCHESTRATOR_DEVELOPER_CONFIG", DEVELOPER_CONFIG),
        ("ORCHESTRATOR_REVIEWER_CONFIG", REVIEWER_CONFIG),
    ):
        if path is not None and not path.is_file():
            raise RuntimeError(f"{name} must be a file.")

    if RUNTIME_DIR is not None and not RUNTIME_DIR.is_absolute():
        raise RuntimeError("ORCHESTRATOR_RUNTIME_DIR must be an absolute path.")
    if RUNTIME_DIR is not None:
        try:
            RUNTIME_DIR.resolve().relative_to(PROJECT_DIR.resolve())
        except ValueError:
            pass
        else:
            raise RuntimeError(
                "ORCHESTRATOR_RUNTIME_DIR must be outside the orchestrator source directory."
            )

    if MAX_REVIEW_ROUNDS < 1:
        raise RuntimeError("ORCHESTRATOR_MAX_REVIEW_ROUNDS must be at least 1.")

    if TASK_FILE is None or not TASK_FILE.exists():
        raise RuntimeError(f"Task file does not exist: {TASK_FILE}")
    if not TASK_FILE.is_file():
        raise RuntimeError(f"Task path is not a file: {TASK_FILE}")

    template_file = PROJECT_DIR / "input" / "task.example.md"
    if TASK_FILE.read_bytes() == template_file.read_bytes():
        raise RuntimeError(
            "Task file is unchanged from input/task.example.md; edit it before running."
        )


def run_preflight() -> int:
    try:
        validate_runtime_configuration()
    except (RuntimeError, OSError) as exc:
        print(f"Preflight failed: {exc}")
        return 1

    print("Configuration: OK")
    print("Target repository: OK")
    print("Runtime directory: OK")
    print("Task: OK")
    print(f"Docker image: {IMAGE}")
    print("Preflight complete.")
    return 0
