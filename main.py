import sys

from config import (
    DEVELOPER_FIX_PROMPT,
    DEVELOPER_PROMPT,
    MAX_REVIEW_ROUNDS,
    REVIEWER_FIX_OUTPUT_PROMPT,
    REVIEWER_PROMPT,
)
from developer import run_developer
from quality_gates import run_git_diff_check, run_pytest
from reviewer import read_verdict, run_reviewer


def main() -> int:
    developer_prompt = DEVELOPER_PROMPT.read_text(encoding="utf-8").strip()
    developer_exit_code = run_developer(developer_prompt)
    if developer_exit_code != 0:
        print("Developer run failed.")
        return 1

    reviewer_prompt = REVIEWER_PROMPT.read_text(encoding="utf-8").strip()
    reviewer_fix_output_prompt = REVIEWER_FIX_OUTPUT_PROMPT.read_text(
        encoding="utf-8"
    ).strip()
    correction_prompt = DEVELOPER_FIX_PROMPT.read_text(encoding="utf-8").strip()

    for review_round in range(1, MAX_REVIEW_ROUNDS + 1):
        if run_pytest() != 0:
            print("Pytest quality gate failed.")
            return 1

        if run_git_diff_check() != 0:
            print("Git diff quality gate failed.")
            return 1

        reviewer_exit_code = run_reviewer(reviewer_prompt)
        if reviewer_exit_code != 0:
            print("Reviewer run failed.")
            return 1

        try:
            verdict = read_verdict()
        except RuntimeError as exc:
            print(f"Reviewer output contract failed: {exc}")
            print("Retrying reviewer output recovery.")

            reviewer_exit_code = run_reviewer(reviewer_fix_output_prompt)
            if reviewer_exit_code != 0:
                print("Reviewer output recovery run failed.")
                return 1

            try:
                verdict = read_verdict()
            except RuntimeError as retry_exc:
                print(f"Reviewer output recovery failed: {retry_exc}")
                print(
                    "Reviewer output contract could not be recovered. "
                    "Human intervention is required."
                )
                return 4

        print(f"=== REVIEW ROUND {review_round}: {verdict} ===")
        if verdict == "APPROVED":
            print("Workflow completed successfully.")
            return 0

        if review_round == MAX_REVIEW_ROUNDS:
            print("Review limit reached without approval. Human intervention is required.")
            return 3

        print("Reviewer requested changes. Starting a correction round.")
        developer_exit_code = run_developer(correction_prompt, review_read_only=True)
        if developer_exit_code != 0:
            print("Developer correction run failed.")
            return 1

    raise RuntimeError("Review loop ended unexpectedly.")


if __name__ == "__main__":
    sys.exit(main())
