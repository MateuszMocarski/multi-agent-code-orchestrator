import os
import json
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from orchestrator import config, image
from orchestrator import toolchain


class ImageCommandTests(unittest.TestCase):
    def test_package_resources_resolve_from_repository_root(self):
        self.assertEqual(image.ROOT, config.PROJECT_DIR)
        self.assertTrue((image.ROOT / "docker" / "Dockerfile.agent").is_file())
        self.assertTrue((config.PROMPTS_DIR / "developer.txt").is_file())
        self.assertTrue((image.ROOT / "input" / "task.example.md").is_file())

    def test_default_tags_are_backend_specific_and_predictable(self):
        self.assertEqual(
            image.default_image_name("codex"), "orchestrator-agent:codex-py314-v1"
        )
        self.assertEqual(
            image.default_image_name("opencode"),
            "orchestrator-agent:opencode-py314-v1",
        )

    def test_unknown_backend_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "one of: opencode, codex"):
            image.default_image_name("other")

    def test_build_command_selects_backend_and_image(self):
        uid, gid = image.host_identity()
        with tempfile.TemporaryDirectory() as temp_dir:
            command = image.build_command(
                "codex", "custom:tag", Path(temp_dir)
            )
        self.assertEqual(
            command[:10] + command[-3:],
            [
                "docker", "build", "--file", f"{temp_dir}/Dockerfile.agent",
                "--build-arg", "AGENT_BACKEND=codex",
                "--build-arg", f"AGENT_UID={uid}",
                "--build-arg", f"AGENT_GID={gid}",
                "--tag", "custom:tag", temp_dir,
            ],
        )

    def test_requirements_are_passed_as_build_secret_not_context_files(self):
        context = Path("/tmp/context")
        requirements = Path("/target/requirements.txt")
        command = image.build_command(
            "opencode", "custom:tag", context, requirements
        )
        self.assertIn(
            "id=target-requirements,src=/target/requirements.txt", command
        )

    def test_codex_smoke_checks_global_flag_position_and_reviewer_flags(self):
        command = image._smoke_command("codex")
        uid, gid = image.host_identity()
        self.assertIn(f'$(id -u)" = {uid}', command)
        self.assertIn(f'$(id -g)" = {gid}', command)
        self.assertIn("sys.version_info[:2] == (3, 14)", command)
        self.assertIn(f"codex-cli {toolchain.load()['tools']['codex']['version']}", command)
        self.assertIn(
            "codex --ask-for-approval never exec --sandbox workspace-write", command
        )
        self.assertIn("--add-dir /review --model parser-check-model --help", command)
        self.assertNotIn("exec --ask-for-approval", command)

    def test_build_command_passes_central_pins_as_build_arguments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            command = image.build_command("codex", "tag", Path(temp_dir))
        pins = toolchain.load()
        for name, value in (("CODEX_VERSION", pins["tools"]["codex"]["version"]),
                            ("OPENCODE_VERSION", pins["tools"]["opencode"]["version"]),
                            ("PYTEST_VERSION", pins["tools"]["pytest"]["version"])):
            self.assertIn(f"{name}={value}", command)

    def test_build_metadata_records_backend_and_toolchain_identity(self):
        command = image.build_command("opencode", "tag", Path("/tmp"))
        self.assertIn("AGENT_BACKEND_PACKAGE=opencode-ai", command)
        pins = toolchain.load()
        self.assertIn(f"AGENT_BACKEND_VERSION={pins['tools']['opencode']['version']}", command)
        self.assertIn(f"TOOLCHAIN_ID={image.toolchain_identity(pins)}", command)

    def test_image_metadata_must_match_pinned_backend(self):
        pins = toolchain.load()
        metadata = "|".join(("codex", pins["tools"]["codex"]["package"],
                             pins["tools"]["codex"]["version"], image.toolchain_identity(pins)))
        with patch.object(image.subprocess, "run", return_value=SimpleNamespace(stdout=metadata)) as runner:
            image._inspect_image_metadata("codex", "image")
        self.assertIn("--format", runner.call_args.args[0])
        with patch.object(image.subprocess, "run", return_value=SimpleNamespace(stdout="wrong|labels")):
            with self.assertRaisesRegex(RuntimeError, "metadata mismatch"):
                image._inspect_image_metadata("codex", "image")

    def test_legacy_image_without_metadata_remains_inspectable(self):
        with patch.object(image.subprocess, "run", return_value=SimpleNamespace(stdout="|".join(["<no value>"] * 4))), \
             patch("builtins.print") as printer:
            image._inspect_image_metadata("codex", "legacy")
        printer.assert_called_once()

    def test_same_process_build_and_metadata_read_updated_toolchain_lock(self):
        # image and toolchain are imported above before the lock is changed.
        with tempfile.TemporaryDirectory() as temp:
            lock = Path(temp) / "toolchain.json"
            data = toolchain.load()
            lock.write_text(json.dumps(data), encoding="utf-8")
            with patch.object(toolchain, "LOCK_FILE", lock):
                original = image.build_command("codex", "tag", Path(temp))
                self.assertIn(f"CODEX_VERSION={data['tools']['codex']['version']}", original)
                data["tools"]["codex"]["version"] = "9.8.7"
                lock.write_text(json.dumps(data), encoding="utf-8")
                updated = image.build_command("codex", "tag", Path(temp))
                self.assertIn("CODEX_VERSION=9.8.7", updated)
                identity = image.toolchain_identity(data)
                self.assertIn(f"TOOLCHAIN_ID={identity}", updated)
                metadata = "|".join(("codex", data["tools"]["codex"]["package"], "9.8.7", identity))
                with patch.object(image.subprocess, "run", return_value=SimpleNamespace(stdout=metadata)):
                    image._inspect_image_metadata("codex", "tag")

    def test_opencode_smoke_uses_only_non_inference_cli_commands(self):
        command = image._smoke_command("opencode")
        self.assertIn("opencode --version", command)
        self.assertIn("opencode --help", command)
        self.assertNotIn("opencode run", command)

    def test_explicit_missing_environment_file_fails_clearly(self):
        environment = os.environ.copy()
        environment["ORCHESTRATOR_ENV_FILE"] = "/path/that/does/not/exist"
        result = subprocess.run(
            [str(image.ROOT / "image.sh"), "--help"],
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Environment file not found", result.stderr)

    def test_default_environment_file_is_optional(self):
        environment = os.environ.copy()
        environment.pop("ORCHESTRATOR_ENV_FILE", None)
        with tempfile.TemporaryDirectory() as temp_dir:
            checkout = Path(temp_dir)
            shutil.copyfile(image.ROOT / "image.sh", checkout / "image.sh")
            (checkout / "image.sh").chmod(0o755)
            shutil.copytree(image.ROOT / "orchestrator", checkout / "orchestrator")
            shutil.copyfile(image.ROOT / "toolchain.json", checkout / "toolchain.json")
            result = subprocess.run(
                [str(checkout / "image.sh"), "--help"],
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
