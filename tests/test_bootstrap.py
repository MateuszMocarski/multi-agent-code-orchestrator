import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orchestrator import bootstrap


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.layout = bootstrap.plan(self.root / "space with blanks", "sample")
        self.state = self.root / "external state"
        self.state.mkdir()

    def make_git_repo(self, name):
        repository = self.root / name
        repository.mkdir()
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        (repository / "file").write_text("one\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", "file"], check=True)
        subprocess.run(
            ["git", "-C", str(repository), "-c", "user.name=Bootstrap",
             "-c", "user.email=bootstrap@example.invalid", "commit", "-qm", "one"],
            check=True,
        )
        first = bootstrap.repository_head(repository)
        (repository / "file").write_text("two\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "commit", "-qam", "two"],
                       env={**os.environ, "GIT_AUTHOR_NAME": "Bootstrap",
                            "GIT_AUTHOR_EMAIL": "bootstrap@example.invalid",
                            "GIT_COMMITTER_NAME": "Bootstrap",
                            "GIT_COMMITTER_EMAIL": "bootstrap@example.invalid"}, check=True)
        return repository, first, bootstrap.repository_head(repository)

    def make_target_clone_pair(self, name):
        source, _, _ = self.make_git_repo(f"{name}-source")
        commit_a = bootstrap.repository_head(source)
        subprocess.run(["git", "-C", str(source), "branch", "-M", "revision"], check=True)
        destination = self.root / f"{name}-destination"
        subprocess.run(["git", "clone", "-q", str(source), str(destination)], check=True)
        (source / "file").write_text("source commit B\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(source), "commit", "-qam", "source B"],
            env={**os.environ, "GIT_AUTHOR_NAME": "Bootstrap",
                 "GIT_AUTHOR_EMAIL": "bootstrap@example.invalid",
                 "GIT_COMMITTER_NAME": "Bootstrap",
                 "GIT_COMMITTER_EMAIL": "bootstrap@example.invalid"},
            check=True,
        )
        commit_b = bootstrap.repository_head(source)
        return source, destination, commit_a, commit_b

    def tearDown(self):
        self.temp.cleanup()

    def test_workspace_planning_requires_absolute_and_maps_layout(self):
        self.assertEqual(self.layout.target_repo, self.layout.workspace / "projects/sample/repo")
        self.assertEqual(self.layout.runtime, self.layout.workspace / "projects/sample/runtime")
        self.assertEqual(self.layout.env_file, self.layout.workspace / "config/orchestrator.env")
        with self.assertRaisesRegex(ValueError, "absolute"):
            bootstrap.plan(Path("relative"), "sample")

    def test_target_name_derivation(self):
        self.assertEqual(bootstrap.derive_target_name("https://host/group/project.git"), "project")
        self.assertEqual(bootstrap.derive_target_name("/tmp/code"), "code")

    def test_generated_env_quotes_paths_and_selects_backend_contract(self):
        codex = bootstrap.config_text(self.layout, "codex", "tag", self.state, None, None, None)
        self.assertIn("ORCHESTRATOR_RUNTIME_DIR='", codex)
        self.assertIn("ORCHESTRATOR_CODEX_STATE_DIR=", codex)
        self.assertNotIn("DEVELOPER_CONFIG", codex)
        opencode = bootstrap.config_text(self.layout, "opencode", "tag", None,
                                         self.root / "dev config.json", self.root / "review.json", None)
        self.assertIn("ORCHESTRATOR_DEVELOPER_CONFIG=", opencode)
        self.assertIn("ORCHESTRATOR_REVIEWER_CONFIG=", opencode)
        self.assertNotIn("CODEX_STATE", opencode)
        self.assertEqual(bootstrap.shell_value("/a path/it's"), "'/a path/it'\"'\"'s'")

    def test_codex_model_options_are_optional_exact_and_shell_quoted(self):
        default = bootstrap.config_text(self.layout, "codex", "tag", self.state, None, None, None)
        self.assertNotIn("ORCHESTRATOR_DEVELOPER_MODEL", default)
        self.assertNotIn("ORCHESTRATOR_REVIEWER_MODEL", default)
        configured = bootstrap.config_text(
            self.layout, "codex", "tag", self.state, None, None, None,
            "model with spaces", "opaque's-id",
        )
        self.assertIn("ORCHESTRATOR_DEVELOPER_MODEL='model with spaces'", configured)
        self.assertIn("ORCHESTRATOR_REVIEWER_MODEL='opaque'\"'\"'s-id'", configured)

    def test_default_image_comes_from_existing_image_module(self):
        self.assertEqual(bootstrap.image.default_image_name("codex"), "orchestrator-agent:codex-py314-v1")

    def test_existing_directories_are_preserved(self):
        runtime = self.layout.runtime / "input"
        runtime.mkdir(parents=True)
        marker = runtime / "keep"
        marker.write_text("data")
        runtime.mkdir(parents=True, exist_ok=True)
        self.assertEqual(marker.read_text(), "data")

    def test_config_refuses_replacement_and_force_is_atomic_private(self):
        path = self.root / "config.env"
        bootstrap.write_config(path, "A=1\n", False)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        path.chmod(0o644)
        bootstrap.write_config(path, "A=1\n", False)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(RuntimeError, "differs"):
            bootstrap.write_config(path, "A=2\n", False)
        bootstrap.write_config(path, "A=2\n", True)
        self.assertEqual(path.read_text(), "A=2\n")

    def test_incompatible_repo_destination_is_rejected(self):
        path = self.root / "repo"
        path.mkdir()
        with self.assertRaisesRegex(RuntimeError, "not a Git repository"):
            bootstrap.clone_repo("unused", path)

    def test_external_codex_state_must_exist_outside_repositories(self):
        bootstrap.validate_codex_state(self.state, self.layout)
        with self.assertRaisesRegex(ValueError, "existing directory"):
            bootstrap.validate_codex_state(self.root / "absent", self.layout)
        inside = self.layout.orchestrator / "nested"
        inside.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "outside"):
            bootstrap.validate_codex_state(inside, self.layout)

    def test_default_codex_state_directory_is_private(self):
        state = self.root / "state" / "codex"
        bootstrap.ensure_private_directory(state.parent)
        bootstrap.ensure_private_directory(state)
        self.assertEqual(state.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(state.stat().st_mode & 0o777, 0o700)

    def test_external_codex_state_permissions_are_preserved(self):
        source, _, _ = self.make_git_repo("external-state-target")
        external = self.root / "external-codex-state"
        external.mkdir(mode=0o750)
        external.chmod(0o750)
        workspace = self.root / "external-state-workspace"
        args = bootstrap.make_parser().parse_args([
            "--workspace", str(workspace), "--target-repo", str(source),
            "--backend", "codex", "--codex-state", str(external), "--skip-image-build",
        ])
        original_run = bootstrap.run

        def run_with_mocked_images(command, **kwargs):
            if len(command) >= 2 and Path(command[0]).name == "image.sh":
                return ""
            return original_run(command, **kwargs)

        with mock.patch.object(bootstrap, "check_prerequisites"), mock.patch.object(
            bootstrap, "run", side_effect=run_with_mocked_images
        ):
            bootstrap.bootstrap(args)
        self.assertEqual(external.stat().st_mode & 0o777, 0o750)

    def test_existing_repository_identity_accepts_normalized_local_source(self):
        source, _, _ = self.make_git_repo("identity-source")
        destination = self.root / "identity-destination"
        subprocess.run(["git", "clone", "-q", str(source), str(destination)], check=True)
        bootstrap.verify_repository_identity(destination, {str(source) + "/"}, "Target")

    def test_unrelated_existing_repository_is_rejected_without_mutation(self):
        source, _, _ = self.make_git_repo("expected-source")
        destination_source, _, _ = self.make_git_repo("unrelated-source")
        destination = self.root / "unrelated-destination"
        subprocess.run(["git", "clone", "-q", str(destination_source), str(destination)], check=True)
        marker = destination / "user-data"
        marker.write_text("keep\n", encoding="utf-8")
        head = bootstrap.repository_head(destination)
        with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
            bootstrap.verify_repository_identity(destination, {str(source)}, "Target")
        self.assertEqual(bootstrap.repository_head(destination), head)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep\n")

    def test_remote_style_local_source_ref_resolution_and_ambiguity(self):
        source, _, commit = self.make_git_repo("ref-source")
        subprocess.run(["git", "-C", str(source), "branch", "release"], check=True)
        bare = self.root / "remote.git"
        subprocess.run(["git", "clone", "-q", "--bare", str(source), str(bare)], check=True)
        remote_url = bare.as_uri()
        self.assertEqual(
            bootstrap.expected_source_commit(remote_url, "release"),
            commit,
        )
        subprocess.run(["git", "-C", str(bare), "tag", "release", commit], check=True)
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            bootstrap.expected_source_commit(remote_url, "release")
        self.assertEqual(
            bootstrap.expected_source_commit(remote_url, "refs/heads/release"),
            commit,
        )

    def test_run_stream_mode_preserves_failure_diagnostic(self):
        error = subprocess.CalledProcessError(7, ["slow-command"], stderr=None, output=None)
        with mock.patch.object(bootstrap.subprocess, "run", side_effect=error) as mocked:
            with self.assertRaisesRegex(RuntimeError, "exit code 7"):
                bootstrap.run(["slow-command"], capture=False)
        self.assertIsNone(mocked.call_args.kwargs["stdout"])
        self.assertIsNone(mocked.call_args.kwargs["stderr"])

    def test_repeated_bootstrap_preserves_runtime_state_and_private_config(self):
        source, _, _ = self.make_git_repo("rerun-target")
        workspace = self.root / "workspace with spaces"
        args = bootstrap.make_parser().parse_args([
            "--workspace", str(workspace), "--target-repo", str(source),
            "--backend", "codex", "--skip-image-build",
        ])
        original_run = bootstrap.run
        seen_image_actions = []

        def run_with_mocked_images(command, **kwargs):
            if len(command) >= 2 and Path(command[0]).name == "image.sh":
                seen_image_actions.append(command[1])
                return ""
            return original_run(command, **kwargs)

        with mock.patch.object(bootstrap, "check_prerequisites"), mock.patch.object(
            bootstrap, "run", side_effect=run_with_mocked_images
        ):
            bootstrap.bootstrap(args)
            config = workspace / "config" / "orchestrator.env"
            runtime_marker = workspace / "projects" / source.name / "runtime" / "output" / "keep"
            state_marker = workspace / "state" / "codex" / "keep"
            runtime_marker.write_text("runtime\n", encoding="utf-8")
            state_marker.write_text("state\n", encoding="utf-8")
            config_content = config.read_text(encoding="utf-8")
            config.chmod(0o644)
            bootstrap.bootstrap(args)

        self.assertEqual(config.read_text(encoding="utf-8"), config_content)
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(config.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(runtime_marker.read_text(encoding="utf-8"), "runtime\n")
        self.assertEqual(state_marker.read_text(encoding="utf-8"), "state\n")
        self.assertEqual(seen_image_actions, ["check", "mount-check"] * 2)

    def test_missing_prerequisites_are_actionable(self):
        with mock.patch.object(bootstrap.platform, "system", return_value="Windows"):
            with self.assertRaisesRegex(RuntimeError, "Linux or WSL"):
                bootstrap.check_prerequisites()

    def test_existing_orchestrator_matching_revision_is_accepted(self):
        repository, _, current = self.make_git_repo("existing-orchestrator")
        bootstrap.verify_revision(repository, current, "Orchestrator")

    def test_existing_orchestrator_mismatching_revision_is_rejected(self):
        repository, old, current = self.make_git_repo("stale-orchestrator")
        with self.assertRaisesRegex(RuntimeError, f"expected {old}, current HEAD is {current}"):
            bootstrap.verify_revision(repository, old, "Orchestrator")
        self.assertEqual(bootstrap.repository_head(repository), current)

    def test_existing_target_requested_ref_matching_head_is_accepted(self):
        source, _, _, current = self.make_target_clone_pair("matching-target")
        destination = self.root / "matching-target-current"
        subprocess.run(["git", "clone", "-q", str(source), str(destination)], check=True)
        requested = bootstrap.expected_source_commit(str(source), "revision", "target repository")
        self.assertEqual(requested, current)
        self.assertEqual(bootstrap.repository_head(destination), current)
        bootstrap.verify_revision(destination, requested, "Target repository")

    def test_existing_target_requested_ref_mismatch_is_rejected(self):
        source, destination, requested, current = self.make_target_clone_pair("stale-target")
        expected = bootstrap.expected_source_commit(str(source), "revision", "target repository")
        self.assertEqual(expected, current)
        with self.assertRaisesRegex(RuntimeError, f"expected {expected}, current HEAD is {requested}"):
            bootstrap.verify_revision(destination, expected, "Target repository")
        self.assertEqual(bootstrap.repository_head(destination), requested)


if __name__ == "__main__":
    unittest.main()
