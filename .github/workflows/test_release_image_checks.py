"""Exercise the actual push steps without publishing or requiring a Docker daemon."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from release_image_ref import signing_reference

ROOT = Path(__file__).resolve().parents[2]
DIGEST = "sha256:" + "a" * 64


class SigningReferenceTests(unittest.TestCase):
    def test_portal_and_runner_keep_the_registry_repository(self):
        for repository in ("ghcr.io/trellumhq/trellum", "ghcr.io/trellumhq/trellum-runner"):
            self.assertEqual(signing_reference(repository, {"digest": DIGEST}), f"{repository}@{DIGEST}")

    def test_invalid_descriptors_fail_closed(self):
        for descriptor in (None, [], {}, {"digest": None}, {"digest": "trellum@" + DIGEST},
                           {"digest": "sha256:abc"}, {"digest": DIGEST + "\n"}):
            with self.subTest(descriptor=descriptor), self.assertRaises((TypeError, ValueError)):
                signing_reference("ghcr.io/trellumhq/trellum", descriptor)

    def test_local_alias_and_wrong_registry_are_rejected(self):
        for repository in ("trellum", "docker.io/trellumhq/trellum", "ghcr.io/another/project"):
            with self.subTest(repository=repository), self.assertRaises(ValueError):
                signing_reference(repository, {"digest": DIGEST})

    def test_cli_rejects_malformed_and_oversized_input_without_outputs(self):
        for payload in ("not json", "{}", " " * 65537):
            result = subprocess.run(
                [sys.executable, str(ROOT / ".github/workflows/release_image_ref.py"),
                 "ghcr.io/trellumhq/trellum"],
                input=payload, text=True, capture_output=True, timeout=10, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")


class PublishedPushStepTests(unittest.TestCase):
    def run_step(self, name, descriptor=None, registry_exit=0):
        workflow = (ROOT / ".github/workflows/release.yml").read_text()
        step = workflow.split(f"      - name: {name}\n", 1)[1].split("\n      - ", 1)[0]
        script = step.split("        run: |\n", 1)[1]
        script = "\n".join(line[10:] for line in script.splitlines())
        script = script.replace("${{ steps.version.outputs.version }}", "v0.10.1")
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            docker = temp / "docker"
            docker.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "args = sys.argv[1:]\n"
                "with open(os.environ['DOCKER_CALLS'], 'a') as stream:\n"
                "    stream.write(json.dumps(args) + '\\n')\n"
                "if args[:3] == ['buildx', 'imagetools', 'inspect']:\n"
                "    print(os.environ['REGISTRY_DESCRIPTOR'])\n"
                "    sys.exit(int(os.environ['REGISTRY_EXIT']))\n"
                "elif args[0] == 'inspect':\n"
                f"    print('trellum@{DIGEST}')\n"
                "elif args[0] not in ['tag', 'push']:\n"
                "    sys.exit(97)\n"
            )
            docker.chmod(0o755)
            output = temp / "output"
            output.touch()
            calls = temp / "calls"
            result = subprocess.run(
                ["bash", "-e", "-c", script], cwd=ROOT, text=True, capture_output=True, timeout=10, check=False,
                env={**os.environ, "PATH": f"{temp}{os.pathsep}{os.environ['PATH']}",
                     "REGISTRY": "ghcr.io", "IMAGE_NAME": "TrellumHQ/Trellum",
                     "GITHUB_OUTPUT": str(output), "DOCKER_CALLS": str(calls),
                     "REGISTRY_DESCRIPTOR": json.dumps(descriptor), "REGISTRY_EXIT": str(registry_exit)},
            )
            return result, output.read_text(), [json.loads(line) for line in calls.read_text().splitlines()]

    def test_both_steps_resolve_the_pushed_repository_not_a_local_alias(self):
        for name, suffix in (("Push the proven image", ""), ("Push the runner image", "-runner")):
            with self.subTest(name=name):
                result, output, calls = self.run_step(name, {"digest": DIGEST})
                self.assertEqual(result.returncode, 0, result.stderr)
                repository = f"ghcr.io/trellumhq/trellum{suffix}"
                self.assertEqual(output, f"image={repository}\ndigest={repository}@{DIGEST}\n")
                self.assertEqual(calls[-1], ["buildx", "imagetools", "inspect", f"{repository}:v0.10.1",
                                             "--format", "{{json .Manifest}}"])

    def test_registry_failure_or_invalid_digest_cannot_reach_signing(self):
        for name in ("Push the proven image", "Push the runner image"):
            for descriptor, exit_code in (({}, 0), ({"digest": "wrong"}, 0), ({"digest": DIGEST}, 1)):
                with self.subTest(name=name, descriptor=descriptor, exit_code=exit_code):
                    result, output, _ = self.run_step(name, descriptor, exit_code)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(output, "")


if __name__ == "__main__":
    unittest.main()
