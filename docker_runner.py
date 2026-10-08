import subprocess


def run_container(label: str, command: list[str]) -> int:
    """Run a Docker command and report its exit status."""
    print(f"=== {label} START ===")
    result = subprocess.run(command)
    print(f"=== {label} EXIT CODE: {result.returncode} ===")
    return result.returncode
