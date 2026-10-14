import os
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]


def environment_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else None


REPO = environment_path("ORCHESTRATOR_TARGET_REPO")
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
try:
    MAX_REVIEW_ROUNDS = int(os.environ.get("ORCHESTRATOR_MAX_REVIEW_ROUNDS", "4"))
except ValueError:
    MAX_REVIEW_ROUNDS = 0

BACKEND = os.environ.get("ORCHESTRATOR_AGENT_BACKEND", "opencode")
IMAGE = os.environ.get("ORCHESTRATOR_DOCKER_IMAGE")
DOCKER_COMMAND = ("docker",)
COMMON_AGENT_ENVIRONMENT = ("PYTHONDONTWRITEBYTECODE=1",)
QUALITY_GATE_ENVIRONMENT = ("PYTHONDONTWRITEBYTECODE=1", "PYTEST_ADDOPTS=-p no:cacheprovider")


def _require_path(name: str, path: Path | None, *, kind: str) -> None:
    if path is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    if not path.is_absolute():
        raise RuntimeError(f"{name} must be an absolute path.")
    if not path.exists():
        raise RuntimeError(f"Configured path does not exist: {name}={path}")
    if kind == "directory" and not path.is_dir():
        raise RuntimeError(f"{name} must be a directory.")
    if kind == "file" and not path.is_file():
        raise RuntimeError(f"{name} must be a file.")


def validate_runtime_configuration() -> None:
    if BACKEND not in {"opencode", "codex"}:
        raise RuntimeError(
            "ORCHESTRATOR_AGENT_BACKEND must be one of: opencode, codex."
        )
    if not IMAGE:
        raise RuntimeError("Missing required environment variable: ORCHESTRATOR_DOCKER_IMAGE")

    _require_path("ORCHESTRATOR_TARGET_REPO", REPO, kind="directory")
    _require_path("ORCHESTRATOR_RUNTIME_DIR", RUNTIME_DIR, kind="directory")
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

    _require_path("ORCHESTRATOR_RUNTIME_DIR/input/task.md", TASK_FILE, kind="file")
    template_file = PROJECT_DIR / "input" / "task.example.md"
    if TASK_FILE is not None and TASK_FILE.read_bytes() == template_file.read_bytes():
        raise RuntimeError(
            "Task file is unchanged from input/task.example.md; edit it before running."
        )

    if BACKEND == "opencode":
        from .agent_runtime import validate_opencode_configuration

        validate_opencode_configuration()
    elif BACKEND == "codex":
        from .agent_runtime import validate_codex_configuration

        validate_codex_configuration()


def run_preflight() -> int:
    try:
        validate_runtime_configuration()
    except (RuntimeError, OSError) as exc:
        print(f"Preflight failed: {exc}")
        return 1

    print("Configuration: OK")
    print(f"Backend: {BACKEND}")
    print("Target repository: OK")
    print("Runtime directory: OK")
    print("Task: OK")
    print(f"Docker image: {IMAGE}")
    print("Preflight complete.")
    return 0
