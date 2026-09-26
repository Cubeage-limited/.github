#!/usr/bin/env python3
"""Fail when a workflow can run on a GitHub-hosted runner.

Cubeage runs every workflow on its own runners (GitHub budget $0). This reads
each workflow under .github/workflows and reports a job whose `runs-on` is, or
can evaluate to, a GitHub-hosted label:
  * a literal hosted label (ubuntu-*, windows-*, macos-*), scalar or list;
  * a hosted label anywhere inside a `${{ ... }}` expression (e.g. a fallback);
  * a matrix value used by `runs-on` that names a hosted label;
  * a `runs-on` taken from `vars.*` or `inputs.*` with no exact own label,
    because its value is set outside the reviewed file.
Own labels: sylphx-* and [self-hosted, ...] lists. Exit 1 with one line per finding.
"""
from __future__ import annotations

import pathlib
import re
import sys

import yaml

HOSTED = re.compile(r"\b(ubuntu|windows|macos)-(latest|\d[\w.-]*)\b", re.IGNORECASE)
OWN = re.compile(r"^(sylphx-[\w-]+|self-hosted)$")
INDIRECT = re.compile(r"\b(vars|inputs|github\.event\.inputs)\.")


def job_findings(path: str, name: str, job: dict) -> list[str]:
    runs_on = job.get("runs-on")
    if runs_on is None:
        return []  # a reusable-workflow call (`uses:`) picks its own runner
    where = f"{path}: job '{name}'"
    labels = runs_on if isinstance(runs_on, list) else [runs_on]
    if isinstance(runs_on, dict):
        labels = [runs_on.get("group", ""), *(runs_on.get("labels") or [])]
    out: list[str] = []
    # A list that asks for `self-hosted` only ever matches our own runners; its
    # other labels (OS, arch, size) are ours to name, in any case.
    self_hosted = isinstance(runs_on, list) and any(str(x).strip().lower() == "self-hosted" for x in runs_on)
    for label in labels:
        text = str(label)
        if HOSTED.search(text):
            out.append(f"{where}: GitHub-hosted runner {text!r}")
        elif "${{" in text:
            if "matrix." in text:
                matrix = (job.get("strategy") or {}).get("matrix") or {}
                if HOSTED.search(yaml.safe_dump(matrix)):
                    out.append(f"{where}: runs-on {text!r} takes a GitHub-hosted label from its matrix")
            elif INDIRECT.search(text) and not re.search(r"'(sylphx-[\w-]+)'", text):
                out.append(f"{where}: runs-on {text!r} comes from a variable with no own-runner label")
        elif not self_hosted and not OWN.match(text.strip()):
            out.append(f"{where}: runner {text!r} is not one of ours (sylphx-* or self-hosted)")
    return out


def check(root: pathlib.Path) -> list[str]:
    findings: list[str] = []
    wf_dir = root / ".github" / "workflows"
    for path in sorted(list(wf_dir.glob("*.yml")) + list(wf_dir.glob("*.yaml"))):
        rel = str(path.relative_to(root))
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as err:
            findings.append(f"{rel}: not valid YAML ({err.__class__.__name__})")
            continue
        for name, job in (doc.get("jobs") or {}).items():
            if isinstance(job, dict):
                findings.extend(job_findings(rel, name, job))
    return findings


def main(argv: list[str]) -> int:
    root = pathlib.Path(argv[1] if len(argv) > 1 else ".")
    findings = check(root)
    for line in findings:
        print(f"::error::{line}")
    if findings:
        print(f"{len(findings)} job(s) can run on a GitHub-hosted runner; use sylphx-linux-standard, "
              "sylphx-linux-xlarge or [self-hosted, sylphx, macos, standard].")
        return 1
    print("SELF_HOSTED_RUNNERS_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
