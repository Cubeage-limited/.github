#!/usr/bin/env python3
"""Tests for the cubeage-sdk-pin release gate (python3 -m unittest / direct run)."""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import check_sdk_pin  # noqa: E402

UNITY = "https://github.com/Cubeage/cubeage-native-sdk.git?path=engines/unity/com.cubeage.sdk#v{v}"


def manifest(v):
    return '{\n  "dependencies": {\n    "com.cubeage.sdk": "%s"\n  }\n}\n' % UNITY.format(v=v)


class GateCase(unittest.TestCase):
    def run_gate(self, files, *extra):
        with tempfile.TemporaryDirectory() as root:
            subprocess.run(["git", "init", "-q", root], check=True)
            for rel, body in files.items():
                full = os.path.join(root, rel)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                mode = "wb" if isinstance(body, bytes) else "w"
                with open(full, mode) as fh:
                    fh.write(body)
            subprocess.run(["git", "-C", root, "add", "-A"], check=True)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = check_sdk_pin.main([root, *extra])
            return code, out.getvalue()


class RefusedPins(GateCase):
    def test_manifest_pinning_v4_2_19_is_refused(self):
        code, out = self.run_gate({"client/Packages/manifest.json": manifest("4.2.19")})
        self.assertEqual(1, code)
        self.assertIn(
            "::error file=client/Packages/manifest.json,line=3::cubeage-native-sdk 4.2.19 is refused by the release gate "
            "(V1 guest-stranding bug in 4.2.18/4.2.19); pin v4.2.20 or later.",
            out,
        )

    def test_manifest_pinning_v4_2_18_is_refused(self):
        code, _ = self.run_gate({"Packages/manifest.json": manifest("4.2.18")})
        self.assertEqual(1, code)

    def test_packages_lock_version_is_refused(self):
        lock = '{\n  "dependencies": {\n    "com.cubeage.sdk": {\n      "version": "%s",\n      "hash": "abc"\n    }\n  }\n}\n' % UNITY.format(v="4.2.18")
        code, out = self.run_gate({"Packages/packages-lock.json": lock})
        self.assertEqual(1, code)
        self.assertIn("file=Packages/packages-lock.json,line=4", out)

    def test_solar2d_pin_json_and_build_settings(self):
        pin = '{\n  "ref": "v4.2.19",\n  "sdkVersion": "4.2.19"\n}\n'
        settings = 'plugins = {\n  url = "https://github.com/Cubeage/cubeage-native-sdk/releases/download/v4.2.18/android.tgz" },\n'
        code, out = self.run_gate({"client/ci/cubeage_sdk_pin.json": pin, "client/src/build.settings": settings})
        self.assertEqual(1, code)
        self.assertIn("file=client/ci/cubeage_sdk_pin.json,line=2", out)
        self.assertIn("file=client/ci/cubeage_sdk_pin.json,line=3", out)
        self.assertIn("file=client/src/build.settings,line=2", out)

    def test_spm_package_swift_single_and_multi_line(self):
        one = '.package(url: "https://github.com/Cubeage/cubeage-native-sdk.git", from: "4.2.19"),\n'
        multi = '.package(\n  url: "https://github.com/Cubeage/cubeage-native-sdk",\n  exact: "4.2.18"\n),\n'
        code, out = self.run_gate({"a/Package.swift": one, "b/Package.swift": multi})
        self.assertEqual(1, code)
        self.assertIn("file=a/Package.swift,line=1", out)
        self.assertIn("file=b/Package.swift,line=3", out)

    def test_spm_package_resolved(self):
        resolved = (
            '{\n "pins" : [\n  {\n   "identity" : "cubeage-native-sdk",\n'
            '   "location" : "https://github.com/Cubeage/cubeage-native-sdk.git",\n'
            '   "state" : {\n    "revision" : "abc",\n    "version" : "4.2.19"\n   }\n  }\n ]\n}\n'
        )
        code, out = self.run_gate({"App.xcworkspace/xcshareddata/swiftpm/Package.resolved": resolved})
        self.assertEqual(1, code)
        self.assertIn("line=8", out)

    def test_gradle_coordinate(self):
        code, out = self.run_gate({"app/build.gradle": "dependencies {\n  implementation 'com.cubeage:sdk-android:4.2.19'\n}\n"})
        self.assertEqual(1, code)
        self.assertIn("file=app/build.gradle,line=2", out)

    def test_workflow_env_and_clone_and_checkout_ref(self):
        wf = (
            "env:\n  CUBEAGE_SDK_REF: v4.2.19\njobs:\n  a:\n    steps:\n"
            "      - run: git clone --branch v4.2.18 https://github.com/Cubeage/cubeage-native-sdk.git x\n"
            "      - uses: actions/checkout@v4\n        with:\n          repository: Cubeage/cubeage-native-sdk\n          ref: v4.2.19\n"
        )
        code, out = self.run_gate({".github/workflows/build.yml": wf})
        self.assertEqual(1, code)
        for line in (2, 6, 10):
            self.assertIn(f"file=.github/workflows/build.yml,line={line}::", out)

    def test_gitmodules_branch(self):
        gm = '[submodule "sdk"]\n\tpath = sdk\n\turl = https://github.com/Cubeage/cubeage-native-sdk.git\n\tbranch = v4.2.19\n'
        code, out = self.run_gate({".gitmodules": gm})
        self.assertEqual(1, code)
        self.assertIn("file=.gitmodules,line=4", out)

    def test_minimum_input_tightens(self):
        code, out = self.run_gate({"Packages/manifest.json": manifest("4.2.15")}, "--minimum", "4.2.20")
        self.assertEqual(1, code)
        self.assertIn("below the release gate minimum 4.2.20", out)

    def test_deny_input_overrides(self):
        code, _ = self.run_gate({"Packages/manifest.json": manifest("4.2.15")}, "--deny", "4.2.15")
        self.assertEqual(1, code)


class AllowedPins(GateCase):
    def test_allowed_versions_pass_and_are_summarised(self):
        code, out = self.run_gate({"a/Packages/manifest.json": manifest("4.2.15"), "b/Packages/manifest.json": manifest("4.2.20")})
        self.assertEqual(0, code)
        self.assertIn("2 cubeage-native-sdk pin(s) found", out)
        self.assertNotIn("::error", out)

    def test_minimum_accepts_equal_and_newer(self):
        code, _ = self.run_gate({"a/manifest.json": manifest("4.2.20"), "b/manifest.json": manifest("4.3.0")}, "--minimum", "4.2.20")
        self.assertEqual(0, code)

    def test_prose_and_markdown_never_fail(self):
        files = {
            "README.md": "Do not use https://github.com/Cubeage/cubeage-native-sdk.git#v4.2.19 (bug).\n",
            "docs/notes.txt": "CUBEAGE_SDK_REF: v4.2.19\n",
            "client/src/build.settings": "-- plugin.cubeage v4.2.19 embeds notifications\n",
            "app.lua": 'print("cubeage-native-sdk 4.2.19 changelog")\n',
        }
        code, out = self.run_gate(files)
        self.assertEqual(0, code, out)

    def test_ignore_marker_skips_line(self):
        wf = "# note\n# cubeage-sdk-pin: ignore\nCUBEAGE_SDK_REF: v4.2.19\n"
        code, _ = self.run_gate({"ci/test.env": wf})
        self.assertEqual(0, code)

    def test_skips_library_meta_binary_and_large(self):
        line = manifest("4.2.19")
        files = {
            "Library/PackageCache/manifest.json": line,
            "x/manifest.json.meta": line,
            "bin/blob.json": b"\0\0" + line.encode(),
            "big/manifest.json": line + " " * (3 * 1024 * 1024),
            "node_modules/p/manifest.json": line,
        }
        code, out = self.run_gate(files)
        self.assertEqual(0, code, out)
        self.assertIn("0 cubeage-native-sdk pin(s) found", out)

    def test_repo_without_pins_passes(self):
        code, _ = self.run_gate({"src/main.py": "print('hi')\n"})
        self.assertEqual(0, code)


if __name__ == "__main__":
    unittest.main()
