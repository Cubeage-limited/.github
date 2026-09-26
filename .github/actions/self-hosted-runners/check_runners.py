#!/usr/bin/env python3
"""Fail when a workflow can run on a runner that costs GitHub money.

The goal is zero GitHub spend (owner standards/dx.md, owner#750):
  * a PRIVATE repository runs every job on our own runners;
  * a PUBLIC repository may also use GitHub's free standard hosted runners
    (ubuntu-*, windows-*, macos-* with no size suffix);
  * no repository may use a larger or GPU hosted runner, or a runner group.

For each workflow under .github/workflows it reads every job's `runs-on`:
a scalar or list label, a label inside a `${{ ... }}` expression (e.g. a
fallback), a matrix value used by `runs-on`, and a `runs-on` taken from
`vars.*`/`inputs.*` with no exact own label (its value is set outside the
reviewed file, so it could name anything). Own labels: sylphx-* and
[self-hosted, ...] lists.

Usage: check_runners.py [ROOT] [--visibility private|public]
An unknown visibility is treated as private (fail closed). Exit 1 with one
line per finding.
"""
from __future__ import annotations

import pathlib
import re
import sys

import yaml

HOSTED = re.compile(r"\b(?:ubuntu|windows|macos)-[\w.-]+", re.IGNORECASE)
# GitHub's free standard images: an OS and version (or latest), optionally the
# free arm/intel variant. Anything else under these prefixes is a larger runner.
STANDARD = re.compile(r"^(?:ubuntu|windows|macos)-(?:latest|\d+(?:\.\d+)?)(?:-(?:arm|intel))?$", re.IGNORECASE)
GPU = re.compile(r"gpu", re.IGNORECASE)
OWN = re.compile(r"^(sylphx-[\w-]+|self-hosted)$")
INDIRECT = re.compile(r"\b(vars|inputs|github\.event\.inputs)\.")


def hosted_verdicts(text: str, public: bool) -> list[str]:
    """Findings for every hosted-looking label named anywhere in `text`."""
    out: list[str] = []
    for label in HOSTED.findall(text):
        if GPU.search(label) or not STANDARD.match(label):
            out.append(f"larger or GPU GitHub-hosted runner {label!r} (never allowed)")
        elif not public:
            out.append(f"GitHub-hosted runner {label!r} in a private repository")
    return out


def job_findings(path: str, name: str, job: dict, public: bool) -> list[str]:
    runs_on = job.get("runs-on")
    if runs_on is None:
        return []  # a reusable-workflow call (`uses:`) picks its own runner
    where = f"{path}: job '{name}'"
    out: list[str] = []
    if isinstance(runs_on, dict):
        group = str(runs_on.get("group", ""))
        if group and not group.startswith("sylphx"):
            out.append(f"{where}: runner group {group!r} is a GitHub larger-runner group (never allowed)")
        labels = list(runs_on.get("labels") or [])
    else:
        labels = runs_on if isinstance(runs_on, list) else [runs_on]
    # A list that asks for `self-hosted` only ever matches our own runners; its
    # other labels (OS, arch, size) are ours to name, in any case.
    self_hosted = isinstance(runs_on, list) and any(str(x).strip().lower() == "self-hosted" for x in runs_on)
    for label in labels:
        text = str(label)
        if GPU.search(text) and not OWN.match(text.strip()):
            out.append(f"{where}: GPU runner {text!r} (never allowed)")
            continue
        hosted = hosted_verdicts(text, public)
        if hosted:
            out.extend(f"{where}: {v}" for v in hosted)
        elif HOSTED.search(text):
            continue  # a free standard hosted label in a public repository
        elif "${{" in text:
            if "matrix." in text:
                matrix = (job.get("strategy") or {}).get("matrix") or {}
                out.extend(f"{where}: runs-on {text!r} takes {v} from its matrix"
                           for v in hosted_verdicts(yaml.safe_dump(matrix), public))
            elif INDIRECT.search(text) and not re.search(r"'(sylphx-[\w-]+)'", text):
                out.append(f"{where}: runs-on {text!r} comes from a variable with no own-runner label")
        elif not self_hosted and not OWN.match(text.strip()):
            out.append(f"{where}: runner {text!r} is not one of ours (sylphx-* or self-hosted)")
    return out


def check(root: pathlib.Path, public: bool = False) -> list[str]:
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
                findings.extend(job_findings(rel, name, job, public))
    return findings


def main(argv: list[str]) -> int:
    args = argv[1:]
    visibility = "private"
    if "--visibility" in args:
        i = args.index("--visibility")
        visibility = (args[i + 1] if i + 1 < len(args) else "").strip().lower()
        del args[i:i + 2]
    public = visibility == "public"
    root = pathlib.Path(args[0] if args else ".")
    findings = check(root, public)
    for line in findings:
        print(f"::error::{line}")
    if findings:
        allowed = "sylphx-linux-standard, sylphx-linux-xlarge or [self-hosted, sylphx, macos, standard]"
        if public:
            allowed += ", or a free standard hosted label (ubuntu-latest, windows-latest, macos-latest)"
        print(f"{len(findings)} job(s) can run on a runner that costs GitHub money; use {allowed}.")
        return 1
    print(f"SELF_HOSTED_RUNNERS_PASS visibility={'public' if public else 'private'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
