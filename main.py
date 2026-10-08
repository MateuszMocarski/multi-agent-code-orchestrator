import sys

from config import DEVELOPER_PROMPT, REVIEWER_PROMPT
from developer import run_developer
from quality_gates import run_git_diff_check, run_pytest
from reviewer import read_verdict, run_reviewer


def main() -> int:
    developer_prompt = DEVELOPER_PROMPT.read_text(encoding="utf-8").strip()
    developer_exit_code = run_developer(developer_prompt)
    if developer_exit_code != 0:
        print("Developer run failed.")
        return 1

    if run_pytest() != 0:
        print("Pytest quality gate failed.")
        return 1

    if run_git_diff_check() != 0:
        print("Git diff quality gate failed.")
        return 1

    reviewer_prompt = REVIEWER_PROMPT.read_text(encoding="utf-8").strip()
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
