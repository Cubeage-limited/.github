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

sys.path.insert(0, str(ACTION))
import check_game_standard  # noqa: E402  (the script under test, for its pure helpers)

TODAY = "2026-09-27"  # every fixture date is read against this day
FUTURE = "2027-03-31"
PAST = "2026-01-01"
CALENDAR = "liveops/calendar.toml"
WEB_BUILD = "crates/ab12-web"  # the fixture title's web build, as [platforms] web_build names it
WEB_FILES = {f"{WEB_BUILD}/keel.toml": "[web.boot]\nmodule_kb = 2000\n"}


def musts(modules: tuple[str, ...]) -> list[str]:
    """Every MUST rule a title declaring these modules owes."""
    wanted = {"core", *modules}
    return sorted({rule["id"] for rule in RULES["rules"] if rule["level"] == "MUST" and rule["module"] in wanted})


def event(template: str = "weekend-ladder", start: str = "2026-09-01", end: str = "2027-03-01") -> str:
    """One [[event]]; by default it covers TODAY and the next 8 weeks."""
    return f'[[event]]\ntemplate = "{template}"\nstart = {start}\nend = {end}\n'


def manifest(
    modules: tuple[str, ...] = ("card-board",),
    statuses: dict[str, str] | None = None,
    drop: tuple[str, ...] = (),
    first_download: str = "{ android = 120, ios = 120, web = 3 }",
    cold_start: str = "5",
    first_fun: str | None = "30",
    daily_reasons: str | None = '["daily_seed", "check_in"]',
    session_minutes: str | None = "3",
    auto_play: str | None = '"yes"',
    habit: bool = True,
    calendar: str | None = CALENDAR,
    web_build: str | None = None,
    web_url: str | None = None,
    rules_section: bool = True,
    **lines: str,
) -> str:
    """A manifest that passes on its own; keyword arguments replace its lines.

    `None` drops one line, and `habit=False` drops the whole `[habit]` table.
    `web_build` and `web_url` are raw TOML values, and `[platforms]` is written
    only when one of the two is given; any other keyword adds that top-level
    line as it is written.
    """
    entries = {rule: "adopted" for rule in musts(modules) if rule not in drop}
    entries.update(statuses or {})
    top = {
        "standard": f'standard = "{RULES["standard"]}"',
        "title": 'title = "ab12"',
        "audience": 'audience = "general"',
        "age_rating": 'age_rating = "18+"',
        "simulated_gambling": "simulated_gambling = true",
        "locales": 'locales = ["zh-Hant", "en"]',
        "modules": "modules = [%s]" % ", ".join(json.dumps(module) for module in modules),
    }
    top.update(lines)
    budgets = [
        "[budgets]",
        f"first_download_mb = {first_download}",
        f"cold_start_s = {cold_start}",
    ]
    if first_fun is not None:
        budgets.append(f"first_fun_s = {first_fun}")
    body = [
        *top.values(),
        "",
        *budgets,
        "",
    ]
    if habit:
        table = ["[habit]"]
        if daily_reasons is not None:
            table.append(f"daily_reasons = {daily_reasons}")
        if session_minutes is not None:
            table.append(f"session_minutes = {session_minutes}")
        if auto_play is not None:
            table.append(f"auto_play = {auto_play}")
        body += [*table, ""]
    if web_build is not None or web_url is not None:
        table = ["[platforms]"]
        if web_build is not None:
            table.append(f"web_build = {web_build}")
        if web_url is not None:
            table.append(f"web_url = {web_url}")
        body += [*table, ""]
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

    def test_standard_version(self) -> None:
        self.assertPasses(repo())
        current = RULES["standard"]
        known = check_game_standard.known_standards(current)
        self.assertEqual(known[0], current)
        self.assertEqual(known[-1], "1.0")
        listed = ", ".join(repr(version) for version in known)
        for old in known[1:]:
            out = run(repo(manifest(standard=f'standard = "{old}"')))
            self.assertEqual(out.returncode, 0, out.stdout)
            self.assertIn(
                f"::warning file=game-standard.toml::standard '{old}' is superseded; "
                f'move the manifest to standard = "{current}"',
                out.stdout,
            )
        self.assertFails(repo(manifest(standard='standard = "0.9"')), says=f"standard must be {listed}")
        self.assertFails(repo(manifest(standard="standard = 1.1")), says=f"standard must be {listed}")

    def test_known_standards(self) -> None:
        self.assertEqual(check_game_standard.known_standards("1.3"), ("1.3", "1.2", "1.1", "1.0"))
        self.assertEqual(check_game_standard.known_standards("1.0"), ("1.0",))

    def test_first_fun_budget(self) -> None:
        self.assertPasses(repo(manifest(first_fun="12")))
        self.assertPasses(repo(manifest(first_fun="30")))
        self.assertFails(
            repo(manifest(first_fun="31")),
            says="first_fun_s is 31 s; GS-HAB-7 needs it over 0 and at most 30 s",
        )
        self.assertFails(repo(manifest(first_fun="0")), says="first_fun_s is 0 s")
        self.assertFails(repo(manifest(first_fun="-5")), says="first_fun_s is -5 s")
        self.assertFails(repo(manifest(first_fun='"30"')), says="first_fun_s must be a number of seconds")
        # Required only while GS-HAB-7 is adopted.
        self.assertFails(
            repo(manifest(first_fun=None)),
            says="[budgets] first_fun_s is required once GS-HAB-7 is adopted",
        )
        self.assertPasses(
            repo(manifest(first_fun=None, statuses={"GS-HAB-7": "n/a the first fun arrives later in this genre"})),
        )
        # Range-checked whenever it is there, adopted or not.
        self.assertFails(
            repo(manifest(first_fun="31", statuses={"GS-HAB-7": "n/a the first fun arrives later"})),
            says="first_fun_s is 31 s",
        )

    def test_habit_daily_reasons(self) -> None:
        self.assertPasses(repo())
        self.assertPasses(repo(manifest(daily_reasons='["daily_seed", "daily_shop", "timed_chest"]')))
        self.assertFails(
            repo(manifest(daily_reasons='["daily_seed"]')),
            says="daily_reasons names fewer than the two daily reasons GS-HAB-2 needs",
        )
        self.assertFails(repo(manifest(daily_reasons="[]")), says="daily_reasons names fewer than the two")
        self.assertFails(
            repo(manifest(daily_reasons='["daily_seed", "daily_seed"]')),
            says='daily_reasons lists "daily_seed" twice',
        )
        self.assertFails(
            repo(manifest(daily_reasons='["daily_seed", "check_in", "check_in"]')),
            says='daily_reasons lists "check_in" twice',
        )
        self.assertFails(
            repo(manifest(daily_reasons='["daily_seed", "wheel"]')),
            says='daily_reasons "wheel" is not a daily reason',
        )
        self.assertFails(
            repo(manifest(daily_reasons='"daily_seed"')),
            says="daily_reasons must be a list of daily reasons",
        )
        self.assertFails(
            repo(manifest(daily_reasons=None)),
            says="[habit] daily_reasons is required once GS-HAB-2 is adopted",
        )
        self.assertFails(repo(manifest(habit=False)), says="daily_reasons is required")
        self.assertPasses(
            repo(manifest(daily_reasons=None, statuses={"GS-HAB-2": "n/a no daily reasons yet"})),
        )

    def test_habit_session_minutes(self) -> None:
        self.assertPasses(repo(manifest(session_minutes="2")))
        self.assertPasses(repo(manifest(session_minutes="5")))
        self.assertPasses(repo(manifest(session_minutes="3.5")))
        self.assertFails(
            repo(manifest(session_minutes="1")),
            says="session_minutes is 1; GS-HAB-10 keeps the core unit of play between 2 and 5 minutes",
        )
        self.assertFails(repo(manifest(session_minutes="6")), says="session_minutes is 6")
        self.assertFails(repo(manifest(session_minutes='"3"')), says="session_minutes must be a number of minutes")
        self.assertFails(
            repo(manifest(session_minutes=None)),
            says="[habit] session_minutes is required once GS-HAB-10 is adopted",
        )
        # Range-checked whenever it is there, adopted or not.
        self.assertFails(
            repo(manifest(session_minutes="1", statuses={"GS-HAB-10": "n/a the sessions here run long"})),
            says="session_minutes is 1",
        )
        self.assertPasses(
            repo(manifest(session_minutes=None, statuses={"GS-HAB-10": "waived revived legacy build | 2027-03-31"})),
        )

    def test_habit_auto_play(self) -> None:
        self.assertPasses(repo(manifest(auto_play='"yes"')))
        self.assertPasses(repo(manifest(auto_play='"takeover-only"')))
        self.assertFails(
            repo(manifest(auto_play='"no"')),
            says='auto_play = "no" beside an adopted GS-HAB-11',
        )
        self.assertFails(
            repo(manifest(auto_play=None)),
            says="[habit] auto_play is required once GS-HAB-11 is adopted",
        )
        self.assertFails(
            repo(manifest(auto_play='"maybe"')),
            says='auto_play must be "yes", "no" or "takeover-only"',
        )
        self.assertFails(repo(manifest(auto_play="true")), says='auto_play must be "yes", "no" or "takeover-only"')
        # A genre where GS-HAB-11 is n/a: "yes" warns, "no" is silent.
        warned = run(repo(manifest(auto_play='"yes"', statuses={"GS-HAB-11": "n/a solving is the fun"})))
        self.assertEqual(warned.returncode, 0, warned.stdout)
        self.assertIn(
            '::warning file=game-standard.toml::auto_play = "yes" while GS-HAB-11 is n/a; '
            'adopt the rule or set auto_play = "no"',
            warned.stdout,
        )
        quiet = self.assertPasses(repo(manifest(auto_play='"no"', statuses={"GS-HAB-11": "n/a solving is the fun"})))
        self.assertNotIn("::warning", quiet)

    def test_calendar_weeks(self) -> None:
        # The default fixture covers today and the next 8 weeks, so a release passes.
        self.assertPasses(repo(), args=("--mode", "release"))

        # One week only: the other seven weeks each warn in pr and error in release.
        one_week = event(start="2026-09-27", end="2026-10-04")
        warned = run(repo(**{CALENDAR: one_week}))
        self.assertEqual(warned.returncode, 0, warned.stdout)
        self.assertIn(
            "::warning file=liveops/calendar.toml::no event covers the week starting 2026-10-04",
            warned.stdout,
        )
        self.assertFails(
            repo(**{CALENDAR: one_week}),
            args=("--mode", "release"),
            says="no event covers the week starting 2026-10-04",
        )
        self.assertFails(
            repo(**{CALENDAR: one_week}),
            args=("--mode", "release"),
            # today + 7 x 7 days: the last of the 8 windows
            says="no event covers the week starting 2026-11-15",
        )
        # An event that abuts the next window without overlapping it leaves it empty.
        self.assertFails(
            repo(**{CALENDAR: event(start="2026-09-27", end="2026-10-04") + event(start="2026-10-04", end="2026-10-11")}),
            args=("--mode", "release"),
            says="no event covers the week starting 2026-10-11",
        )
        # A gap in the middle is named by its own week, and only that week.
        gapped = event(start="2026-09-01", end="2026-10-05") + event(start="2026-10-19", end="2027-03-01")
        out = self.assertFails(
            repo(**{CALENDAR: gapped}),
            args=("--mode", "release"),
            says="no event covers the week starting 2026-10-11",
        )
        self.assertEqual(out.count("no event covers the week starting"), 1)
        # No weekly check once GS-HAB-9 is n/a.
        skipped = self.assertPasses(repo(manifest(statuses={"GS-HAB-9": "n/a no weekly rotation yet"}), **{CALENDAR: one_week}))
        self.assertNotIn("no event covers the week starting", skipped)

    def test_the_calendar_path_is_required_by_gs_hab_9_too(self) -> None:
        self.assertFails(
            repo(
                manifest(statuses={"GS-LIVE-1": "n/a no live-ops calendar yet"}, calendar=None),
                **{CALENDAR: None},
            ),
            says="[paths] calendar is required once GS-HAB-9 is adopted",
        )
        self.assertFails(
            repo(manifest(calendar=None), **{CALENDAR: None}),
            says="[paths] calendar is required once GS-LIVE-1 and GS-HAB-9 are adopted",
        )
        self.assertFails(
            repo(manifest(statuses={"GS-LIVE-1": "n/a no live-ops calendar yet"}), **{CALENDAR: None}),
            says="the calendar file is missing",
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
        # Nothing to read when neither GS-LIVE-1 nor GS-HAB-9 is adopted, even with no [paths] and no file.
        self.assertPasses(
            repo(
                manifest(
                    statuses={
                        "GS-LIVE-1": "n/a no live-ops calendar yet",
                        "GS-HAB-9": "n/a nothing weekly to look forward to yet",
                    },
                    calendar=None,
                ),
                **{CALENDAR: None},
            ),
        )
        self.assertFails(
            repo(manifest(statuses={"GS-HAB-9": "n/a nothing weekly to look forward to yet"}, calendar=None)),
            says="[paths] calendar is required once GS-LIVE-1 is adopted",
        )
        out = self.assertFails(repo(**{CALENDAR: None}), says="the calendar file is missing")
        self.assertEqual(out.count("the calendar file is missing"), 1)
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
        # The repository's own keel.toml is where a web_build of "." points.
        self.assertPasses(repo(manifest(web_build='"."'), **{"keel.toml": "[web.boot]\nmodule_kb = 3000\n"}))
        self.assertFails(
            repo(manifest(web_build='"."'), **{"keel.toml": "[web.boot]\nmodule_kb = 3001\n"}),
            says="[web.boot] module_kb is 3001 KB, over the 3000 KB web budget",
        )
        self.assertPasses(repo(manifest(web_build='"."'), **{"keel.toml": "[web]\nname = 'ab12'\n"}))

    def test_nested_keel_toml_is_checked_and_build_dirs_are_skipped(self) -> None:
        self.assertFails(
            repo(
                manifest(web_build=f'"{WEB_BUILD}"'),
                **{f"{WEB_BUILD}/keel.toml": "[web.boot]\nmodule_kb = 3100\n"},
            ),
            says="[web.boot] module_kb is 3100 KB, over the 3000 KB web budget",
        )
        self.assertPasses(repo(**{"target/wasm/keel.toml": "[web.boot]\nmodule_kb = 9999\n"}))

    def test_keel_title_needs_a_web_build(self) -> None:
        keel = {"keel.toml": "[web]\nname = 'ab12'\n"}
        self.assertFails(
            repo(**keel),
            says="[platforms] web_build is required in a Keel title (GS-WEB-1): the directory of the web "
            "build's keel.toml",
        )
        # A legacy client answers GS-WEB-1 with n/a and ships no web build.
        self.assertPasses(
            repo(manifest(statuses={"GS-WEB-1": "n/a legacy client, replaced by https://example/1"}), **keel),
        )
        # No keel.toml outside the skipped directories: not a Keel title, so [platforms] is optional.
        self.assertPasses(repo())
        self.assertPasses(repo(**{"target/wasm/keel.toml": "[web.boot]\nmodule_kb = 2000\n"}))

    def test_web_build_holds_a_web_keel_toml(self) -> None:
        self.assertPasses(repo(manifest(web_build=f'"{WEB_BUILD}"'), **WEB_FILES))
        self.assertFails(
            repo(manifest(web_build=f'"{WEB_BUILD}"')),
            says=f"[platforms] web_build '{WEB_BUILD}' is not a directory in the repository",
        )
        # A relative path that stays inside the repository, `..` or not.
        self.assertPasses(repo(manifest(web_build='"./crates/ab12-web"'), **WEB_FILES))
        self.assertPasses(repo(manifest(web_build='"crates/../crates/ab12-web"'), **WEB_FILES))
        self.assertFails(
            repo(manifest(web_build=f'"{WEB_BUILD}"'), **{"crates/ab12-web/keel.toml": "[audio]\nname = 'x'\n"}),
            says=f"[platforms] web_build '{WEB_BUILD}' keel.toml has no [web] table",
        )
        self.assertFails(
            repo(manifest(web_build=f'"{WEB_BUILD}"'), **{"crates/ab12-web/keel.toml": "[[[\n"}),
            says="does not parse as TOML",
        )
        self.assertFails(
            repo(manifest(web_build=f'"{WEB_BUILD}"'), **{f"{WEB_BUILD}/README.md": "the web build\n"}),
            says=f"[platforms] web_build '{WEB_BUILD}' holds no keel.toml",
        )
        self.assertFails(
            repo(manifest(web_build='"/srv/ab12-web"'), **WEB_FILES),
            says="[platforms] web_build must be relative to the repository root",
        )
        self.assertFails(
            repo(manifest(web_build='"../ab12-web"'), **WEB_FILES),
            says="[platforms] web_build must stay inside the repository (found '../ab12-web')",
        )
        self.assertFails(
            repo(manifest(web_build="3"), **WEB_FILES),
            says="[platforms] web_build must be a non-empty string",
        )
        self.assertFails(
            repo(manifest(web_build='""'), **WEB_FILES),
            says="[platforms] web_build must be a non-empty string",
        )
        # Checked whenever it is given, even beside an n/a GS-WEB-1.
        self.assertFails(
            repo(manifest(web_build='"crates/nope"', statuses={"GS-WEB-1": "n/a legacy client"}), **WEB_FILES),
            says="[platforms] web_build 'crates/nope' is not a directory in the repository",
        )

    def test_platforms_must_be_a_table(self) -> None:
        self.assertFails(repo(manifest(platforms="platforms = 3")), says="[platforms] must be a table")
        self.assertPasses(
            repo(manifest(platforms='platforms = { web_url = "https://www.cubeage.com/en/games/ab12" }')),
        )

    def test_web_url_is_a_page_on_cubeage_com(self) -> None:
        self.assertPasses(repo(manifest(web_url='"https://www.cubeage.com/en/games/ab12"')))
        self.assertFails(
            repo(manifest(web_url='"https://example.com/ab12"')),
            says="web_url must be a page on https://www.cubeage.com/ (found 'https://example.com/ab12')",
        )
        self.assertFails(
            repo(manifest(web_url='"https://cubeage.com/en/games/ab12"')),
            says="web_url must be a page on https://www.cubeage.com/",
        )
        self.assertFails(
            repo(manifest(web_url='"http://www.cubeage.com/en/games/ab12"')),
            says="web_url must be a page on https://www.cubeage.com/",
        )
        self.assertFails(repo(manifest(web_url="3")), says="web_url must be a page on https://www.cubeage.com/")

    def test_a_release_of_a_keel_title_needs_its_web_url(self) -> None:
        self.assertPasses(repo(manifest(web_build=f'"{WEB_BUILD}"'), **WEB_FILES))
        self.assertFails(
            repo(manifest(web_build=f'"{WEB_BUILD}"'), **WEB_FILES),
            args=("--mode", "release"),
            says="[platforms] web_url is required for a release of a Keel title (GS-WEB-1): its page on "
            "https://www.cubeage.com/",
        )
        self.assertPasses(
            repo(
                manifest(web_build=f'"{WEB_BUILD}"', web_url='"https://www.cubeage.com/en/games/ab12"'),
                **WEB_FILES,
            ),
            args=("--mode", "release"),
        )
        # A legacy client is n/a on GS-WEB-1 and releases without a web page.
        self.assertPasses(
            repo(
                manifest(
                    web_build=f'"{WEB_BUILD}"',
                    statuses={"GS-WEB-1": "n/a legacy client, replaced by https://example/1"},
                ),
                **WEB_FILES,
            ),
            args=("--mode", "release"),
        )

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
