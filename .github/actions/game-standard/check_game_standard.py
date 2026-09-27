#!/usr/bin/env python3
"""Check a title's game-standard.toml against the Cubeage Game Standard.

The manifest is the title's side of the standard: the genre modules it
declares, its declared budgets, and one status per MUST rule. This script
checks that declaration; it carries no business content of its own (rule ids,
levels and module names come from rules.json beside it), reads no network, and
uses only the Python standard library.

Mode `pr` checks:

  * `game-standard.toml` exists and parses as TOML;
  * `standard` is a known version (`1.2` is current; `1.1` and `1.0` are still
    accepted with a warning asking for `1.2`), `title` is a well-formed title ID,
    `audience` is general or kids, `modules` are known module names (`core` is
    implicit and must not be listed), `age_rating` is a string,
    `simulated_gambling` is a boolean, `locales` is a non-empty list;
  * every MUST rule of the core and of the declared modules has a status in
    `[rules]`, and no status names a rule that is not in the rule list;
  * each status parses: `adopted`; `planned <https URL>`; `n/a <reason>`;
    `waived <reason> | <YYYY-MM-DD>` (reason given, date not in the past);
  * a kids title declares the kids module, and the kids module is not combined
    with simulated gambling;
  * `[budgets]` declares a first download size per platform and a cold start,
    both within the standard's limits, and once GS-HAB-7 is `adopted` it
    declares `first_fun_s` between 0 and 30 seconds;
  * `[habit]` answers the habit rules the title adopted: two distinct
    `daily_reasons` once GS-HAB-2 is `adopted`, a `session_minutes` between 2
    and 5 once GS-HAB-10 is `adopted`, and an `auto_play` of `yes` or
    `takeover-only` once GS-HAB-11 is `adopted` (an `auto_play = "yes"` beside
    an `n/a` GS-HAB-11 is a warning);
  * when GS-LIVE-1 is `adopted`, `[paths] calendar` exists, parses, and every
    `[[event]]` has a template and an end after its start;
  * once GS-HAB-9 is `adopted`, each of the next 8 weeks has a calendar event
    (a warning in mode `pr`, an error in mode `release`);
  * a `keel.toml`, when present, keeps `[web.boot] module_kb` within the web
    budget;
  * `[platforms]` is a table; a repository holding a `keel.toml` (a Keel title)
    declares `web_build` - the directory of its web build's `keel.toml` -
    unless GS-WEB-1 is `n/a`; a `web_build`, whenever it is given, is a path
    inside the repository holding a `keel.toml` with a `[web]` table; and a
    `web_url`, whenever it is given, is a page on https://www.cubeage.com/.

Mode `release` adds: no MUST may be only `planned`; the calendar's last event
must end at least 56 days out and every one of the next 8 weeks must have an
event (both warnings in mode `pr`); a Keel title declares `web_url`, its page on
https://www.cubeage.com/; and the file given as `--artifact` must fit the
declared `first_download_mb` of its platform.

Usage:
  check_game_standard.py [ROOT] [--mode pr|release] [--manifest game-standard.toml]
      [--artifact PATH] [--artifact-platform android|ios|web]
      [--rules rules.json] [--today YYYY-MM-DD]

Findings print as GitHub annotations (`::error file=...::message`, `::warning`),
a summary table goes to $GITHUB_STEP_SUMMARY when set, and any error exits 1.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, timedelta

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_RULES = HERE / "rules.json"

CURRENT_STANDARD = "1.2"
KNOWN_STANDARDS = ("1.2", "1.1", "1.0")
SKIP_DIRS = {".git", "target", "node_modules", "vendor", "dist"}
TITLE_ID = re.compile(r"^[a-z][a-z0-9]{1,23}$")
HTTPS_URL = re.compile(r"^https://\S+$")
WEB_URL = re.compile(r"^https://www\.cubeage\.com/\S+$")
WAIVER_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

KINDS = ("adopted", "planned", "n/a", "waived")
STATUS_HELP = "adopted | planned <https URL> | n/a <reason> | waived <reason> | YYYY-MM-DD"
AUDIENCES = ("general", "kids")
IMPLICIT_MODULE = "core"
PLATFORMS = ("android", "ios", "web")
FIRST_DOWNLOAD_MB = {"android": 150.0, "ios": 150.0, "web": 3.0}
COLD_START_MAX_S = 5.0
FIRST_FUN_MAX_S = 30.0
WEB_BOOT_MODULE_KB_MAX = 3000
CALENDAR_DAYS = 56
CALENDAR_WEEKS = 8
HABIT_REASONS = ("daily_seed", "daily_shop", "timed_chest", "daily_quests", "check_in", "daily_event")
SESSION_MINUTES_MIN = 2
SESSION_MINUTES_MAX = 5
AUTO_PLAY = ("yes", "no", "takeover-only")
STATUS_LABELS = {"n/a": "n/a", "missing": "missing", "invalid": "invalid", "must": "MUST to answer"}


@dataclass(frozen=True)
class Finding:
    """One problem, on the file it was found in, as a GitHub annotation."""

    file: str
    message: str
    warning: bool = False


def empty_counts() -> dict[str, int]:
    """The status tally the summary table shows."""
    return {kind: 0 for kind in (*KINDS, "missing", "invalid", "must")}


def load_rules(path: pathlib.Path | str = DEFAULT_RULES) -> dict:
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def must_ids(rules: dict, modules: list[str]) -> list[str]:
    """Every MUST the title owes: the core, plus the modules it declares."""
    wanted = {IMPLICIT_MODULE, *modules}
    return sorted({rule["id"] for rule in rules["rules"] if rule["level"] == "MUST" and rule["module"] in wanted})


def parse_status(rule_id: str, value: object, today: date) -> list[str]:
    """Errors in one `[rules]` status; [] when it parses."""
    if not isinstance(value, str) or not value.strip():
        return [f"{rule_id}: a status is {STATUS_HELP}"]
    kind, _, rest = value.strip().partition(" ")
    kind = kind.lower()
    if kind not in KINDS:
        return [f"{rule_id}: status {value!r} must be {STATUS_HELP}"]
    if kind == "adopted":
        return [f"{rule_id}: adopted takes no reason"] if rest.strip() else []
    if kind == "planned":
        if not HTTPS_URL.match(rest.strip()):
            return [f"{rule_id}: planned needs the issue to do it, as an https:// link"]
        return []
    if kind == "n/a":
        return [] if rest.strip() else [f"{rule_id}: n/a needs a reason"]
    left, separator, right = value.strip().partition("|")
    if not separator:
        return [f"{rule_id}: waived needs `waived <reason> | YYYY-MM-DD`"]
    errors: list[str] = []
    reason = left.strip()[len(kind) :].strip()
    when = right.strip()
    if not reason:
        errors.append(f"{rule_id}: waived needs a reason")
    match = WAIVER_DATE.match(when)
    if not match:
        errors.append(f"{rule_id}: the waiver date must be YYYY-MM-DD (found {when!r})")
        return errors
    try:
        until = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        errors.append(f"{rule_id}: {when} is not a real date")
    else:
        if until < today:
            errors.append(f"{rule_id}: the waiver expired on {when}, before {today.isoformat()}")
    return errors


def status_kind(value: object) -> str:
    """The kind word of one status, lowercased; "" when it is not a string."""
    return value.strip().partition(" ")[0].lower() if isinstance(value, str) else ""


def adopted_ids(statuses: dict) -> set[str]:
    """The rule ids whose status is exactly `adopted`."""
    return {rule_id for rule_id, value in statuses.items() if status_kind(value) == "adopted"}


def is_toml_date(value: object) -> bool:
    return isinstance(value, (date, datetime))


def as_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def as_datetime(value: date | datetime) -> datetime:
    return value if isinstance(value, datetime) else datetime(value.year, value.month, value.day)


def ends_after(start: object, end: object) -> tuple[bool, str]:
    """Is `end` after `start`, and why not. A TOML date and datetime compare as dates."""
    for first, second in ((start, end), (as_datetime(start), as_datetime(end))):
        try:
            return second > first, ""
        except TypeError:
            continue
    return False, " (start and end use different date types)"


def event_findings(where: str, index: int, event: object) -> tuple[list[Finding], date | None]:
    """Findings for one [[event]], and the day it ends."""
    at = f"event {index}"
    if not isinstance(event, dict):
        return [Finding(where, f"{at} is not a table")], None
    findings: list[Finding] = []
    template = event.get("template")
    if not isinstance(template, str) or not template.strip():
        findings.append(Finding(where, f"{at} needs a template (the live-ops template it runs)"))
    start, end = event.get("start"), event.get("end")
    for key, value in (("start", start), ("end", end)):
        if not is_toml_date(value):
            findings.append(Finding(where, f"{at} needs {key} as a TOML date or datetime"))
    if is_toml_date(start) and is_toml_date(end):
        after, why = ends_after(start, end)
        if not after:
            findings.append(Finding(where, f"{at}: end must be after start{why}"))
        else:
            return findings, as_date(end)
    return findings, None


def load_calendar(path: pathlib.Path, where: str) -> tuple[list | None, list[Finding]]:
    """The `[[event]]` tables of a calendar file, and why it could not be read."""
    if not path.is_file():
        return None, [Finding(where, "the calendar file is missing")]
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as err:
        return None, [Finding(where, f"does not parse as TOML: {err}")]
    except (OSError, UnicodeDecodeError) as err:
        return None, [Finding(where, f"cannot be read: {err}")]
    events = data.get("event", [])
    if not isinstance(events, list):
        return None, [Finding(where, "[[event]] must be an array of tables")]
    return events, []


def event_spans(events: list) -> list[tuple[date, date]]:
    """The date span of every [[event]] that has one."""
    spans: list[tuple[date, date]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        start, end = event.get("start"), event.get("end")
        if is_toml_date(start) and is_toml_date(end):
            spans.append((as_date(start), as_date(end)))
    return spans


def check_calendar_weeks(events: list, where: str, mode: str, today: date) -> list[Finding]:
    """GS-HAB-9: something new every week - each of the next 8 weeks has an event."""
    spans = event_spans(events)
    findings: list[Finding] = []
    for week in range(CALENDAR_WEEKS):
        start = today + timedelta(days=week * 7)
        end = start + timedelta(days=7)
        if not any(event_start < end and event_end > start for event_start, event_end in spans):
            findings.append(
                Finding(
                    where,
                    f"no event covers the week starting {start.isoformat()} (GS-HAB-9: something new "
                    "every week)",
                    warning=mode != "release",
                ),
            )
    return findings


def check_calendar(events: list, where: str, mode: str, today: date) -> list[Finding]:
    """GS-LIVE-1: the calendar's events and how far past today they run."""
    findings: list[Finding] = []
    ends: list[date] = []
    for index, event in enumerate(events, start=1):
        found, end = event_findings(where, index, event)
        findings.extend(found)
        if end is not None:
            ends.append(end)
    horizon = today + timedelta(days=CALENDAR_DAYS)
    if not ends:
        findings.append(
            Finding(where, "the calendar has no event that runs", warning=mode != "release"),
        )
    elif max(ends) < horizon:
        findings.append(
            Finding(
                where,
                f"the last event ends {max(ends).isoformat()}; the release gate needs the calendar "
                f"to cover {CALENDAR_DAYS} days, to {horizon.isoformat()}",
                warning=mode != "release",
            ),
        )
    return findings


def check_keel(path: pathlib.Path, where: str = "keel.toml") -> list[Finding]:
    if not path.is_file():
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as err:
        return [Finding(where, f"does not parse as TOML: {err}")]
    except (OSError, UnicodeDecodeError) as err:
        return [Finding(where, f"cannot be read: {err}")]
    boot = data.get("web") or {}
    boot = boot.get("boot") or {} if isinstance(boot, dict) else {}
    module_kb = boot.get("module_kb") if isinstance(boot, dict) else None
    if module_kb is None:
        return []
    if isinstance(module_kb, bool) or not isinstance(module_kb, (int, float)):
        return [Finding(where, "[web.boot] module_kb must be a number of KB")]
    if module_kb > WEB_BOOT_MODULE_KB_MAX:
        return [
            Finding(
                where,
                f"[web.boot] module_kb is {module_kb:g} KB, over the {WEB_BOOT_MODULE_KB_MAX} KB web budget",
            ),
        ]
    return []


def check_platforms(
    root: pathlib.Path,
    data: dict,
    where: str,
    mode: str,
    keel_title: bool,
    statuses: dict,
) -> list[Finding]:
    """GS-WEB-1: the web build [platforms] declares, and the page that plays it."""
    findings: list[Finding] = []
    platforms = data.get("platforms")
    if platforms is None:
        platforms = {}
    elif not isinstance(platforms, dict):
        return [Finding(where, "[platforms] must be a table")]

    # A legacy client answers GS-WEB-1 with `n/a` and ships no web build.
    exempt = status_kind(statuses.get("GS-WEB-1")) == "n/a"
    web_build = platforms.get("web_build")
    if web_build is None:
        if keel_title and not exempt:
            findings.append(
                Finding(
                    where,
                    "[platforms] web_build is required in a Keel title (GS-WEB-1): the directory of the "
                    "web build's keel.toml",
                ),
            )
    else:
        findings.extend(check_web_build(root, web_build, where))

    web_url = platforms.get("web_url")
    if web_url is not None:
        if not isinstance(web_url, str) or not WEB_URL.match(web_url):
            findings.append(
                Finding(where, f"web_url must be a page on https://www.cubeage.com/ (found {web_url!r})"),
            )
    elif mode == "release" and keel_title and not exempt:
        findings.append(
            Finding(
                where,
                "[platforms] web_url is required for a release of a Keel title (GS-WEB-1): its page on "
                "https://www.cubeage.com/",
            ),
        )
    return findings


def check_web_build(root: pathlib.Path, web_build: object, where: str) -> list[Finding]:
    """The directory a title names as its web build: the web keel.toml lives there."""
    if not isinstance(web_build, str) or not web_build.strip():
        return [
            Finding(
                where,
                "[platforms] web_build must be a non-empty string: the directory of the web build's keel.toml",
            ),
        ]
    directory = pathlib.Path(web_build)
    if directory.is_absolute():
        return [
            Finding(where, f"[platforms] web_build must be relative to the repository root (found {web_build!r})"),
        ]
    inside = root.resolve()
    resolved = (root / directory).resolve()
    if resolved != inside and inside not in resolved.parents:
        return [Finding(where, f"[platforms] web_build must stay inside the repository (found {web_build!r})")]
    if not resolved.is_dir():
        return [Finding(where, f"[platforms] web_build {web_build!r} is not a directory in the repository")]
    keel = resolved / "keel.toml"
    if not keel.is_file():
        return [Finding(where, f"[platforms] web_build {web_build!r} holds no keel.toml")]
    try:
        parsed = tomllib.loads(keel.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as err:
        return [Finding(where, f"[platforms] web_build {web_build!r} does not parse as TOML: {err}")]
    except (OSError, UnicodeDecodeError) as err:
        return [Finding(where, f"[platforms] web_build {web_build!r} cannot be read: {err}")]
    if not isinstance(parsed.get("web"), dict):
        return [Finding(where, f"[platforms] web_build {web_build!r} keel.toml has no [web] table")]
    return []


def check_manifest(
    data: dict,
    rules: dict,
    where: str,
    mode: str,
    today: date,
) -> tuple[list[Finding], dict[str, int]]:
    """Every manifest-level check, without the files the manifest points at."""
    findings: list[Finding] = []
    counts = empty_counts()

    standard = data.get("standard")
    if standard not in KNOWN_STANDARDS:
        findings.append(
            Finding(where, f'standard must be {", ".join(repr(s) for s in KNOWN_STANDARDS)} (found {standard!r})'),
        )
    elif standard != CURRENT_STANDARD:
        findings.append(
            Finding(
                where,
                f'standard {standard!r} is superseded; move the manifest to standard = "{CURRENT_STANDARD}"',
                warning=True,
            ),
        )

    title = data.get("title")
    if not isinstance(title, str) or not TITLE_ID.match(title):
        findings.append(Finding(where, f"title must match {TITLE_ID.pattern} (found {title!r})"))

    audience = data.get("audience")
    if audience not in AUDIENCES:
        findings.append(Finding(where, f'audience must be "general" or "kids" (found {audience!r})'))

    known_modules = list(rules["modules"])
    declared: list[str] = []
    modules = data.get("modules")
    if not isinstance(modules, list) or not all(isinstance(module, str) for module in modules):
        findings.append(Finding(where, "modules must be a list of module names"))
    else:
        for module in modules:
            if module == IMPLICIT_MODULE:
                findings.append(Finding(where, f'modules must not list "{IMPLICIT_MODULE}": core is implicit'))
            elif module not in known_modules:
                findings.append(Finding(where, f'module "{module}" is not a module ({", ".join(known_modules)})'))
            elif module in declared:
                findings.append(Finding(where, f'module "{module}" is listed twice'))
            else:
                declared.append(module)

    age_rating = data.get("age_rating")
    if not isinstance(age_rating, str) or not age_rating.strip():
        findings.append(Finding(where, "age_rating must be the strictest store rating the title answers"))

    gambling = data.get("simulated_gambling")
    if not isinstance(gambling, bool):
        findings.append(Finding(where, "simulated_gambling must be true or false"))

    locales = data.get("locales")
    if (
        not isinstance(locales, list)
        or not locales
        or not all(isinstance(locale, str) and locale.strip() for locale in locales)
    ):
        findings.append(Finding(where, "locales must be a non-empty list of the shipped locales"))

    if audience == "kids" and "kids" not in declared:
        findings.append(Finding(where, 'audience = "kids" declares the kids module'))
    if "kids" in declared and gambling is True:
        findings.append(Finding(where, "the kids module forbids simulated_gambling = true"))

    by_id = {rule["id"]: rule for rule in rules["rules"]}
    statuses = data.get("rules")
    if not isinstance(statuses, dict):
        findings.append(Finding(where, "[rules] gives one status per MUST rule"))
        statuses = {}
    for rule_id, value in statuses.items():
        rule = by_id.get(rule_id)
        if rule is None:
            findings.append(Finding(where, f"{rule_id} is not a rule in the standard's rule list"))
            continue
        errors = parse_status(rule_id, value, today)
        if errors:
            counts["invalid"] += 1
            findings.extend(Finding(where, error) for error in errors)
            continue
        counts[str(value).strip().partition(" ")[0].lower()] += 1
        if rule["level"] == "MUST" and mode == "release" and str(value).strip().lower().startswith("planned"):
            findings.append(
                Finding(where, f"{rule_id} is only planned; a release needs every MUST adopted or waived"),
            )

    adopted = adopted_ids(statuses)
    findings.extend(check_budgets(data.get("budgets"), where, adopted))
    findings.extend(check_habit(data.get("habit"), where, adopted, statuses))

    required = must_ids(rules, declared)
    counts["must"] = len(required)
    for rule_id in required:
        if rule_id not in statuses:
            counts["missing"] += 1
            findings.append(Finding(where, f"{rule_id} has no status in [rules] (a {by_id[rule_id]['module']} MUST)"))

    return findings, counts


def check_budgets(budgets: object, where: str, adopted: set[str]) -> list[Finding]:
    findings: list[Finding] = []
    if not isinstance(budgets, dict):
        return [Finding(where, "[budgets] declares first_download_mb per platform, cold_start_s and first_fun_s")]
    first = budgets.get("first_download_mb")
    if not isinstance(first, dict) or not first:
        findings.append(
            Finding(where, "[budgets] first_download_mb declares a size per platform, e.g. { android = 120 }"),
        )
    else:
        for platform, megabytes in first.items():
            limit = FIRST_DOWNLOAD_MB.get(platform)
            if limit is None:
                findings.append(
                    Finding(where, f'first_download_mb "{platform}" is not a platform ({", ".join(PLATFORMS)})'),
                )
            elif isinstance(megabytes, bool) or not isinstance(megabytes, (int, float)):
                findings.append(Finding(where, f"first_download_mb.{platform} must be a number of MB"))
            elif megabytes > limit:
                findings.append(
                    Finding(where, f"first_download_mb.{platform} is {megabytes:g} MB, over the {limit:g} MB budget"),
                )
    cold = budgets.get("cold_start_s")
    if isinstance(cold, bool) or not isinstance(cold, (int, float)):
        findings.append(Finding(where, "cold_start_s must be a number of seconds"))
    elif cold > COLD_START_MAX_S:
        findings.append(Finding(where, f"cold_start_s is {cold:g} s, over the {COLD_START_MAX_S:g} s budget"))
    first_fun = budgets.get("first_fun_s")
    if first_fun is None:
        if "GS-HAB-7" in adopted:
            findings.append(
                Finding(
                    where,
                    "[budgets] first_fun_s is required once GS-HAB-7 is adopted: the seconds to the "
                    "first fun (a meaningful choice with feedback), at most 30",
                ),
            )
    elif isinstance(first_fun, bool) or not isinstance(first_fun, (int, float)):
        findings.append(Finding(where, "first_fun_s must be a number of seconds"))
    elif first_fun > FIRST_FUN_MAX_S or first_fun <= 0:
        findings.append(
            Finding(
                where,
                f"first_fun_s is {first_fun:g} s; GS-HAB-7 needs it over 0 and at most {FIRST_FUN_MAX_S:g} s",
            ),
        )
    return findings


def check_habit(habit: object, where: str, adopted: set[str], statuses: dict) -> list[Finding]:
    """[habit]: the daily reasons, the session length and the auto-play decision."""
    findings: list[Finding] = []
    table = habit if isinstance(habit, dict) else {}
    if habit is not None and not isinstance(habit, dict):
        findings.append(Finding(where, "[habit] must be a table"))

    reasons = table.get("daily_reasons")
    if reasons is None:
        if "GS-HAB-2" in adopted:
            findings.append(
                Finding(
                    where,
                    "[habit] daily_reasons is required once GS-HAB-2 is adopted: at least two daily "
                    "reasons to open",
                ),
            )
    elif not isinstance(reasons, list) or not all(isinstance(reason, str) and reason.strip() for reason in reasons):
        findings.append(
            Finding(where, f"daily_reasons must be a list of daily reasons ({', '.join(HABIT_REASONS)})"),
        )
    else:
        seen: set[str] = set()
        for reason in reasons:
            if reason not in HABIT_REASONS:
                findings.append(
                    Finding(where, f'daily_reasons "{reason}" is not a daily reason ({", ".join(HABIT_REASONS)})'),
                )
            elif reason in seen:
                findings.append(Finding(where, f'daily_reasons lists "{reason}" twice'))
            seen.add(reason)
        if "GS-HAB-2" in adopted and len(seen) < 2:
            findings.append(
                Finding(where, "daily_reasons names fewer than the two daily reasons GS-HAB-2 needs"),
            )

    minutes = table.get("session_minutes")
    if minutes is None:
        if "GS-HAB-10" in adopted:
            findings.append(
                Finding(
                    where,
                    "[habit] session_minutes is required once GS-HAB-10 is adopted: the core unit of "
                    "play, 2 to 5 minutes",
                ),
            )
    elif isinstance(minutes, bool) or not isinstance(minutes, (int, float)):
        findings.append(Finding(where, "session_minutes must be a number of minutes"))
    elif not SESSION_MINUTES_MIN <= minutes <= SESSION_MINUTES_MAX:
        findings.append(
            Finding(
                where,
                f"session_minutes is {minutes:g}; GS-HAB-10 keeps the core unit of play between "
                f"{SESSION_MINUTES_MIN} and {SESSION_MINUTES_MAX} minutes",
            ),
        )

    auto_play = table.get("auto_play")
    if auto_play is None:
        if "GS-HAB-11" in adopted:
            findings.append(
                Finding(
                    where,
                    '[habit] auto_play is required once GS-HAB-11 is adopted: "yes", "no" or '
                    '"takeover-only"',
                ),
            )
    elif not isinstance(auto_play, str) or auto_play not in AUTO_PLAY:
        findings.append(
            Finding(where, f'auto_play must be "yes", "no" or "takeover-only" (found {auto_play!r})'),
        )
    else:
        if "GS-HAB-11" in adopted and auto_play == "no":
            findings.append(
                Finding(
                    where,
                    'auto_play = "no" beside an adopted GS-HAB-11; its genre needs "yes" or '
                    '"takeover-only", or the rule marked `n/a <reason>`',
                ),
            )
        if status_kind(statuses.get("GS-HAB-11")) == "n/a" and auto_play == "yes":
            findings.append(
                Finding(
                    where,
                    'auto_play = "yes" while GS-HAB-11 is n/a; adopt the rule or set auto_play = "no"',
                    warning=True,
                ),
            )
    return findings


def check(
    root: pathlib.Path | str,
    rules: dict,
    mode: str = "pr",
    manifest: str = "game-standard.toml",
    artifact: str | None = None,
    artifact_platform: str | None = None,
    today: date | None = None,
) -> tuple[list[Finding], dict[str, int]]:
    """Every finding for one title repository, and the status counts for the summary."""
    today = today or date.today()
    root = pathlib.Path(root)
    counts = empty_counts()
    manifest_path = root / manifest
    if not manifest_path.is_file():
        return [
            Finding(manifest, "the manifest is missing: a title repository has game-standard.toml at its root"),
        ], counts
    try:
        data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as err:
        return [Finding(manifest, f"does not parse as TOML: {err}")], counts
    except (OSError, UnicodeDecodeError) as err:
        return [Finding(manifest, f"cannot be read: {err}")], counts

    findings, counts = check_manifest(data, rules, manifest, mode, today)
    keel_files = [
        keel
        for keel in sorted(root.glob("**/keel.toml"))
        if not any(part in SKIP_DIRS for part in keel.relative_to(root).parts)
    ]
    for keel in keel_files:
        findings.extend(check_keel(keel, str(keel.relative_to(root))))

    statuses = data.get("rules") if isinstance(data.get("rules"), dict) else {}
    adopted = adopted_ids(statuses)
    findings.extend(check_platforms(root, data, manifest, mode, bool(keel_files), statuses))
    # GS-LIVE-1 reads the calendar's events; GS-HAB-9 reads the weeks they cover.
    wants_calendar = [rule for rule in ("GS-LIVE-1", "GS-HAB-9") if rule in adopted]
    if wants_calendar:
        paths = data.get("paths")
        calendar = paths.get("calendar") if isinstance(paths, dict) else None
        if not isinstance(calendar, str) or not calendar.strip():
            needed = " and ".join(wants_calendar)
            findings.append(
                Finding(
                    manifest,
                    f"[paths] calendar is required once {needed} {'are' if len(wants_calendar) > 1 else 'is'} adopted",
                ),
            )
        else:
            events, load_findings = load_calendar(root / calendar, calendar)
            findings.extend(load_findings)
            if events is not None:
                if "GS-LIVE-1" in adopted:
                    findings.extend(check_calendar(events, calendar, mode, today))
                if "GS-HAB-9" in adopted:
                    findings.extend(check_calendar_weeks(events, calendar, mode, today))

    if mode == "release" and artifact:
        findings.extend(check_artifact(root, data, artifact, artifact_platform))

    return findings, counts


def check_artifact(
    root: pathlib.Path,
    data: dict,
    artifact: str,
    artifact_platform: str | None,
) -> list[Finding]:
    if not artifact_platform:
        return [Finding(artifact, "--artifact needs --artifact-platform (android, ios or web)")]
    path = pathlib.Path(artifact)
    if not path.is_absolute():
        path = root / path
    if not path.is_file():
        return [Finding(artifact, "the built artifact was not found")]
    budgets = data.get("budgets") if isinstance(data.get("budgets"), dict) else {}
    first = budgets.get("first_download_mb") if isinstance(budgets.get("first_download_mb"), dict) else {}
    declared = first.get(artifact_platform)
    if isinstance(declared, bool) or not isinstance(declared, (int, float)):
        return [
            Finding(
                artifact,
                f"the manifest declares no first_download_mb.{artifact_platform} to check the artifact against",
            ),
        ]
    megabytes = path.stat().st_size / (1024 * 1024)
    if megabytes > declared:
        return [
            Finding(
                artifact,
                f"the artifact is {megabytes:.1f} MB, over the declared first_download_mb.{artifact_platform} "
                f"of {declared:g} MB",
            ),
        ]
    return []


def manifest_title(path: pathlib.Path) -> str:
    try:
        return str(tomllib.loads(path.read_text(encoding="utf-8")).get("title", path.name))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError):
        return path.name


def plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def render_summary(manifest: str, title: str, mode: str, counts: dict[str, int], findings: list[Finding]) -> str:
    errors = sum(1 for finding in findings if not finding.warning)
    warnings = len(findings) - errors
    rows = ["| status | rules |", "| --- | --- |"]
    for kind in (*KINDS, "missing", "invalid", "must"):
        if kind == "invalid" and not counts.get(kind):
            continue
        rows.append(f"| {STATUS_LABELS.get(kind, kind)} | {counts.get(kind, 0)} |")
    if errors:
        result = f"fail ({plural(errors, 'error')}, {plural(warnings, 'warning')})"
    else:
        result = "pass"
    return "\n".join(
        [
            f"## game-standard: {title} (`{manifest}`, mode {mode})",
            "",
            *rows,
            "",
            f"Result: {result}.",
            "",
        ],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check game-standard.toml against the Cubeage Game Standard.")
    parser.add_argument("root", nargs="?", default=".", help="the title repository (default: .)")
    parser.add_argument("--mode", choices=("pr", "release"), default="pr")
    parser.add_argument("--manifest", default="game-standard.toml", help="manifest path, relative to ROOT")
    parser.add_argument("--artifact", help="built release artifact, checked against its declared budget")
    parser.add_argument("--artifact-platform", choices=PLATFORMS, help="the platform --artifact is for")
    parser.add_argument("--rules", default=str(DEFAULT_RULES), help="the rule list (default: rules.json beside it)")
    parser.add_argument("--today", help="today's date, YYYY-MM-DD (default: the real date)")
    args = parser.parse_args(argv)

    today = date.today()
    if args.today:
        try:
            today = date.fromisoformat(args.today)
        except ValueError:
            print(f"::error::--today {args.today!r} is not a YYYY-MM-DD date")
            return 2

    root = pathlib.Path(args.root)
    findings, counts = check(
        root,
        load_rules(args.rules),
        mode=args.mode,
        manifest=args.manifest,
        artifact=args.artifact,
        artifact_platform=args.artifact_platform,
        today=today,
    )
    for finding in findings:
        level = "warning" if finding.warning else "error"
        print(f"::{level} file={finding.file}::{finding.message}")

    summary = render_summary(
        args.manifest,
        manifest_title(root / args.manifest),
        args.mode,
        counts,
        findings,
    )
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(summary)

    errors = sum(1 for finding in findings if not finding.warning)
    if errors:
        print(f"{plural(errors, 'problem')} in {args.manifest}; the annotations above say where.")
        return 1
    print(f"{args.manifest} is in step with the standard: {counts['must']} MUST rules answered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
