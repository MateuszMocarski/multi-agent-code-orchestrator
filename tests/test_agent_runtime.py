import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import quality_gates


class AgentRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.runtime = self.root / "runtime"
        self.task_dir = self.runtime / "input"
        self.task_dir.mkdir(parents=True)
        self.repo.mkdir()
        self.task = self.task_dir / "task.md"
        self.task.write_text("A customized task.\n", encoding="utf-8")
        self.dev_config = self.root / "developer.json"
        self.rev_config = self.root / "reviewer.json"
        self.dev_config.write_text("{}\n", encoding="utf-8")
        self.rev_config.write_text("{}\n", encoding="utf-8")
        self.state = self.root / "codex-state"
        self.state.mkdir()
        self.base_env = {
            "ORCHESTRATOR_TARGET_REPO": str(self.repo),
            "ORCHESTRATOR_RUNTIME_DIR": str(self.runtime),
            "ORCHESTRATOR_DOCKER_IMAGE": "test-image:latest",
        }
        self.original_env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.original_env)
        importlib.reload(config)
        if "agent_runtime" in sys.modules:
            importlib.reload(sys.modules["agent_runtime"])
        if "container_command" in sys.modules:
            importlib.reload(sys.modules["container_command"])
        self.temp.cleanup()

    def load_config(self, **extra):
        os.environ.clear()
        os.environ.update({**self.base_env, **extra})
        reloaded = importlib.reload(config)
        runtime = importlib.import_module("agent_runtime")
        importlib.reload(runtime)
        builder = importlib.import_module("container_command")
        importlib.reload(builder)
        return reloaded, runtime, builder

    def test_backend_default_and_explicit_selection(self):
        cfg, _, _ = self.load_config()
        self.assertEqual(cfg.BACKEND, "opencode")
        cfg, _, _ = self.load_config(ORCHESTRATOR_AGENT_BACKEND="opencode")
        self.assertEqual(cfg.BACKEND, "opencode")
        cfg, _, _ = self.load_config(ORCHESTRATOR_AGENT_BACKEND="codex")
        self.assertEqual(cfg.BACKEND, "codex")

    def test_invalid_backend_rejected(self):
        for backend in ("other", "Codex"):
            cfg, _, _ = self.load_config(ORCHESTRATOR_AGENT_BACKEND=backend)
            with self.subTest(backend=backend):
                with self.assertRaisesRegex(RuntimeError, "one of: opencode, codex"):
                    cfg.validate_runtime_configuration()

    def test_opencode_requires_its_config_and_codex_does_not(self):
        cfg, _, _ = self.load_config(ORCHESTRATOR_AGENT_BACKEND="opencode")
        with self.assertRaisesRegex(RuntimeError, "ORCHESTRATOR_DEVELOPER_CONFIG"):
            cfg.validate_runtime_configuration()
        cfg, _, _ = self.load_config(
            ORCHESTRATOR_AGENT_BACKEND="codex",
            ORCHESTRATOR_CODEX_STATE_DIR=str(self.state),
        )
        cfg.validate_runtime_configuration()

    def test_codex_requires_external_state_directory(self):
        cfg, _, _ = self.load_config(ORCHESTRATOR_AGENT_BACKEND="codex")
        with self.assertRaisesRegex(RuntimeError, "ORCHESTRATOR_CODEX_STATE_DIR"):
            cfg.validate_runtime_configuration()
        cfg, _, _ = self.load_config(
            ORCHESTRATOR_AGENT_BACKEND="codex",
            ORCHESTRATOR_CODEX_STATE_DIR=str(self.state / "missing"),
        )
        with self.assertRaisesRegex(RuntimeError, "does not exist"):
            cfg.validate_runtime_configuration()

    def test_codex_developer_and_reviewer_mount_permissions_and_network(self):
        _, _, builder = self.load_config(
            ORCHESTRATOR_AGENT_BACKEND="codex",
            ORCHESTRATOR_CODEX_STATE_DIR=str(self.state),
            ORCHESTRATOR_DEVELOPER_MODEL="developer-choice",
            ORCHESTRATOR_REVIEWER_MODEL="reviewer-choice",
        )
        developer = builder.build_agent_command("developer", "implement")
        correction = builder.build_agent_command(
            "developer", "correct", review_read_only=True
        )
        reviewer = builder.build_agent_command("reviewer", "review")
        dev_text = " ".join(developer)
        correction_text = " ".join(correction)
        review_text = " ".join(reviewer)
        self.assertIn(f"src={self.repo},dst=/workspace", dev_text)
        self.assertNotIn(f"src={self.repo},dst=/workspace,readonly", dev_text)
        self.assertIn(f"src={self.task_dir},dst=/input,readonly", dev_text)
        self.assertIn(f"src={self.runtime / 'review'},dst=/review,readonly", correction_text)
        self.assertIn(f"src={self.repo},dst=/workspace,readonly", review_text)
        self.assertIn(f"src={self.runtime / 'review'},dst=/review", review_text)
        self.assertIn(f"src={self.state},dst=/codex-state", review_text)
        self.assertIn("CODEX_HOME=/codex-state", reviewer)
        self.assertIn("--add-dir", reviewer)
        self.assertIn("developer-choice", developer)
        self.assertNotIn("reviewer-choice", developer)
        self.assertIn("reviewer-choice", reviewer)
        dev_codex = developer[developer.index("codex"):developer.index("implement")]
        reviewer_codex = reviewer[reviewer.index("codex"):reviewer.index("review")]
        self.assertEqual(
            dev_codex,
            [
                "codex", "--ask-for-approval", "never", "exec",
                "--sandbox", "workspace-write", "--cd", "/workspace",
                "--model", "developer-choice",
            ],
        )
        self.assertEqual(
            reviewer_codex,
            [
                "codex", "--ask-for-approval", "never", "exec",
                "--sandbox", "workspace-write", "--cd", "/workspace",
                "--add-dir", "/review", "--model", "reviewer-choice",
            ],
        )
        self.assertNotIn("--network", developer)
        self.assertNotIn("--network", reviewer)
        self.assertNotIn("--name", developer)
        self.assertNotIn("--name", reviewer)

    def test_opencode_network_and_runtime_config_are_backend_specific(self):
        _, _, builder = self.load_config(
            ORCHESTRATOR_AGENT_BACKEND="opencode",
            ORCHESTRATOR_DEVELOPER_CONFIG=str(self.dev_config),
            ORCHESTRATOR_REVIEWER_CONFIG=str(self.rev_config),
        )
        command = builder.build_agent_command("developer", "implement")
        self.assertIn("--network", command)
        self.assertIn("host", command)
        self.assertTrue(
            any(
                f"src={self.dev_config},dst=/config/opencode.json,readonly" in item
                for item in command
            )
        )
        self.assertIn("OPENCODE_CONFIG=/config/opencode.json", command)
        self.assertEqual(
            command[command.index("opencode"):command.index("implement")],
            ["opencode", "run", "--standalone", "--auto"],
        )
        self.assertNotIn("--name", command)

    def test_quality_gate_has_no_agent_auth_or_host_network(self):
        self.load_config(
            ORCHESTRATOR_AGENT_BACKEND="codex",
            ORCHESTRATOR_CODEX_STATE_DIR=str(self.state),
        )
        captured = []
        with patch.object(quality_gates, "run_container", side_effect=lambda _, cmd: captured.append(cmd) or 0), patch.object(
            quality_gates, "REPO", self.repo
        ), patch.object(quality_gates, "IMAGE", "test-image:latest"):
            quality_gates.run_pytest()
        command = captured[0]
        text = " ".join(command)
        self.assertNotIn("CODEX_HOME", text)
        self.assertNotIn(str(self.state), text)
        self.assertNotIn("OPENCODE_CONFIG", text)
        self.assertNotIn("opencode.json", text)
        self.assertNotIn("--network", command)


if __name__ == "__main__":
    unittest.main()
