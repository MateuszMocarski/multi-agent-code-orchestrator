import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from orchestrator import config, image


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
            command,
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
        self.assertIn("codex-cli 0.156.1", command)
        self.assertIn(
            "codex --ask-for-approval never exec --sandbox workspace-write", command
        )
        self.assertIn("--add-dir /review --model parser-check-model --help", command)
        self.assertNotIn("exec --ask-for-approval", command)

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
            result = subprocess.run(
                [str(checkout / "image.sh"), "--help"],
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
