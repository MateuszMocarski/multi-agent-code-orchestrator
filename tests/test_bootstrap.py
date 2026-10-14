import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import bootstrap


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
