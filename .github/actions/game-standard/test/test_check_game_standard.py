#!/usr/bin/env python3
"""Runs check_game_standard.py on fixture title repositories and asserts its verdicts.

Run from the repository root: python3 -m unittest discover -s .github/actions/game-standard/test
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ACTION = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ACTION / "check_game_standard.py"
RULES = json.loads((ACTION / "rules.json").read_text(encoding="utf-8"))

TODAY = "2026-09-27"  # every fixture date is read against this day
FUTURE = "2027-03-31"
PAST = "2026-01-01"
CALENDAR = "liveops/calendar.toml"


def musts(modules: tuple[str, ...]) -> list[str]:
    """Every MUST rule a title declaring these modules owes."""
    wanted = {"core", *modules}
    return sorted({rule["id"] for rule in RULES["rules"] if rule["level"] == "MUST" and rule["module"] in wanted})


def event(template: str = "weekend-ladder", start: str = "2026-10-01", end: str = "2027-03-01") -> str:
    return f'[[event]]\ntemplate = "{template}"\nstart = {start}\nend = {end}\n'


def manifest(
    modules: tuple[str, ...] = ("card-board",),
    statuses: dict[str, str] | None = None,
    drop: tuple[str, ...] = (),
    first_download: str = "{ android = 120, ios = 120, web = 3 }",
    cold_start: str = "5",
    calendar: str | None = CALENDAR,
    rules_section: bool = True,
    **lines: str,
) -> str:
    """A manifest that passes on its own; keyword arguments replace its lines."""
    entries = {rule: "adopted" for rule in musts(modules) if rule not in drop}
    entries.update(statuses or {})
    top = {
        "standard": 'standard = "1.0"',
        "title": 'title = "ab12"',
        "audience": 'audience = "general"',
        "age_rating": 'age_rating = "18+"',
        "simulated_gambling": "simulated_gambling = true",
        "locales": 'locales = ["zh-Hant", "en"]',
        "modules": "modules = [%s]" % ", ".join(json.dumps(module) for module in modules),
    }
    top.update(lines)
    body = [
        top["standard"],
        top["title"],
        top["audience"],
        top["age_rating"],
        top["simulated_gambling"],
        top["locales"],
        top["modules"],
        "",
        "[budgets]",
        f"first_download_mb = {first_download}",
        f"cold_start_s = {cold_start}",
        "",
    ]
    if calendar is not None:
        body += ["[paths]", f"calendar = {json.dumps(calendar)}", ""]
    if rules_section:
        body += ["[rules]", *(f"{json.dumps(rule)} = {json.dumps(value)}" for rule, value in entries.items()), ""]
    return "\n".join(body) + "\n"


def repo(manifest_text: str | None = None, **files: str | None) -> dict[str, str]:
    """A fixture title repository: the manifest, and the calendar it points at."""
    fixture = {
        "game-standard.toml": manifest_text if manifest_text is not None else manifest(),
        CALENDAR: event(),
    }
    fixture.update(files)  # a None value removes that file
    return {name: body for name, body in fixture.items() if body is not None}


def run(files: dict[str, str], args: tuple[str, ...] = (), summary: bool = False) -> subprocess.CompletedProcess:
    """Run the check against a fixture repository built from `files`."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        for name, body in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        command = [sys.executable, str(SCRIPT), str(root), "--today", TODAY, *args]
        env = {"GITHUB_STEP_SUMMARY": str(root / "summary.md")} if summary else None
        out = subprocess.run(command, capture_output=True, text=True, check=False, env=env)
        if summary:
            out.stdout += (root / "summary.md").read_text(encoding="utf-8")
        return out


class GameStandardTests(unittest.TestCase):
    def assertPasses(self, files: dict[str, str], args: tuple[str, ...] = ()) -> str:
        out = run(files, args)
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertEqual(out.stderr, "")
        return out.stdout

    def assertFails(self, files: dict[str, str], args: tuple[str, ...] = (), says: str | None = None) -> str:
        out = run(files, args)
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("::error file=", out.stdout)
        if says is not None:
            self.assertIn(says, out.stdout)
        return out.stdout

    def test_passing_title_repository(self) -> None:
        self.assertPasses(repo())

    def test_missing_and_unparseable_manifest_fail_closed(self) -> None:
        self.assertFails({}, says="the manifest is missing")
        self.assertFails({"game-standard.toml": "standard = \n"}, says="does not parse as TOML")

    def test_missing_must_status_fails(self) -> None:
        dropped = musts(("card-board",))[0]
        self.assertFails(repo(manifest(drop=(dropped,))), says=f"{dropped} has no status in [rules]")

    def test_must_of_an_undeclared_module_is_not_required(self) -> None:
        multiplayer_must = next(
            rule["id"] for rule in RULES["rules"] if rule["level"] == "MUST" and rule["module"] == "multiplayer"
        )
        self.assertPasses(repo(manifest(modules=(), drop=(multiplayer_must,))))
        self.assertFails(
            repo(manifest(modules=("multiplayer",), drop=(multiplayer_must,))),
            says=f"{multiplayer_must} has no status in [rules] (a multiplayer MUST)",
        )

    def test_unknown_rule_id_fails(self) -> None:
        self.assertFails(repo(manifest(statuses={"GS-NOPE-1": "adopted"})), says="GS-NOPE-1 is not a rule")

    def test_status_shapes(self) -> None:
        self.assertFails(repo(manifest(statuses={"GS-BND-1": "eventually"})), says="must be adopted")
        self.assertFails(repo(manifest(statuses={"GS-BND-1": "planned"})), says="planned needs the issue")
        self.assertFails(
            repo(manifest(statuses={"GS-BND-1": "planned http://github.com/x/1"})),
            says="planned needs the issue",
        )
        self.assertFails(repo(manifest(statuses={"GS-BND-1": "n/a"})), says="n/a needs a reason")
        self.assertPasses(repo(manifest(statuses={"GS-BND-1": "planned https://github.com/Cubeage/ab12/issues/1"})))
        self.assertPasses(repo(manifest(statuses={"GS-BND-1": "n/a no player trading"})))

    def test_waiver_dates(self) -> None:
        self.assertPasses(
            repo(manifest(statuses={"GS-BND-1": f"waived revived legacy build, no client release | {FUTURE}"})),
        )
        self.assertFails(
            repo(manifest(statuses={"GS-BND-1": f"waived revived legacy build | {PAST}"})),
            says="the waiver expired on 2026-01-01",
        )
        self.assertFails(
            repo(manifest(statuses={"GS-BND-1": "waived revived legacy build | 2027-13-45"})),
            says="not a real date",
        )
        self.assertFails(repo(manifest(statuses={"GS-BND-1": f"waived | {FUTURE}"})), says="waived needs a reason")
        self.assertFails(
            repo(manifest(statuses={"GS-BND-1": "waived revived legacy build"})),
            says="waived needs `waived <reason> | YYYY-MM-DD`",
        )

    def test_kids_audience_and_gambling(self) -> None:
        self.assertFails(
            repo(manifest(audience='audience = "kids"')),
            says='audience = "kids" declares the kids module',
        )
        self.assertFails(
            repo(manifest(modules=("kids",), simulated_gambling="simulated_gambling = true")),
            says="the kids module forbids simulated_gambling = true",
        )
        self.assertPasses(
            repo(
                manifest(
                    modules=("kids",),
                    audience='audience = "kids"',
                    simulated_gambling="simulated_gambling = false",
                )
            )
        )
        self.assertFails(repo(manifest(modules=("kids", "gadgets"))), says='module "gadgets" is not a module')
        self.assertFails(repo(manifest(modules=("core", "kids"))), says='modules must not list "core"')

    def test_budgets(self) -> None:
        self.assertFails(
            repo(manifest(first_download="{ android = 151 }")),
            says="first_download_mb.android is 151 MB, over the 150 MB budget",
        )
        self.assertFails(
            repo(manifest(first_download="{ web = 3.5 }")),
            says="first_download_mb.web is 3.5 MB, over the 3 MB budget",
        )
        self.assertFails(
            repo(manifest(first_download="{ android = 120, switch = 10 }")),
            says='"switch" is not a platform',
        )
        self.assertFails(repo(manifest(cold_start="6")), says="cold_start_s is 6 s, over the 5 s budget")
        self.assertPasses(repo(manifest(first_download="{ web = 3 }", cold_start="4.5")))

    def test_the_calendar_is_checked_once_gs_live_1_is_adopted(self) -> None:
        # Nothing to read when GS-LIVE-1 is not adopted, even with no [paths] and no file.
        self.assertPasses(
            repo(manifest(statuses={"GS-LIVE-1": "n/a no live-ops calendar yet"}, calendar=None), **{CALENDAR: None}),
        )
        self.assertFails(
            repo(manifest(calendar=None)),
            says="[paths] calendar is required once GS-LIVE-1 is adopted",
        )
        self.assertFails(repo(**{CALENDAR: None}), says="the calendar file is missing")
        self.assertFails(repo(**{CALENDAR: "[[event]\n"}), says="does not parse as TOML")
        self.assertFails(
            repo(**{CALENDAR: event(start="2026-12-01", end="2026-10-01")}),
            args=("--mode", "release"),
            says="end must be after start",
        )
        self.assertFails(
            repo(**{CALENDAR: "[[event]]\nstart = 2026-10-01\nend = 2027-03-01\n"}),
            says="needs a template",
        )
        self.assertFails(
            repo(**{CALENDAR: "[[event]]\ntemplate = 'x'\nstart = 2026-10-01\n"}),
            says="needs end as a TOML date",
        )
        self.assertPasses(repo(**{CALENDAR: event(template="festival", start="2026-10-01", end="2027-01-05")}))

    def test_the_calendar_window_warns_in_pr_and_fails_in_release(self) -> None:
        files = repo(**{CALENDAR: event(start="2026-09-01", end="2026-10-20")})
        warned = run(files)
        self.assertEqual(warned.returncode, 0, warned.stdout)
        self.assertIn("::warning file=", warned.stdout)
        self.assertFails(files, args=("--mode", "release"), says="the release gate needs the calendar to cover 56 days")

    def test_release_refuses_planned_musts(self) -> None:
        files = repo(manifest(statuses={"GS-LIVE-2": "planned https://github.com/Cubeage/ab12/issues/123"}))
        self.assertPasses(files)
        self.assertFails(
            files,
            args=("--mode", "release"),
            says="is only planned; a release needs every MUST adopted or waived",
        )

    def test_release_checks_the_artifact_size(self) -> None:
        files = repo(
            manifest(first_download="{ android = 1, ios = 120 }"),
            **{"build/app.apk": "x" * (2 * 1024 * 1024)},
        )
        self.assertPasses(
            files,
            args=("--mode", "release", "--artifact", "build/app.apk", "--artifact-platform", "ios"),
        )
        self.assertFails(
            files,
            args=("--mode", "release", "--artifact", "build/app.apk", "--artifact-platform", "android"),
            says="the artifact is 2.0 MB, over the declared first_download_mb.android of 1 MB",
        )
        self.assertFails(
            repo(manifest()),
            args=("--mode", "release", "--artifact", "build/app.apk", "--artifact-platform", "android"),
            says="the built artifact was not found",
        )
        self.assertFails(
            files,
            args=("--mode", "release", "--artifact", "build/app.apk"),
            says="--artifact needs --artifact-platform",
        )

    def test_keel_web_boot_module_budget(self) -> None:
        self.assertPasses(repo(**{"keel.toml": "[web.boot]\nmodule_kb = 3000\n"}))
        self.assertFails(
            repo(**{"keel.toml": "[web.boot]\nmodule_kb = 3001\n"}),
            says="[web.boot] module_kb is 3001 KB, over the 3000 KB web budget",
        )
        self.assertPasses(repo(**{"keel.toml": "[web]\nname = 'ab12'\n"}))

    def test_summary_table(self) -> None:
        files = repo(manifest(statuses={"GS-LIVE-2": "n/a not a live-ops title"}))
        out = run(files, summary=True)
        self.assertEqual(out.returncode, 0, out.stdout)
        summary = out.stdout[out.stdout.index("## game-standard:") :]
        self.assertIn("## game-standard: ab12 (`game-standard.toml`, mode pr)", summary)
        self.assertIn(f"| adopted | {len(musts(('card-board',))) - 1} |", summary)
        self.assertIn("| n/a | 1 |", summary)
        self.assertIn("Result: pass.", summary)


if __name__ == "__main__":
    unittest.main()
