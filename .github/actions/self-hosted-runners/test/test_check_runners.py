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


def run(workflows: dict[str, str]) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        wf = pathlib.Path(tmp) / ".github" / "workflows"
        wf.mkdir(parents=True)
        for name, body in workflows.items():
            (wf / name).write_text(textwrap.dedent(body), encoding="utf-8")
        return subprocess.run([sys.executable, str(SCRIPT), tmp], capture_output=True, text=True, check=False)


def job(runs_on: str, extra: str = "") -> str:
    return f"on: push\njobs:\n  a:\n    runs-on: {runs_on}\n{extra}    steps:\n      - run: true\n"


class CheckRunnersTests(unittest.TestCase):
    def assertPasses(self, runs_on: str, extra: str = "") -> None:
        out = run({"w.yml": job(runs_on, extra)})
        self.assertEqual(out.returncode, 0, out.stdout)

    def assertFails(self, runs_on: str, extra: str = "") -> None:
        out = run({"w.yml": job(runs_on, extra)})
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

    def test_reusable_workflow_call_is_skipped(self) -> None:
        out = run({"w.yml": "on: push\njobs:\n  a:\n    uses: Cubeage/.github/.github/workflows/x.yml@main\n"})
        self.assertEqual(out.returncode, 0, out.stdout)


if __name__ == "__main__":
    unittest.main()
