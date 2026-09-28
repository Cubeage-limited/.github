#!/usr/bin/env python3
"""Fail if an org workflow names a retired distribution service (owner#799).

Firebase App Distribution and Appetize.io are retired outright - no step, not
even a notice one - so no workflow in this repository may put either name back.
Case and separators are squashed, so "App Distribution", "AppDistribution",
"app-distribution" and "APP DISTRIBUTION" all hit the same fence.

Usage:
    check_retired_distribution.py [root]          scan .github/workflows/*.yml
    check_retired_distribution.py --self-test     prove the fence inverts
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

SPELLINGS = (
    "Firebase App Distribution",
    "AppDistribution",
    "app-distribution",
    "APP DISTRIBUTION",
    "firebase_app_distribution",
)


def squashed(text: str) -> str:
    """Lower-case and drop every non-letter, so spellings cannot hide."""

    return re.sub(r"[^a-z]", "", text.lower())


def offences(root: Path) -> list[str]:
    found: list[str] = []
    for path in sorted((root / ".github" / "workflows").glob("*.yml")):
        source = path.read_text(encoding="utf-8")
        if "appdistribution" in squashed(source):
            found.append(f"{path.name} names Firebase App Distribution (retired, owner#799)")
        if "appetize" in source.lower():
            found.append(f"{path.name} names Appetize.io (retired, owner#799)")
    return found


def self_test() -> None:
    """Green on clean text and the device skip notice; red on every spelling."""

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        workflows = root / ".github" / "workflows"
        workflows.mkdir(parents=True)
        clean = (
            "name: build\n"
            "jobs:\n"
            "  x:\n"
            "    steps:\n"
            "      - name: 'Android runtime smoke (skipped: device step not served)'\n"
            "        run: echo \"::notice title=Device step skipped::Firebase Test Lab is retired\"\n"
        )
        target = workflows / "build.yml"
        target.write_text(clean, encoding="utf-8")
        if offences(root):
            raise SystemExit("self-test: clean workflow text must pass")
        for spelling in SPELLINGS:
            target.write_text(clean + f"# {spelling}\n", encoding="utf-8")
            if not offences(root):
                raise SystemExit(f"self-test: {spelling!r} must be caught")
        target.write_text(clean + "# appetize.io\n", encoding="utf-8")
        if not offences(root):
            raise SystemExit("self-test: Appetize must be caught")


def main() -> int:
    args = sys.argv[1:]
    if "--self-test" in args:
        self_test()
        print("Retired distribution fence self-test: PASS")
        return 0
    root = Path(args[0]) if args else Path(".")
    found = offences(root)
    for message in found:
        print(f"::error::{message}", file=sys.stderr)
    if found:
        return 1
    print("Retired distribution fence: no workflow names a retired service (owner#799)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
