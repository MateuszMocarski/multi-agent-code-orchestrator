from pathlib import Path


HOME = Path.home()
PROJECT_DIR = Path(__file__).resolve().parent

# Keep the existing repository and OpenCode configuration locations.
REPO = HOME / "workspace/agents/local-opencode-lab"
DEVELOPER_CONFIG = HOME / "workspace/agents/config/opencode-qwen35/opencode.json"
REVIEWER_CONFIG = HOME / "workspace/agents/config/opencode-gptoss-reviewer/opencode.json"

INPUT_DIR = PROJECT_DIR / "input"
TASK_FILE = INPUT_DIR / "task.md"
REVIEW_DIR = PROJECT_DIR / "review"
REVIEW_FILE = REVIEW_DIR / "reviewer-feedback.md"
PROMPTS_DIR = PROJECT_DIR / "prompts"
DEVELOPER_PROMPT = PROMPTS_DIR / "developer.txt"
REVIEWER_PROMPT = PROMPTS_DIR / "reviewer.txt"

IMAGE = "opencode-local:2.0.22"
DOCKER_COMMAND = ("sudo", "docker")
OPENCODE_COMMAND = ("opencode", "run", "--standalone", "--auto")
CONTAINER_ENVIRONMENT = (
    "OPENCODE_CONFIG=/config/opencode.json",
    "PYTHONDONTWRITEBYTECODE=1",
    "PYTEST_ADDOPTS=-p no:cacheprovider",
)
