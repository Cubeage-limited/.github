#!/usr/bin/env python3
"""Release gate: refuse builds that pin a denied cubeage-native-sdk version.

Scans the tracked files of a checkout for real pin sites (never prose) and exits
1 when any pin names a denied version or a version below --minimum.
Python 3 standard library only.

A line (or the line above it) containing ``cubeage-sdk-pin: ignore`` is skipped,
for tests that must name a refused version.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from typing import Iterator, NamedTuple

MAX_BYTES = 2 * 1024 * 1024
SDK = "cubeage-native-sdk"
IGNORE_MARKER = "cubeage-sdk-pin: ignore"
SKIP_DIR_PARTS = ("Library", "node_modules")
SKIP_SUFFIXES = (".meta", ".md", ".markdown", ".rst", ".txt", ".adoc")
SELF_DIR = ".github/actions/cubeage-sdk-pin/"

VER = r"(\d+\.\d+\.\d+)(?:-[0-9A-Za-z.]+)?(?![0-9A-Za-z.])"

# A version attached to the SDK repo on one line: git ref (#v4.2.1, @v4.2.1),
# release/archive URL (/v4.2.1/, tags/v4.2.1), or a CLI tag argument.
RE_SDK_REF = re.compile(
    r"(?:[#@/=:]|--branch[ =]|--tag[ =]|-b |\bdownload |\btag )[\"']?v?" + VER
)
# Key/value pin: CUBEAGE_SDK_REF: v4.2.1, cubeage_sdk_version = "4.2.1".
RE_KEY = re.compile(
    r"\bcubeage[_-]?sdk[_-]?(?:ref|version|tag)\b[\"']?\s*[:=]\s*[\"']?v?" + VER,
    re.IGNORECASE,
)
# Solar2D pin file keys (client/ci/cubeage_sdk_pin.json).
RE_PIN_JSON = re.compile(r"\"(?:ref|sdkVersion|tag|version)\"\s*:\s*\"v?" + VER)
# Gradle / TOML / properties coordinates: com.cubeage.something:artifact:4.2.1
RE_COORD = re.compile(r"\bcom\.cubeage[\w.-]*:[\w.-]+:v?" + VER)
# Look-ahead forms that pair a repo mention with a version on a later line.
RE_LOOK = re.compile(
    r"(?:\bfrom\s*:|\bexact\s*:|\.exact\(|\brevision\s*:|\bbranch\s*[:=]|\bref\s*:|\"version\"\s*:|\bversion\s*[:=])"
    r"\s*[\"']?v?" + VER
)
LOOKAHEAD = 6


class Hit(NamedTuple):
    path: str
    line: int
    version: str


def parse_version(text: str) -> tuple[int, int, int]:
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", text.strip())
    if not m:
        raise ValueError(f"not a version: {text!r}")
    return int(m[1]), int(m[2]), int(m[3])


def tracked_files(root: str) -> list[str]:
    out = subprocess.run(
        ["git", "-C", root, "ls-files", "-z"], check=True, capture_output=True
    ).stdout.decode("utf-8", "surrogateescape")
    return [p for p in out.split("\0") if p]


def skipped(path: str) -> bool:
    if path.startswith(SELF_DIR) or path.endswith(SKIP_SUFFIXES):
        return True
    return any(part in SKIP_DIR_PARTS for part in path.split("/"))


def read_text(root: str, path: str) -> list[str] | None:
    full = os.path.join(root, path)
    try:
        if os.path.islink(full) or not os.path.isfile(full):
            return None
        if os.path.getsize(full) > MAX_BYTES:
            return None
        with open(full, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", "replace").splitlines()


def is_comment(path: str, line: str) -> bool:
    s = line.lstrip()
    if path.endswith(".json"):
        return False
    return s.startswith(("#", "//", "--", "/*", "*"))


def scan_lines(path: str, lines: list[str]) -> Iterator[Hit]:
    base = os.path.basename(path)
    is_pin_json = base.startswith("cubeage_sdk_pin") and base.endswith(".json")
    is_gradle = base.endswith((".gradle", ".kts", ".toml", ".properties", ".pom"))
    is_swift = base in ("Package.swift", "Package.resolved")
    is_gitmodules = base == ".gitmodules"
    seen: set[tuple[int, str]] = set()

    def emit(n: int, ver: str) -> Iterator[Hit]:
        if (n, ver) not in seen:
            seen.add((n, ver))
            yield Hit(path, n, ver)

    for i, line in enumerate(lines):
        if IGNORE_MARKER in line or (i > 0 and IGNORE_MARKER in lines[i - 1]):
            continue
        if is_comment(path, line):
            continue
        n = i + 1
        if is_pin_json:
            for m in RE_PIN_JSON.finditer(line):
                yield from emit(n, m[1])
        for m in RE_KEY.finditer(line):
            yield from emit(n, m[1])
        if is_gradle:
            for m in RE_COORD.finditer(line):
                yield from emit(n, m[1])
        if SDK in line:
            for m in RE_SDK_REF.finditer(line):
                yield from emit(n, m[1])
            if not RE_SDK_REF.search(line) and (is_swift or is_gitmodules or "repository" in line or is_gradle or "url" in line.lower() or "location" in line):
                window = lines[i : i + 1 + LOOKAHEAD]
                for j, nxt in enumerate(window):
                    if IGNORE_MARKER in nxt:
                        break
                    m = RE_LOOK.search(nxt)
                    if m:
                        yield from emit(i + 1 + j, m[1])
                        break
                    if j and SDK in nxt:
                        break


def scan_submodules(root: str) -> Iterator[tuple[str, int, str | None]]:
    """Yield (path, line, version-or-None) for submodules of the SDK repo."""
    gm = os.path.join(root, ".gitmodules")
    if not os.path.isfile(gm):
        return
    with open(gm, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    sect_path, sect_line, is_sdk = None, 0, False
    blocks = []
    for i, line in enumerate(lines):
        if line.strip().startswith("[submodule"):
            if sect_path and is_sdk:
                blocks.append((sect_path, sect_line))
            sect_path, sect_line, is_sdk = None, i + 1, False
        m = re.match(r"\s*path\s*=\s*(.+?)\s*$", line)
        if m:
            sect_path = m[1]
        if re.match(r"\s*url\s*=", line) and SDK in line:
            is_sdk = True
    if sect_path and is_sdk:
        blocks.append((sect_path, sect_line))
    for sub, ln in blocks:
        tags = subprocess.run(
            ["git", "-C", os.path.join(root, sub), "tag", "--points-at", "HEAD"],
            capture_output=True,
        ).stdout.decode().split()
        vers = [t for t in tags if re.match(r"v?\d+\.\d+\.\d+", t)]
        yield sub, ln, (vers[0] if vers else None)


def evaluate(hits: list[Hit], deny: set[tuple[int, int, int]], minimum) -> list[tuple[Hit, str]]:
    bad = []
    for h in hits:
        v = parse_version(h.version)
        if v in deny:
            bad.append((
                h,
                f"cubeage-native-sdk {h.version} is refused by the release gate "
                "(V1 guest-stranding bug in 4.2.18/4.2.19); pin v4.2.20 or later.",
            ))
        elif minimum and v < minimum:
            want = ".".join(map(str, minimum))
            bad.append((
                h,
                f"cubeage-native-sdk {h.version} is below the release gate minimum "
                f"{want}; pin v{want} or later.",
            ))
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--deny", default="4.2.18,4.2.19")
    ap.add_argument("--minimum", default="")
    args = ap.parse_args(argv)

    deny = {parse_version(x) for x in args.deny.split(",") if x.strip()}
    minimum = parse_version(args.minimum) if args.minimum.strip() else None

    hits: list[Hit] = []
    for path in tracked_files(args.root):
        if skipped(path):
            continue
        lines = read_text(args.root, path)
        if lines is not None:
            hits.extend(scan_lines(path, lines))

    unresolved = []
    for sub, ln, ver in scan_submodules(args.root):
        if ver:
            hits.append(Hit(".gitmodules", ln, ver.lstrip("v")))
        else:
            unresolved.append((sub, ln))

    print(f"cubeage-sdk-pin: {len(hits)} cubeage-native-sdk pin(s) found")
    for h in hits:
        print(f"  {h.path}:{h.line}  {h.version}")
    for sub, ln in unresolved:
        print(f"  .gitmodules:{ln}  submodule {sub} (commit pin, version not resolvable here)")

    bad = evaluate(hits, deny, minimum)
    for h, msg in bad:
        print(f"::error file={h.path},line={h.line}::{msg}")
    if bad:
        print(f"cubeage-sdk-pin: FAILED, {len(bad)} refused pin(s)")
        return 1
    print("cubeage-sdk-pin: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
