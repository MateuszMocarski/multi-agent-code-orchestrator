#!/usr/bin/env python3
"""Prepare an external, reproducible workspace for the orchestrator."""

import argparse
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import image


@dataclass(frozen=True)
class Layout:
    workspace: Path
    orchestrator: Path
    config_dir: Path
    env_file: Path
    projects: Path
    project: Path
    target_repo: Path
    runtime: Path
    state: Path


def plan(workspace: Path, target_name: str) -> Layout:
    root = workspace.expanduser()
    if not root.is_absolute():
        raise ValueError("--workspace must be an absolute path")
    root = Path(os.path.abspath(root))
    if not target_name or target_name in {".", ".."} or any(c in target_name for c in "/\\\0"):
        raise ValueError("Target name must be a single safe directory name")
    project = root / "projects" / target_name
    return Layout(root, root / "orchestrator", root / "config", root / "config" / "orchestrator.env",
                  root / "projects", project, project / "repo", project / "runtime", root / "state")


def derive_target_name(source: str) -> str:
    raw = source.rstrip("/\\").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    name = raw[:-4] if raw.lower().endswith(".git") else raw
    if not name or name in {".", ".."} or any(c in name for c in "/\\\0"):
        raise ValueError(f"Cannot derive a safe target name from repository source: {source}")
    return name


def run(command: list[str], *, cwd: Path | None = None, env=None, capture: bool = True) -> str:
    try:
        result = subprocess.run(
            command, cwd=cwd, env=env, check=True, text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Required tool not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or exc.stdout or "").strip()
        suffix = f": {details}" if details else f" (exit code {exc.returncode})"
        raise RuntimeError(f"Command failed ({shlex.join(command)}){suffix}") from exc
    return result.stdout.strip() if capture and result.stdout else ""


def check_prerequisites() -> None:
    if platform.system() != "Linux":
        raise RuntimeError("Prerequisite check: run on Linux or WSL with a Linux Python environment.")
    if sys.version_info < (3, 10):
        raise RuntimeError("Prerequisite check: Python 3.10 or newer is required.")
    if not hasattr(os, "getuid") or os.getuid() == 0:
        raise RuntimeError("Prerequisite check: run as a non-root Linux user; sudo is not required.")
    for tool in ("git", "docker"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"Prerequisite check: {tool} CLI not found; install/configure it before bootstrap.")
    run(["git", "--version"])
    run(["docker", "--version"])
    run(["docker", "info"])


def is_git_repo(path: Path) -> bool:
    return path.is_dir() and (path / ".git").exists() and subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def repository_head(path: Path) -> str:
    return run(["git", "-C", str(path), "rev-parse", "HEAD^{commit}"])


def resolve_local_ref(repository: Path, ref: str) -> str:
    if ref.startswith(("refs/heads/", "refs/tags/", "refs/remotes/")):
        return run(["git", "-C", str(repository), "rev-parse", f"{ref}^{{commit}}"])
    candidates = []
    for full_ref in (f"refs/heads/{ref}", f"refs/tags/{ref}"):
        result = subprocess.run(
            ["git", "-C", str(repository), "show-ref", "--verify", "--hash", full_ref],
            text=True, capture_output=True,
        )
        if result.returncode == 0:
            commit = run([
                "git", "-C", str(repository), "rev-parse", f"{full_ref}^{{commit}}"
            ])
            candidates.append((full_ref, commit))
    if len(candidates) > 1:
        raise RuntimeError(f"Ref {ref!r} is ambiguous in {repository}; use a full refs/heads/... or refs/tags/... name")
    if candidates:
        return candidates[0][1]
    return run(["git", "-C", str(repository), "rev-parse", f"{ref}^{{commit}}"])


def normalize_source(source: str) -> str:
    path = Path(source).expanduser()
    if path.exists():
        return str(path.resolve())
    return source.rstrip("/")


def verify_repository_identity(repository: Path, expected_sources: set[str], description: str) -> None:
    try:
        origin = run(["git", "-C", str(repository), "remote", "get-url", "origin"])
    except RuntimeError as exc:
        raise RuntimeError(
            f"{description} repository has no usable origin at {repository}; "
            "the existing repository was left unchanged."
        ) from exc
    actual = normalize_source(origin)
    expected = {normalize_source(source) for source in expected_sources if source}
    if actual not in expected:
        choices = ", ".join(sorted(expected)) or "(none)"
        raise RuntimeError(
            f"{description} repository identity mismatch at {repository}: origin is {origin!r}; "
            f"expected one of: {choices}. Existing origin and repository were left unchanged."
        )


def expected_source_commit(source: str, ref: str | None, description: str = "Git source") -> str:
    source_path = Path(source).expanduser()
    if source_path.exists():
        try:
            return resolve_local_ref(source_path, ref or "HEAD")
        except RuntimeError as exc:
            raise RuntimeError(f"Cannot resolve {description} ref {ref or 'HEAD'!r} in {source_path}: {exc}") from exc

    if ref:
        if ref.startswith("refs/heads/") or ref.startswith("refs/tags/"):
            query_refs = [ref, f"{ref}^{{}}"] if ref.startswith("refs/tags/") else [ref]
        elif ref.startswith("refs/"):
            query_refs = [ref]
        else:
            query_refs = [f"refs/heads/{ref}", f"refs/tags/{ref}", f"refs/tags/{ref}^{{}}"]
        output = run(["git", "ls-remote", source, *query_refs])
        refs = {}
        for line in output.splitlines():
            fields = line.split()
            if len(fields) == 2:
                refs[fields[1]] = fields[0]
        if ref.startswith("refs/heads/"):
            matches = [refs[ref]] if ref in refs else []
        elif ref.startswith("refs/tags/"):
            peeled = f"{ref}^{{}}"
            matches = [refs.get(peeled, refs[ref])] if ref in refs else []
        elif ref.startswith("refs/"):
            matches = [refs[ref]] if ref in refs else []
        else:
            branch = refs.get(f"refs/heads/{ref}")
            tag_ref = f"refs/tags/{ref}"
            tag = refs.get(f"{tag_ref}^{{}}", refs.get(tag_ref))
            matches = [candidate for candidate in (branch, tag) if candidate]
        if len(matches) > 1:
            raise RuntimeError(
                f"{description} ref {ref!r} is ambiguous in {source}; use a full refs/heads/... or refs/tags/... name"
            )
        if matches:
            return matches[0]
        raise RuntimeError(f"Cannot resolve {description} ref {ref!r} from source {source}")

    output = run(["git", "ls-remote", "--symref", source, "HEAD"])
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1] == "HEAD" and fields[0] != "ref:":
            return fields[0]
    raise RuntimeError(f"Cannot resolve default branch commit from {description.lower()}: {source}")


def verify_revision(repository: Path, expected: str, description: str) -> None:
    actual = repository_head(repository)
    if actual != expected:
        raise RuntimeError(
            f"{description} revision mismatch at {repository}: expected {expected}, current HEAD is {actual}. "
            "The existing repository was left unchanged."
        )


def clone_repo(source: str, destination: Path, ref: str | None = None) -> None:
    if destination.exists():
        if destination.is_symlink():
            raise RuntimeError(f"Repository destination must not be a symbolic link: {destination}")
        if not is_git_repo(destination):
            raise RuntimeError(f"Repository destination exists but is not a Git repository: {destination}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", source, str(destination)], capture=False)
    if ref:
        run(["git", "-C", str(destination), "checkout", ref], capture=False)


def shell_value(value: str | Path) -> str:
    return shlex.quote(str(value))


def config_text(layout: Layout, backend: str, selected_image: str, codex_state: Path | None,
                dev_config: Path | None, rev_config: Path | None, requirements: Path | None) -> str:
    values = {
        "ORCHESTRATOR_TARGET_REPO": layout.target_repo,
        "ORCHESTRATOR_RUNTIME_DIR": layout.runtime,
        "ORCHESTRATOR_DOCKER_IMAGE": selected_image,
        "ORCHESTRATOR_AGENT_BACKEND": backend,
        "ORCHESTRATOR_MAX_REVIEW_ROUNDS": "4",
    }
    if backend == "codex":
        values["ORCHESTRATOR_CODEX_STATE_DIR"] = codex_state
    else:
        values["ORCHESTRATOR_DEVELOPER_CONFIG"] = dev_config
        values["ORCHESTRATOR_REVIEWER_CONFIG"] = rev_config
    if requirements:
        values["ORCHESTRATOR_PYTHON_REQUIREMENTS"] = requirements
    return "".join(f"{key}={shell_value(value)}\n" for key, value in values.items())


def write_config(path: Path, content: str, force: bool) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"Configuration path is not a file: {path}")
        if path.read_text(encoding="utf-8") == content:
            path.chmod(0o600)
            return
        if not force:
            raise RuntimeError(f"Generated configuration differs from existing file: {path}; use --force-config to replace it.")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".orchestrator.env.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def external_path(value: str, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    return path.resolve()


def validate_codex_state(state: Path, layout: Layout) -> None:
    if not state.is_dir():
        raise ValueError(f"Codex state must be an existing directory: {state}")
    for protected in (layout.orchestrator, layout.target_repo):
        try:
            state.resolve().relative_to(protected.resolve())
        except ValueError:
            continue
        raise ValueError(f"Codex state must be outside orchestrator and target repositories: {state}")


def ensure_private_directory(path: Path) -> None:
    if path.is_symlink():
        raise RuntimeError(f"Refusing to tighten permissions through a symbolic link: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def safe_preflight(orchestrator: Path, config: str) -> None:
    with tempfile.TemporaryDirectory(prefix="orchestrator-bootstrap-preflight-") as temp:
        root = Path(temp)
        runtime = root / "runtime"
        (runtime / "input").mkdir(parents=True)
        (runtime / "review").mkdir()
        (runtime / "output").mkdir()
        (runtime / "logs").mkdir()
        task = runtime / "input" / "task.md"
        task.write_text("Bootstrap-only validation task. Never execute.\n", encoding="utf-8")
        temporary_env = root / "orchestrator.env"
        lines = [line for line in config.splitlines() if not line.startswith("ORCHESTRATOR_RUNTIME_DIR=")]
        lines.append(f"ORCHESTRATOR_RUNTIME_DIR={shell_value(runtime)}")
        temporary_env.write_text("\n".join(lines) + "\n", encoding="utf-8")
        temporary_env.chmod(0o600)
        env = os.environ.copy()
        env["ORCHESTRATOR_ENV_FILE"] = str(temporary_env)
        run([str(orchestrator / "run.sh"), "--check"], cwd=orchestrator, env=env, capture=False)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="Absolute destination workspace path")
    parser.add_argument("--target-repo", required=True, help="Git URL or local Git repository path")
    parser.add_argument("--backend", required=True, choices=("codex", "opencode"))
    parser.add_argument("--target-name")
    parser.add_argument("--target-ref")
    parser.add_argument("--orchestrator-source")
    parser.add_argument("--orchestrator-ref")
    parser.add_argument("--image")
    parser.add_argument("--python-requirements", help="Absolute path or path relative to cloned target repository")
    parser.add_argument("--codex-state", help="Existing external Codex state directory")
    parser.add_argument("--developer-config", help="Existing OpenCode developer config file")
    parser.add_argument("--reviewer-config", help="Existing OpenCode reviewer config file")
    parser.add_argument("--force-config", action="store_true", help="Replace a different generated environment file")
    parser.add_argument("--skip-image-build", action="store_true", help="Skip build; still check the selected image")
    return parser


def phase(label: str, action):
    print(label, flush=True)
    try:
        return action()
    except (OSError, RuntimeError, ValueError) as exc:
        raise RuntimeError(f"{label} failed: {exc}") from exc


def bootstrap(args) -> None:
    name = args.target_name or derive_target_name(args.target_repo)
    layout = plan(Path(args.workspace), name)
    seed = Path(__file__).resolve().parents[1]
    source = args.orchestrator_source or str(seed)
    seed_origin = None
    ref = args.orchestrator_ref
    backend = args.backend
    selected_image = args.image or image.default_image_name(backend)
    state = external_path(args.codex_state, "--codex-state") if args.codex_state else None
    dev = rev = None
    if backend == "codex" and state is not None and not state.is_dir():
        raise ValueError(f"Codex state must be an existing directory: {state}")
    if backend == "opencode":
        if not args.developer_config or not args.reviewer_config:
            raise ValueError("--backend opencode requires --developer-config and --reviewer-config")
        dev = external_path(args.developer_config, "--developer-config")
        rev = external_path(args.reviewer_config, "--reviewer-config")
        if not dev.is_file() or not rev.is_file():
            raise ValueError(f"OpenCode config paths must be existing files: {dev}, {rev}")
    if state is not None:
        validate_codex_state(state, layout)

    expected_orchestrator = None
    expected_target = None
    requirements = None
    if args.python_requirements:
        requirements = Path(args.python_requirements).expanduser()
        if not requirements.is_absolute():
            requirements = layout.target_repo / requirements
        requirements = requirements.resolve() if layout.target_repo.exists() else requirements.absolute()
        if layout.target_repo.exists() and not requirements.is_file():
            raise ValueError(f"Python requirements file does not exist: {requirements}")

    def check_host():
        nonlocal seed_origin, ref
        check_prerequisites()
        if ref is None and not args.orchestrator_source:
            ref = run(["git", "-C", str(seed), "rev-parse", "HEAD"])
        origin_result = subprocess.run(
            ["git", "-C", str(seed), "remote", "get-url", "origin"],
            text=True, capture_output=True,
        )
        seed_origin = origin_result.stdout.strip() if origin_result.returncode == 0 else None
    phase("[1/7] Checking prerequisites", check_host)

    def prepare_orchestrator():
        nonlocal expected_orchestrator
        expected_orchestrator = expected_source_commit(source, ref, "orchestrator")
        if layout.orchestrator.exists():
            if layout.orchestrator.is_symlink():
                raise RuntimeError(f"Destination must not be a symbolic link: {layout.orchestrator}")
            if not is_git_repo(layout.orchestrator):
                raise RuntimeError(f"Destination is not a Git repository: {layout.orchestrator}")
            origins = {source}
            if not args.orchestrator_source and seed_origin:
                origins.add(seed_origin)
            verify_repository_identity(layout.orchestrator, origins, "Orchestrator")
        else:
            clone_repo(source, layout.orchestrator, ref)
            if not args.orchestrator_source and seed_origin:
                run(["git", "-C", str(layout.orchestrator), "remote", "set-url", "origin", seed_origin])
        verify_revision(layout.orchestrator, expected_orchestrator, "Orchestrator")
    phase("[2/7] Preparing orchestrator checkout", prepare_orchestrator)

    def prepare_target():
        nonlocal expected_target
        if args.target_ref:
            expected_target = expected_source_commit(args.target_repo, args.target_ref, "target repository")
        if layout.target_repo.exists():
            if layout.target_repo.is_symlink():
                raise RuntimeError(f"Destination must not be a symbolic link: {layout.target_repo}")
            if not is_git_repo(layout.target_repo):
                raise RuntimeError(f"Destination is not a Git repository: {layout.target_repo}")
            verify_repository_identity(layout.target_repo, {args.target_repo}, "Target")
        else:
            clone_repo(args.target_repo, layout.target_repo, args.target_ref)
        if expected_target:
            verify_revision(layout.target_repo, expected_target, "Target repository")
    phase("[3/7] Preparing target repository", prepare_target)

    if requirements and not requirements.is_file():
        raise ValueError(f"Python requirements file does not exist: {requirements}")
    if backend == "codex" and state is None:
        state = layout.state / "codex"
    if backend == "codex" and args.codex_state is not None:
        validate_codex_state(state, layout)

    content = config_text(layout, backend, selected_image, state, dev, rev, requirements)

    def write_workspace_config():
        if layout.config_dir.is_symlink() or layout.runtime.is_symlink():
            raise RuntimeError("Config and runtime directories must not be symbolic links")
        layout.config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        layout.config_dir.chmod(0o700)
        write_config(layout.env_file, content, args.force_config)
        layout.runtime.mkdir(parents=True, exist_ok=True)
        for directory in ("input", "review", "output", "logs"):
            (layout.runtime / directory).mkdir(parents=True, exist_ok=True)
    phase("[4/7] Writing configuration", write_workspace_config)
    if backend == "codex" and args.codex_state is None:
        ensure_private_directory(layout.state)
        ensure_private_directory(state)
        validate_codex_state(state, layout)

    env = os.environ.copy()
    env["ORCHESTRATOR_ENV_FILE"] = str(layout.env_file)
    if args.skip_image_build:
        print("[5/7] Image build skipped by request", flush=True)
    else:
        phase("[5/7] Building agent image", lambda: run(
            [str(layout.orchestrator / "image.sh"), "build"], cwd=layout.orchestrator,
            env=env, capture=False,
        ))

    def run_image_checks():
        run([str(layout.orchestrator / "image.sh"), "check"], cwd=layout.orchestrator,
            env=env, capture=False)
        run([str(layout.orchestrator / "image.sh"), "mount-check"], cwd=layout.orchestrator,
            env=env, capture=False)
    phase("[6/7] Running image and mount checks", run_image_checks)
    phase("[7/7] Running safe preflight", lambda: safe_preflight(layout.orchestrator, content))

    print("WORKSPACE READY")
    print(f"Workspace: {layout.workspace}")
    print(f"Orchestrator: {layout.orchestrator}")
    print(f"Orchestrator revision: {expected_orchestrator}")
    print(f"Target repo: {layout.target_repo}")
    print(f"Target revision: {repository_head(layout.target_repo)}")
    print(f"Backend: {backend}\nImage: {selected_image}")
    print(f"Config: {layout.env_file}\nRuntime: {layout.runtime}")
    print("Image check: PASS\nMount check: PASS\nSafe preflight: PASS")
    print("No agent workflow was run.")
    print(f"\nNext: edit {layout.runtime / 'input' / 'task.md'} before any real workflow.")
    print(f"  cd {shell_value(layout.orchestrator)}")
    print(f"  ORCHESTRATOR_ENV_FILE={shell_value(layout.env_file)} ./run.sh --check")
    print("  # Real workflow, after reviewing the task and configuration: ./run.sh")
    if backend == "codex":
        print("Codex authentication: UNKNOWN (not verified).")


def main(argv=None) -> int:
    args = make_parser().parse_args(argv)
    try:
        bootstrap(args)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Bootstrap failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
