#!/usr/bin/env python3
"""Runs check_runners.py on fixture repositories and asserts its verdicts."""
from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile
import textwrap
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "check_runners.py"


def run(workflows: dict[str, str], visibility: str | None = None) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        wf = pathlib.Path(tmp) / ".github" / "workflows"
        wf.mkdir(parents=True)
        for name, body in workflows.items():
            (wf / name).write_text(textwrap.dedent(body), encoding="utf-8")
        args = [sys.executable, str(SCRIPT), tmp] + (["--visibility", visibility] if visibility else [])
        return subprocess.run(args, capture_output=True, text=True, check=False)


def job(runs_on: str, extra: str = "") -> str:
    return f"on: push\njobs:\n  a:\n    runs-on: {runs_on}\n{extra}    steps:\n      - run: true\n"


class CheckRunnersTests(unittest.TestCase):
    def assertPasses(self, runs_on: str, extra: str = "", visibility: str | None = None) -> None:
        out = run({"w.yml": job(runs_on, extra)}, visibility)
        self.assertEqual(out.returncode, 0, out.stdout)

    def assertFails(self, runs_on: str, extra: str = "", visibility: str | None = None) -> None:
        out = run({"w.yml": job(runs_on, extra)}, visibility)
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("::error::", out.stdout)

    def test_own_runners_pass(self) -> None:
        self.assertPasses("sylphx-linux-standard")
        self.assertPasses("sylphx-linux-xlarge")
        self.assertPasses("[self-hosted, sylphx, macos, standard]")
        self.assertPasses("[self-hosted, macOS, X64]")
        self.assertPasses("${{ inputs.source == 'build' && 'sylphx-linux-xlarge' || 'sylphx-linux-standard' }}")

    def test_hosted_labels_fail(self) -> None:
        for label in ("ubuntu-latest", "ubuntu-24.04", "windows-latest", "macos-14", "macos-latest-xlarge", "[ubuntu-latest]", "[self-hosted, ubuntu-latest]"):
            with self.subTest(label=label):
                self.assertFails(label)

    def test_hosted_fallback_in_expression_fails(self) -> None:
        self.assertFails("${{ vars.RUNNER || 'ubuntu-latest' }}")

    def test_variable_without_own_label_fails(self) -> None:
        self.assertFails("${{ vars.CUBEAGE_ANDROID_BUILD_RUNNER }}")

    def test_hosted_matrix_fails(self) -> None:
        self.assertFails("${{ matrix.os }}", "    strategy:\n      matrix:\n        os: [ubuntu-latest, sylphx-linux-standard]\n")
        self.assertPasses("${{ matrix.os }}", "    strategy:\n      matrix:\n        os: [sylphx-linux-standard]\n")

    def test_unknown_runner_fails(self) -> None:
        self.assertFails("some-other-runner")

    def test_private_is_the_default_and_unknown_fails_closed(self) -> None:
        for visibility in (None, "private", "internal", ""):
            with self.subTest(visibility=visibility):
                self.assertFails("ubuntu-latest", visibility=visibility)

    def test_public_may_use_free_standard_hosted_runners(self) -> None:
        for label in ("ubuntu-latest", "ubuntu-24.04", "ubuntu-24.04-arm", "windows-latest", "windows-2025",
                      "macos-latest", "macos-15", "macos-13", "[ubuntu-latest]"):
            with self.subTest(label=label):
                self.assertPasses(label, visibility="public")
        self.assertPasses("${{ vars.RUNNER || 'ubuntu-latest' }}", visibility="public")
        self.assertPasses("${{ matrix.os }}", "    strategy:\n      matrix:\n        os: [ubuntu-latest, macos-latest, windows-latest]\n",
                          visibility="public")
        self.assertPasses("sylphx-linux-standard", visibility="public")

    def test_larger_and_gpu_runners_fail_everywhere(self) -> None:
        for visibility in ("public", "private"):
            for label in ("macos-latest-xlarge", "macos-14-large", "ubuntu-latest-4-cores", "ubuntu-22.04-16core",
                          "windows-latest-8-cores", "ubuntu-gpu", "gpu-t4-4-core", "{group: larger-runners}",
                          "{group: gpu, labels: [ubuntu-latest]}"):
                with self.subTest(visibility=visibility, label=label):
                    self.assertFails(label, visibility=visibility)
            with self.subTest(visibility=visibility, label="larger fallback"):
                self.assertFails("${{ vars.RUNNER || 'ubuntu-latest-8-cores' }}", visibility=visibility)
            with self.subTest(visibility=visibility, label="larger matrix"):
                self.assertFails("${{ matrix.os }}", "    strategy:\n      matrix:\n        os: [macos-latest-xlarge]\n",
                                 visibility=visibility)

    def test_public_still_fails_unknown_and_unreviewed_runners(self) -> None:
        self.assertFails("some-other-runner", visibility="public")
        self.assertFails("${{ vars.CUBEAGE_ANDROID_BUILD_RUNNER }}", visibility="public")

    def test_reusable_workflow_call_is_skipped(self) -> None:
        out = run({"w.yml": "on: push\njobs:\n  a:\n    uses: Cubeage/.github/.github/workflows/x.yml@main\n"})
        self.assertEqual(out.returncode, 0, out.stdout)


if __name__ == "__main__":
    unittest.main()
