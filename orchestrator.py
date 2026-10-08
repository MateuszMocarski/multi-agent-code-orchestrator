from pathlib import Path
import subprocess
import sys


HOME = Path.home()

REPO = HOME / "workspace/agents/local-opencode-lab"

DEVELOPER_CONFIG = (
    HOME / "workspace/agents/config/opencode-qwen35/opencode.json"
)

REVIEWER_CONFIG = (
    HOME / "workspace/agents/config/opencode-gptoss-reviewer/opencode.json"
)

REVIEW_DIR = HOME / "workspace/agents/review"
REVIEW_FILE = REVIEW_DIR / "reviewer-feedback.md"

IMAGE = "opencode-local:2.0.22"


def run_developer(prompt: str) -> int:
    command = [
        "sudo",
        "docker",
        "run",
        "--rm",
        "--name",
        "orchestrator-developer",
        "--network",
        "host",
        "-e",
        "OPENCODE_CONFIG=/config/opencode.json",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        "-e",
        "PYTEST_ADDOPTS=-p no:cacheprovider",
        "--mount",
        f"type=bind,src={REPO},dst=/workspace",
        "--mount",
        (
            f"type=bind,src={DEVELOPER_CONFIG},"
            "dst=/config/opencode.json,readonly"
        ),
        IMAGE,
        "opencode",
        "run",
        "--standalone",
        "--auto",
        prompt,
    ]

    print("=== DEVELOPER START ===")

    result = subprocess.run(command)

    print(f"=== DEVELOPER EXIT CODE: {result.returncode} ===")

    return result.returncode


def run_reviewer(prompt: str) -> int:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)

    # Usuń poprzedni raport, żeby nie zaakceptować starego wyniku.
    REVIEW_FILE.unlink(missing_ok=True)

    command = [
        "sudo",
        "docker",
        "run",
        "--rm",
        "--name",
        "orchestrator-reviewer",
        "--network",
        "host",
        "-e",
        "OPENCODE_CONFIG=/config/opencode.json",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        "-e",
        "PYTEST_ADDOPTS=-p no:cacheprovider",
        "--mount",
        f"type=bind,src={REPO},dst=/workspace,readonly",
        "--mount",
        f"type=bind,src={REVIEW_DIR},dst=/review",
        "--mount",
        (
            f"type=bind,src={REVIEWER_CONFIG},"
            "dst=/config/opencode.json,readonly"
        ),
        IMAGE,
        "opencode",
        "run",
        "--standalone",
        "--auto",
        prompt,
    ]

    print("=== REVIEWER START ===")

    result = subprocess.run(command)

    print(f"=== REVIEWER EXIT CODE: {result.returncode} ===")

    return result.returncode


def read_verdict() -> str:
    if not REVIEW_FILE.exists():
        raise RuntimeError(
            f"Reviewer finished, but {REVIEW_FILE} does not exist."
        )

    lines = REVIEW_FILE.read_text(encoding="utf-8").splitlines()

    if not lines:
        raise RuntimeError("Reviewer feedback file is empty.")

    first_line = lines[0].strip()

    if first_line == "VERDICT: APPROVED":
        return "APPROVED"

    if first_line == "VERDICT: CHANGES_REQUESTED":
        return "CHANGES_REQUESTED"

    raise RuntimeError(
        f"Invalid reviewer verdict: {first_line!r}"
    )


def main() -> int:
    developer_prompt = """
You are the developer agent.

Read the complete task from:

/input/task.md

Inspect /workspace and implement the task completely.

Requirements:
- Actually use tools.
- Do not stage or commit anything.
- Run all validation required by the task.
- Do not claim completion without verifying the repository state.
""".strip()

    reviewer_prompt = """
You are the reviewer agent.

Inspect /workspace using actual tools.

Do not modify anything in /workspace.

For this smoke test:
- run pwd
- run git status --short
- verify that you can inspect the repository

Then create this file using shell/exec:

/review/reviewer-feedback.md

The FIRST LINE must be exactly:

VERDICT: APPROVED

After creating the file, use shell/exec to run:

test -f /review/reviewer-feedback.md
head -n 1 /review/reviewer-feedback.md
cat /review/reviewer-feedback.md

Do not use the Write or Edit tools for /review.
Actually create and verify the file.
Your task is not complete until the file exists and has been read back.
""".strip()

    developer_exit_code = run_developer(developer_prompt)

    if developer_exit_code != 0:
        print("Developer run failed.")
        return 1

    reviewer_exit_code = run_reviewer(reviewer_prompt)

    if reviewer_exit_code != 0:
        print("Reviewer run failed.")
        return 1

    try:
        verdict = read_verdict()
    except RuntimeError as exc:
        print(f"Reviewer output contract failed: {exc}")
        return 1

    print(f"=== REVIEW VERDICT: {verdict} ===")

    if verdict == "APPROVED":
        print("Workflow completed successfully.")
        return 0

    print("Reviewer requested changes.")
    return 2


if __name__ == "__main__":
    sys.exit(main())