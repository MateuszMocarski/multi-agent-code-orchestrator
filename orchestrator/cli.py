"""Convenient developer CLI for bootstrap, checks, runs, and toolchain tasks."""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import bootstrap, image, toolchain


ROOT = Path(__file__).resolve().parents[1]


def discover_config() -> Path:
    explicit = os.environ.get("ORCHESTRATOR_ENV_FILE")
    if explicit is not None:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise RuntimeError(f"Environment file not found: {path}")
        return path.resolve()
    workspace = ROOT.parent
    candidate = workspace / "config" / "orchestrator.env"
    if ROOT.name == "orchestrator" and (workspace / "projects").is_dir() and candidate.is_file():
        return candidate.resolve()
    raise RuntimeError("No workspace configuration found. Set ORCHESTRATOR_ENV_FILE or run ./agent bootstrap.")


def _run(command: list[str], *, env=None, capture=False) -> subprocess.CompletedProcess:
    return subprocess.run(command, env=env, text=True,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None)


def _with_config(command: list[str]) -> int:
    try:
        env_file = discover_config()
    except RuntimeError as exc:
        print(f"Configuration: FAIL ({exc})", file=sys.stderr)
        return 1
    env = os.environ.copy()
    env["ORCHESTRATOR_ENV_FILE"] = str(env_file)
    return _run(command, env=env).returncode


def _sourced_doctor(env_file: Path) -> int:
    script = 'set -a; . "$1"; exec python3 -m orchestrator.cli _doctor'
    return _run(["bash", "-c", script, "agent", str(env_file)]).returncode


def _docker_image_exists(name: str) -> bool:
    if shutil.which("docker") is None:
        raise RuntimeError("Docker CLI not found")
    result = _run(["docker", "image", "inspect", name, "--format", "{{.Id}}"], capture=True)
    if result.returncode == 0:
        return True
    diagnostic = (result.stderr or "").lower()
    if "no such image" in diagnostic or "image not found" in diagnostic:
        return False
    raise RuntimeError((result.stderr or "Docker image inspection failed").strip())


def _docker_version(image_name: str, executable: str) -> str | None:
    if not _docker_image_exists(image_name):
        return None
    result = _run(["docker", "run", "--rm", "--network", "none", "--entrypoint", executable,
                   image_name, "--version"], capture=True)
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


def _normalized_cli_version(backend: str, output: str | None) -> str | None:
    if backend not in {"codex", "opencode"} or not output:
        return None
    match = re.search(r"(?<![0-9])v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)(?![0-9])", output)
    return match.group(1) if match else None


def _codex_auth_help(image_name: str) -> list[list[str]]:
    def help_output(arguments: list[str]) -> tuple[int, str]:
        result = _run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "codex",
                       image_name, *arguments], capture=True)
        return result.returncode, (result.stdout or "") + (result.stderr or "")

    root_code, root_help = help_output(["--help"])
    if root_code != 0 or not re.search(r"\blogin\b", root_help):
        raise RuntimeError("The bundled Codex CLI does not advertise login support.")
    _, login_help = help_output(["login", "--help"])
    status_code, _ = help_output(["login", "status", "--help"])
    logout_code, _ = help_output(["logout", "--help"])
    login = ["login"]
    if "--device-auth" in login_help:
        login.append("--device-auth")
    status = ["login", "status"] if status_code == 0 else ["login"]
    if status == ["login"]:
        raise RuntimeError("The bundled Codex CLI has no non-destructive login status command.")
    if logout_code != 0 and not re.search(r"\blogout\b", root_help):
        raise RuntimeError("The bundled Codex CLI does not advertise logout support.")
    return [login, status, ["logout"]]


def _auth_command(action: str) -> int:
    try:
        env_file = discover_config()
    except RuntimeError as exc:
        print(f"Authentication unavailable: {exc}", file=sys.stderr)
        return 1
    script = 'set -a; . "$1"; exec python3 -m orchestrator.cli _auth "$2"'
    return _run(["bash", "-c", script, "agent", str(env_file), action]).returncode


def _probe_auth_status() -> tuple[str, int]:
    from . import config
    if config.BACKEND != "codex":
        return "not applicable", 0
    state = os.environ.get("ORCHESTRATOR_CODEX_STATE_DIR")
    if not state or not Path(state).is_absolute() or not Path(state).is_dir():
        return "unknown", 1
    if not config.IMAGE:
        return "unknown", 1
    try:
        commands = _codex_auth_help(config.IMAGE)
    except (RuntimeError, OSError):
        return "unknown", 1
    command = auth_docker_command(config.IMAGE, Path(state), commands[1])
    try:
        result = _run(command, capture=True)
    except OSError:
        return "unknown", 1
    status_text = ((result.stdout or "") + " " + (result.stderr or "")).lower()
    if re.search(r"not logged in|not authenticated|logged out|no auth", status_text):
        return "unauthenticated", 1
    if result.returncode == 0 and re.search(r"logged in|authenticated|signed in", status_text):
        return "authenticated", 0
    return "unknown", 2


def _auth_state_action(action: str) -> int:
    from . import config
    if action == "status":
        state, status_code = _probe_auth_status()
        if state == "not applicable":
            print("Codex authentication is not applicable to the OpenCode backend.")
        elif state == "unknown":
            print("Codex authentication: UNKNOWN (status could not be determined).")
        elif state == "unauthenticated":
            print("Codex authentication: unauthenticated.")
        else:
            print("Codex authentication: authenticated (account details hidden).")
        return status_code
    if config.BACKEND != "codex":
        print("Codex authentication is not applicable to the OpenCode backend.")
        return 0
    state = os.environ.get("ORCHESTRATOR_CODEX_STATE_DIR")
    if not state or not Path(state).is_absolute() or not Path(state).is_dir():
        print("Codex state is not configured as an existing absolute directory.", file=sys.stderr)
        return 1
    if not config.IMAGE:
        print("ORCHESTRATOR_DOCKER_IMAGE is not configured.", file=sys.stderr)
        return 1
    try:
        commands = _codex_auth_help(config.IMAGE)
    except RuntimeError as exc:
        print(f"Codex auth CLI inspection failed: {exc}", file=sys.stderr)
        return 1
    args = commands[0] if action == "login" else commands[2]
    command = auth_docker_command(config.IMAGE, Path(state), args, interactive=action == "login")
    if action == "login":
        print("Starting interactive Codex login in the configured workspace state.", flush=True)
        return _run(command).returncode
    result = _run(command, capture=True)
    # Never forward arbitrary CLI output; it can contain account identifiers.
    if action == "logout":
        if result.returncode == 0:
            print("Codex logout succeeded.")
            return 0
        print("Codex logout failed.", file=sys.stderr)
        return result.returncode or 1
    return result.returncode or 1


def auth_docker_command(image_name: str, state: Path, arguments: list[str], *, interactive: bool = False) -> list[str]:
    command = ["docker", "run", "--rm", "--network", "bridge"]
    if interactive:
        command.append("-i")
        if sys.stdin.isatty():
            command.append("-t")
    command.extend(("--mount", f"type=bind,src={state.resolve()},dst=/codex-state",
                   "-e", "CODEX_HOME=/codex-state", "--entrypoint", "codex", image_name, *arguments))
    return command


def doctor() -> int:
    from . import config
    rows = []
    host_ok = sys.version_info >= (3, 10) and sys.platform.startswith("linux") and shutil.which("git")
    rows.append(("Host", "PASS" if host_ok else "FAIL",
                 f"Python {sys.version.split()[0]}, Git={'available' if shutil.which('git') else 'missing'}"))
    try:
        docker_info = _run(["docker", "info"], capture=True)
        docker_ok = docker_info.returncode == 0
    except OSError:
        docker_ok = False
    rows.append(("Docker daemon", "PASS" if docker_ok else "FAIL",
                 "available" if docker_ok else "not reachable"))
    workspace_config_ok = False
    try:
        config.validate_runtime_configuration(require_task=False)
        workspace_config_ok = True
        rows.append(("Workspace config", "PASS", "paths and backend config valid"))
    except (RuntimeError, OSError, TypeError, ValueError) as exc:
        rows.append(("Workspace config", "FAIL", str(exc)))
    try:
        task_state, task_detail = config.task_readiness()
    except (OSError, TypeError, ValueError) as exc:
        task_state, task_detail = "invalid", f"cannot inspect task path: {exc}"
    task_status = "PASS" if task_state == "ready" else "WARN" if task_state in {"missing", "template"} else "FAIL"
    rows.append(("Task", task_status, task_detail))
    backend = config.BACKEND
    valid_backend = backend in {"codex", "opencode"}
    rows.append(("Backend", "PASS" if valid_backend else "FAIL", str(backend)))
    if valid_backend and workspace_config_ok and backend == "codex":
        model_dev = os.environ.get("ORCHESTRATOR_DEVELOPER_MODEL") or "CLI default"
        model_rev = os.environ.get("ORCHESTRATOR_REVIEWER_MODEL") or "CLI default"
        rows.append(("Models", "PASS", f"developer={model_dev}; reviewer={model_rev}"))
    elif valid_backend and workspace_config_ok:
        rows.append(("Models", "PASS", "selected in OpenCode role configuration files"))
    else:
        rows.append(("Models", "WARN", "not checked (backend or workspace config invalid)"))

    image_exists = None
    if config.IMAGE:
        if not valid_backend or not workspace_config_ok:
            rows.append(("Image", "WARN", "not checked (backend or workspace config invalid)"))
        else:
            try:
                image_exists = _docker_image_exists(config.IMAGE)
            except (RuntimeError, OSError) as exc:
                rows.append(("Image", "FAIL", str(exc)))
            if image_exists is True:
                rows.append(("Image", "PASS", config.IMAGE))
                executable = "codex" if backend == "codex" else "opencode"
                try:
                    installed = _docker_version(config.IMAGE, executable)
                except (RuntimeError, OSError) as exc:
                    installed = None
                    version_detail = f"installed version unavailable ({exc})"
                else:
                    version_detail = ""
                try:
                    expected = toolchain.load()["tools"][backend]["version"]
                except (RuntimeError, OSError, KeyError, TypeError) as exc:
                    rows.append(("Agent CLI", "WARN", f"pinned version unavailable ({exc})"))
                else:
                    actual_version = _normalized_cli_version(backend, installed)
                    if actual_version is None:
                        version_status = "WARN"
                        detail = version_detail or "installed version unavailable"
                        version_detail = f"{detail}; pinned={expected}"
                    else:
                        version_status = "PASS" if actual_version == expected else "WARN"
                        version_detail = f"installed={actual_version}; pinned={expected}"
                    rows.append(("Agent CLI", version_status, version_detail))
            elif image_exists is False:
                rows.append(("Image", "WARN", f"not built: {config.IMAGE}"))
    elif valid_backend and workspace_config_ok:
        rows.append(("Image", "FAIL", "ORCHESTRATOR_DOCKER_IMAGE is not configured"))

    if valid_backend and backend == "codex":
        state = os.environ.get("ORCHESTRATOR_CODEX_STATE_DIR", "")
        state_exists = bool(state) and Path(state).is_absolute() and Path(state).is_dir()
        rows.append(("Codex state", "PASS" if state_exists else "FAIL", state or "not configured"))
        if workspace_config_ok and state_exists and image_exists is True:
            try:
                auth_state, _ = _probe_auth_status()
            except (RuntimeError, OSError, TypeError, ValueError):
                auth_state = "unknown"
            auth_status = "PASS" if auth_state == "authenticated" else "WARN"
            rows.append(("Codex auth", auth_status, auth_state))
        else:
            rows.append(("Codex auth", "WARN", "not checked (workspace config, state, or image unavailable)"))
    for label, status, detail in rows:
        print(f"{status:4} {label}: {detail}")
    return 1 if any(status == "FAIL" for _, status, _ in rows) else 0


def doctor_without_config(reason: str) -> int:
    python_ok = sys.version_info >= (3, 10)
    linux_ok = sys.platform.startswith("linux")
    git_ok = shutil.which("git") is not None
    try:
        docker_ok = _run(["docker", "info"], capture=True).returncode == 0
    except OSError:
        docker_ok = False
    print(f"{'PASS' if python_ok and linux_ok and git_ok else 'FAIL'} Host: Python {sys.version.split()[0]}, Linux={linux_ok}, Git={'available' if git_ok else 'missing'}")
    print(f"{'PASS' if docker_ok else 'FAIL'} Docker daemon: {'available' if docker_ok else 'not reachable'}")
    print(f"FAIL Workspace config: {reason}")
    return 1


def _versions() -> int:
    print(toolchain.versions_report())
    if shutil.which("docker") is None:
        print("  Installed image versions: unavailable (Docker CLI not found)")
        return 0
    selections = []
    try:
        env_file = discover_config()
        script = 'set -a; . "$1"; printf "%s\\0%s" "${ORCHESTRATOR_AGENT_BACKEND:-codex}" "${ORCHESTRATOR_DOCKER_IMAGE:-}"'
        result = _run(["bash", "-c", script, "agent", str(env_file)], capture=True)
        if result.returncode == 0:
            backend, _, configured_image = result.stdout.partition("\0")
            if backend in ("codex", "opencode") and configured_image:
                selections.append((backend, configured_image, "selected"))
    except RuntimeError:
        pass
    for backend in ("codex", "opencode"):
        default_image = image.default_image_name(backend)
        if not any(selected == default_image for _, selected, _ in selections):
            selections.append((backend, default_image, "default"))
    for backend, selected, origin in selections:
        try:
            installed = _docker_version(selected, backend)
            state = installed if installed else "not built"
        except (RuntimeError, OSError) as exc:
            state = f"unavailable ({exc})"
        print(f"  {backend} image {selected} ({origin}): {state}")
    return 0


def _toolchain_check() -> int:
    try:
        for name, pinned, latest in toolchain.check_updates():
            state = toolchain.update_state(pinned, latest)
            print(f"{name}: {state} (pinned {pinned}; latest stable {latest})")
    except (RuntimeError, OSError) as exc:
        print(f"Toolchain check failed: {exc}", file=sys.stderr)
        return 1
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent", description="Bootstrap and operate an isolated agent workspace.")
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("bootstrap", help="prepare a reproducible workspace without inference")
    subs.add_parser("check", help="validate workspace safely; never runs agents")
    subs.add_parser("run", help="run the real developer/reviewer workflow (uses inference)")
    subs.add_parser("doctor", help="report workspace readiness without inference")
    auth = subs.add_parser("auth", help="manage Codex auth in configured workspace state")
    auth.add_argument("action", choices=("status", "login", "logout"))
    subs.add_parser("versions", help="show pinned and installed image versions")
    tool = subs.add_parser("toolchain", help="check or explicitly update pinned versions")
    tool_sub = tool.add_subparsers(dest="tool_action", required=True)
    tool_sub.add_parser("check", help="query registries for newer stable releases")
    update_parser = tool_sub.add_parser("update", help="resolve and pin latest stable releases")
    update_parser.add_argument("components", nargs="*", choices=("codex", "opencode", "pytest", "all"))
    return parser


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "_doctor":
        return doctor()
    if argv and argv[0] == "_auth":
        if len(argv) == 2 and argv[1] in {"status", "login", "logout"}:
            return _auth_state_action(argv[1])
        return 2
    # Let bootstrap own its complete parser, including its detailed --help.
    if argv and argv[0] == "bootstrap":
        return bootstrap.main(argv[1:])
    args = make_parser().parse_args(argv)
    if args.command == "check":
        return _with_config([str(ROOT / "run.sh"), "--check"])
    if args.command == "run":
        print("Starting real developer/reviewer workflow; this invokes agent inference.", flush=True)
        return _with_config([str(ROOT / "run.sh")])
    if args.command == "doctor":
        try:
            return _sourced_doctor(discover_config())
        except RuntimeError as exc:
            return doctor_without_config(str(exc))
    if args.command == "auth":
        return _auth_command(args.action)
    if args.command == "versions":
        return _versions()
    if args.command == "toolchain":
        if args.tool_action == "check":
            return _toolchain_check()
        selected = tuple(args.components or ("codex", "opencode", "pytest"))
        if "all" in selected:
            selected = ("codex", "opencode", "pytest")
        candidate_pins_written = False
        validation_started = False
        toolchain_before = None
        try:
            toolchain_before = toolchain.LOCK_FILE.read_bytes()
            outcomes = toolchain.update(selected=selected)
            candidate_pins_written = any(state == "UPDATE AVAILABLE" for _, _, _, state in outcomes)
            updated_toolchain = toolchain.load()
            for name, pinned, candidate, state in outcomes:
                print(f"{name}: {state} (pinned {pinned}; registry latest {candidate})")
            print("Toolchain update evaluation complete; equal or ahead pins were preserved.", flush=True)
            validation_started = True
            subprocess.run([sys.executable, "-m", "unittest", "discover", "-v"], cwd=ROOT, check=True)
            for backend in ("codex", "opencode"):
                selected_image = image.default_image_name(backend, updated_toolchain)
                image.build_image(backend, selected_image, None, updated_toolchain)
                image.check_image(backend, selected_image, updated_toolchain)
            print("Unit and image smoke checks passed. Review the toolchain.json diff.")
            return 0
        except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as exc:
            lock_changed = candidate_pins_written
            lock_state_unknown = False
            if toolchain_before is not None:
                try:
                    lock_changed = toolchain.LOCK_FILE.read_bytes() != toolchain_before
                except OSError:
                    lock_state_unknown = True
            if lock_changed:
                if validation_started:
                    detail = "Candidate pins remain in toolchain.json; validation failed. Review the lock and diagnostics."
                else:
                    detail = "Candidate pins remain in toolchain.json; validation did not start. Review the lock and diagnostics."
            elif lock_state_unknown:
                detail = "toolchain.json could not be checked after failure; inspect the lock before proceeding."
            elif validation_started:
                detail = "Validation failed; no higher candidate pins were written and toolchain.json was not changed."
            else:
                detail = "Toolchain update failed before validation; no candidate pins were written to toolchain.json."
            print(f"Toolchain update failed: {exc}. {detail}", file=sys.stderr)
            return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
