import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from orchestrator import cli, toolchain


class ToolchainTests(unittest.TestCase):
    def test_toolchain_lock_is_valid_and_versions_are_reported(self):
        data = toolchain.load()
        self.assertEqual(data["tools"]["opencode"]["package"], "opencode-ai")
        self.assertIn("pytest", toolchain.versions_report(data))

    def test_stable_semver_excludes_prereleases(self):
        for value in ("1.2.3-alpha.1", "1.2.3-beta", "1.2.3-rc.1", "latest"):
            self.assertFalse(toolchain.stable_version(value))
        self.assertTrue(toolchain.stable_version("1.2.3"))

    def test_stable_semver_order_and_toolchain_states(self):
        self.assertEqual(toolchain.compare_stable_versions("1.19.0", "2.0.22"), -1)
        self.assertEqual(toolchain.update_state("2.0.22", "1.19.0"), "PIN AHEAD")
        self.assertEqual(toolchain.update_state("2.0.22", "2.0.22"), "CURRENT")
        self.assertEqual(toolchain.update_state("1.19.0", "2.0.22"), "UPDATE AVAILABLE")

    def test_toolchain_check_reports_pin_ahead_instead_of_update(self):
        output = []
        with patch.object(toolchain, "check_updates", return_value=[("opencode", "2.0.22", "1.19.0")]), \
             patch("builtins.print", side_effect=lambda *parts, **kwargs: output.append(" ".join(parts))):
            self.assertEqual(cli._toolchain_check(), 0)
        self.assertIn("opencode: PIN AHEAD (pinned 2.0.22; latest stable 1.19.0)", output[0])

    def test_toolchain_update_skips_downgrade_and_applies_higher_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "toolchain.json"
            data = toolchain.load()
            data["tools"]["opencode"]["version"] = "2.0.22"
            data["tools"]["codex"]["version"] = "1.0.0"
            rendered = __import__("json").dumps(data, indent=2) + "\n"
            path.write_text(rendered, encoding="utf-8")
            original = path.read_bytes()
            candidates = {"opencode-ai": "1.19.0", "@openai/codex": "1.1.0"}
            def query(package, registry): return candidates[package]
            outcomes = toolchain.update(path, ("opencode", "codex"), query)
            updated = toolchain.load(path)
            self.assertEqual(updated["tools"]["opencode"]["version"], "2.0.22")
            self.assertEqual(updated["tools"]["codex"]["version"], "1.1.0")
            self.assertEqual([entry[3] for entry in outcomes], ["PIN AHEAD", "UPDATE AVAILABLE"])
            self.assertEqual(__import__("json").loads(original)["tools"]["opencode"]["version"], "2.0.22")

    def test_all_lower_or_equal_candidates_do_not_rewrite_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "toolchain.json"
            path.write_bytes(toolchain.LOCK_FILE.read_bytes())
            original = path.read_bytes()
            candidates = {"@openai/codex": "0.1.0"}
            outcomes = toolchain.update(path, ("codex",), lambda package, registry: candidates[package])
            self.assertEqual(outcomes[0][3], "PIN AHEAD")
            self.assertEqual(path.read_bytes(), original)

    def test_registry_latest_tag_must_be_stable_and_present(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"dist-tags":{"latest":"2.0.0"},"versions":{"2.0.0":{}}}'

        with patch.object(toolchain.urllib.request, "urlopen", return_value=Response()):
            self.assertEqual(toolchain.latest_stable("pkg"), "2.0.0")
        class Prerelease(Response):
            def read(self): return b'{"dist-tags":{"latest":"3.0.0-rc.1"},"versions":{"3.0.0-rc.1":{}}}'
        with patch.object(toolchain.urllib.request, "urlopen", return_value=Prerelease()):
            with self.assertRaisesRegex(RuntimeError, "stable"):
                toolchain.latest_stable("pkg")

    def test_registry_offline_error_is_actionable(self):
        from urllib.error import URLError
        with patch.object(toolchain.urllib.request, "urlopen", side_effect=URLError("offline")):
            with self.assertRaisesRegex(RuntimeError, "network access"):
                toolchain.latest_stable("pkg")

    def test_registry_rejects_malformed_metadata(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'[]'
        with patch.object(toolchain.urllib.request, "urlopen", return_value=Response()):
            with self.assertRaisesRegex(RuntimeError, "invalid response"):
                toolchain.latest_stable("pkg")

    def test_update_is_atomic_when_candidate_is_invalid(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "toolchain.json"
            original = toolchain.LOCK_FILE.read_text(encoding="utf-8")
            path.write_text(original, encoding="utf-8")
            def query(package, registry): return "1.0.0-rc.1"
            with self.assertRaisesRegex(RuntimeError, "stable exact"):
                toolchain.update(path, ("codex",), query)
            self.assertEqual(path.read_text(encoding="utf-8"), original)


class AgentCliTests(unittest.TestCase):
    def test_explicit_config_precedes_workspace_discovery(self):
        with tempfile.TemporaryDirectory() as temp:
            explicit = Path(temp) / "explicit.env"
            explicit.write_text("A=1\n")
            with patch.dict(os.environ, {"ORCHESTRATOR_ENV_FILE": str(explicit)}):
                self.assertEqual(cli.discover_config(), explicit.resolve())

    def test_workspace_config_discovery_requires_unambiguous_layout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace" / "orchestrator"
            (root / "orchestrator").mkdir(parents=True)
            (root.parent / "projects").mkdir()
            config = root.parent / "config" / "orchestrator.env"
            config.parent.mkdir()
            config.write_text("A=1\n")
            with patch.object(cli, "ROOT", root), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(cli.discover_config(), config.resolve())

    def test_check_command_is_only_the_safe_preflight_entrypoint(self):
        captured = []
        with patch.object(cli, "discover_config", return_value=Path("/tmp/config")), patch.object(
            cli, "_run", side_effect=lambda command, **kwargs: captured.append(command) or type("R", (), {"returncode": 0})()
        ):
            self.assertEqual(cli.main(["check"]), 0)
        self.assertEqual(captured[0][-1], "--check")
        self.assertNotIn("run", captured[0])

    def test_auth_mounts_only_configured_state_and_network(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "external state"
            state.mkdir()
            state.chmod(0o750)
            command = cli.auth_docker_command("agent:codex", state, ["login", "--device-auth"], interactive=True)
            self.assertEqual(state.stat().st_mode & 0o777, 0o750)
        joined = " ".join(command)
        self.assertIn("--network bridge", joined)
        self.assertIn("CODEX_HOME=/codex-state", joined)
        self.assertIn(f"src={state.resolve()},dst=/codex-state", joined)
        for forbidden in ("/workspace", "/input", "/review", "docker.sock"):
            self.assertNotIn(forbidden, joined)

    def test_opencode_auth_is_not_applicable(self):
        from orchestrator import config
        output = []
        with patch.object(config, "BACKEND", "opencode"), patch("builtins.print", side_effect=lambda *p, **k: output.append(" ".join(p))):
            self.assertEqual(cli._auth_state_action("status"), 0)
        self.assertIn("not applicable", output[0])

    def test_public_auth_status_formats_sanitized_probe_state(self):
        output = []
        with patch.object(cli, "_probe_auth_status", return_value=("authenticated", 0)), \
             patch("builtins.print", side_effect=lambda *parts, **kwargs: output.append(" ".join(parts))):
            self.assertEqual(cli._auth_state_action("status"), 0)
        self.assertEqual(output, ["Codex authentication: authenticated (account details hidden)."])

    def test_logout_success_ignores_negative_status_language(self):
        from orchestrator import config
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            output = []
            result = SimpleNamespace(returncode=0, stdout="Successfully logged out", stderr="")
            with patch.object(config, "BACKEND", "codex"), patch.object(config, "IMAGE", "agent:codex"), \
                 patch.dict(os.environ, {"ORCHESTRATOR_CODEX_STATE_DIR": str(state)}), \
                 patch.object(cli, "_codex_auth_help", return_value=[["login"], ["login", "status"], ["logout"]]), \
                 patch.object(cli, "_run", return_value=result), \
                 patch("builtins.print", side_effect=lambda *parts, **kwargs: output.append(" ".join(parts))):
                self.assertEqual(cli._auth_state_action("logout"), 0)
            self.assertIn("logout succeeded", output[0])
            self.assertNotIn("Successfully", output[0])

    def test_logout_nonzero_exit_fails_without_printing_cli_output(self):
        from orchestrator import config
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            result = SimpleNamespace(returncode=7, stdout="secret diagnostic", stderr="")
            with patch.object(config, "BACKEND", "codex"), patch.object(config, "IMAGE", "agent:codex"), \
                 patch.dict(os.environ, {"ORCHESTRATOR_CODEX_STATE_DIR": str(state)}), \
                 patch.object(cli, "_codex_auth_help", return_value=[["login"], ["login", "status"], ["logout"]]), \
                 patch.object(cli, "_run", return_value=result), patch("builtins.print") as printer:
                self.assertEqual(cli._auth_state_action("logout"), 7)
            self.assertNotIn("secret diagnostic", str(printer.call_args_list))

    def test_cli_version_normalization_and_mismatch(self):
        self.assertEqual(cli._normalized_cli_version("codex", "codex-cli 0.156.1"), "0.156.1")
        self.assertEqual(cli._normalized_cli_version("opencode", "opencode 2.0.22"), "2.0.22")
        self.assertIsNone(cli._normalized_cli_version("codex", "unavailable"))

    def test_doctor_warns_when_installed_cli_differs_from_pin(self):
        from orchestrator import config
        with tempfile.TemporaryDirectory() as temp:
            output = []
            with patch.object(config, "validate_runtime_configuration"), \
                 patch.object(config, "BACKEND", "codex"), patch.object(config, "IMAGE", "agent:codex"), \
                 patch.object(config, "task_readiness", return_value=("missing", "not created yet")), \
                 patch.dict(os.environ, {"ORCHESTRATOR_CODEX_STATE_DIR": temp}), \
                 patch.object(cli, "_run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")), \
                 patch.object(cli, "_docker_image_exists", return_value=True), \
                 patch.object(cli, "_docker_version", return_value="codex-cli 0.0.1"), \
                 patch.object(cli, "_probe_auth_status", return_value=("unauthenticated", 1)), \
                 patch("builtins.print", side_effect=lambda *parts, **kwargs: output.append(" ".join(map(str, parts)))):
                self.assertEqual(cli.doctor(), 0, output)
            self.assertTrue(any("WARN Agent CLI: installed=0.0.1" in line for line in output))

    def test_doctor_before_task_creation_warns_but_preflight_stays_strict(self):
        from orchestrator import config
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            runtime = root / "runtime"
            repo.mkdir()
            (runtime / "input").mkdir(parents=True)
            for name in ("review", "output", "logs"):
                (runtime / name).mkdir()
            state = root / "state"
            state.mkdir()
            output = []
            with patch.object(config, "REPO", repo), patch.object(config, "RUNTIME_DIR", runtime), \
                 patch.object(config, "TASK_FILE", runtime / "input" / "task.md"), \
                 patch.object(config, "BACKEND", "codex"), patch.object(config, "IMAGE", "missing:image"), \
                 patch.object(config, "MAX_REVIEW_ROUNDS", 4), \
                 patch.dict(os.environ, {"ORCHESTRATOR_CODEX_STATE_DIR": str(state)}), \
                 patch.object(cli, "_run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")), \
                 patch.object(cli, "_docker_image_exists", return_value=False), \
                 patch("builtins.print", side_effect=lambda *parts, **kwargs: output.append(" ".join(map(str, parts)))):
                self.assertEqual(cli.doctor(), 0, output)
                self.assertTrue(any("WARN Task: not created yet" in row for row in output))
                with self.assertRaisesRegex(RuntimeError, "task.md"):
                    config.validate_runtime_configuration()
                config.TASK_FILE.write_text("A task authored for validation.\n", encoding="utf-8")
                output.clear()
                self.assertEqual(cli.doctor(), 0)
                self.assertTrue(any("PASS Task: valid task is present" in row for row in output))

    def test_doctor_auth_probe_is_silent_and_reports_one_sanitized_row(self):
        from orchestrator import config
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            output = []
            with patch.object(config, "validate_runtime_configuration"), \
                 patch.object(config, "BACKEND", "codex"), patch.object(config, "IMAGE", "agent:codex"), \
                 patch.object(config, "task_readiness", return_value=("missing", "not created yet")), \
                 patch.dict(os.environ, {"ORCHESTRATOR_CODEX_STATE_DIR": temp}), \
                 patch.object(cli, "_run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")), \
                 patch.object(cli, "_docker_image_exists", return_value=True), \
                 patch.object(cli, "_docker_version", return_value="codex-cli 0.156.1"), \
                 patch.object(cli, "_probe_auth_status", return_value=("unauthenticated", 1)), \
                 patch("builtins.print", side_effect=lambda *parts, **kwargs: output.append(" ".join(map(str, parts)))):
                self.assertEqual(cli.doctor(), 0)
            auth_rows = [row for row in output if "Codex auth" in row]
            self.assertEqual(auth_rows, ["WARN Codex auth: unauthenticated"])
            self.assertFalse(any("Codex authentication:" in row for row in output))

    def test_auth_cli_selection_is_based_on_help_capabilities(self):
        responses = {
            ("--help",): (0, "Commands: login logout"),
            ("login", "--help"): (0, "Options: --device-auth"),
            ("login", "status", "--help"): (0, "status"),
            ("logout", "--help"): (0, "logout"),
        }
        def fake_run(cmd, **kwargs):
            args = tuple(cmd[cmd.index("image") + 1:])
            key = args
            code, output = responses[key]
            return type("R", (), {"returncode": code, "stdout": output, "stderr": ""})()
        with patch.object(cli, "_run", side_effect=fake_run):
            commands = cli._codex_auth_help("image")
        self.assertEqual(commands, [["login", "--device-auth"], ["login", "status"], ["logout"]])

    def test_doctor_reports_missing_image_and_unverified_auth_without_failure(self):
        from orchestrator import config
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            output = []
            with patch.object(config, "validate_runtime_configuration"), \
                 patch.object(config, "BACKEND", "codex"), patch.object(config, "IMAGE", "missing:image"), \
                 patch.object(config, "task_readiness", return_value=("missing", "not created yet")), \
                 patch.dict(os.environ, {"ORCHESTRATOR_CODEX_STATE_DIR": str(state)}, clear=False), \
                 patch.object(cli, "_run", return_value=SimpleNamespace(returncode=0)), \
                 patch.object(cli, "_docker_image_exists", return_value=False), \
                 patch("builtins.print", side_effect=lambda *parts, **kwargs: output.append(" ".join(map(str, parts)))):
                self.assertEqual(cli.doctor(), 0)
            self.assertTrue(any("WARN Image: not built" in line for line in output))
            self.assertTrue(any("Codex auth" in line and "not checked" in line for line in output))

    def test_doctor_reports_invalid_configuration(self):
        from orchestrator import config
        output = []
        with patch.object(config, "validate_runtime_configuration", side_effect=RuntimeError("bad config")), \
             patch.object(config, "BACKEND", "codex"), patch.object(config, "IMAGE", None), \
             patch.object(cli, "_run", return_value=SimpleNamespace(returncode=1)), \
             patch("builtins.print", side_effect=lambda *parts, **kwargs: output.append(" ".join(map(str, parts)))):
            self.assertEqual(cli.doctor(), 1)
        self.assertTrue(any("FAIL Workspace config: bad config" in line for line in output))

    def test_doctor_without_workspace_config_still_checks_host(self):
        output = []
        with patch.object(cli, "_run", return_value=SimpleNamespace(returncode=1)), \
             patch("builtins.print", side_effect=lambda *parts, **kwargs: output.append(" ".join(map(str, parts)))):
            self.assertEqual(cli.doctor_without_config("no config"), 1)
        self.assertTrue(any("Host: Python" in line for line in output))
        self.assertTrue(any("Docker daemon" in line for line in output))
        self.assertTrue(any("Workspace config: no config" in line for line in output))


if __name__ == "__main__":
    unittest.main()
