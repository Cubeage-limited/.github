# game-standard

Checks a title repository's `game-standard.toml` against the Cubeage Game
Standard. It carries no business content of its own: the rule ids, levels and
module names come from `rules.json` beside it, and the checker reads nothing
but the repository it is pointed at.

## What a title declares

```toml
standard = "1.5"             # the current version (rules.json `standard`); earlier 1.x versions warn
title = "ab12"               # ^[a-z][a-z0-9]{1,23}$
audience = "general"         # general | kids
modules = ["card-board"]     # genre modules; core is implicit and not listed
age_rating = "18+"           # the strictest store rating the title answers
simulated_gambling = true    # turns on the (SG) hard limits
locales = ["zh-Hant", "en"]  # shipped locales

[budgets]
first_download_mb = { android = 120, ios = 120, web = 3 }
cold_start_s = 5
first_fun_s = 30             # GS-HAB-7; required once GS-HAB-7 is adopted, 0 < s <= 30

[habit]                      # GS-HAB-2, GS-HAB-10, GS-HAB-11
daily_reasons = ["daily_seed", "check_in"]   # daily_seed | daily_shop | timed_chest
                                             # | daily_quests | check_in | daily_event
session_minutes = 3          # GS-HAB-10; 2 to 5 whenever it is given
auto_play = "yes"            # GS-HAB-11; yes | no | takeover-only

[platforms]                  # GS-WEB-1; web_build is required in every Keel title
web_build = "crates/ab12-web"        # the directory of the web keel.toml
web_url = "https://www.cubeage.com/en/games/ab12"   # its page; required to release

[paths]
calendar = "liveops/calendar.toml"   # required once GS-LIVE-1 or GS-HAB-9 is adopted

[rules]                      # one entry per MUST of the core and of the modules
"GS-BOT-1" = "adopted"
"GS-SUB-4" = "planned https://github.com/<owner>/<repo>/issues/123"
"GS-TRD-2" = "n/a no player trading"
"GS-START-1" = "waived revived legacy build, no client release | 2027-03-31"
```

Statuses: `adopted`; `planned <https URL>`; `n/a <reason>`; `waived <reason> |
<YYYY-MM-DD>`, the date not in the past.

## The habit fields

`[budgets] first_fun_s` and `[habit]` answer the section 17 rules, and each one
is required only while its rule is `adopted`:

| field | rule | when it is checked |
| --- | --- | --- |
| `[budgets] first_fun_s` | GS-HAB-7 | required once adopted; always over 0 and at most 30 |
| `[habit] daily_reasons` | GS-HAB-2 | required once adopted, at least two distinct values; every value one of the six reasons, no duplicates |
| `[habit] session_minutes` | GS-HAB-10 | required once adopted; 2 to 5 whenever it is given |
| `[habit] auto_play` | GS-HAB-11 | required once adopted, and then `yes` or `takeover-only`; `no` is for a genre marked `n/a` (an `auto_play = "yes"` beside an `n/a` GS-HAB-11 is a warning) |

`[habit] daily_reasons` values: `daily_seed`, `daily_shop`, `timed_chest`,
`daily_quests`, `check_in`, `daily_event`. `[habit] auto_play` values: `yes`,
`no`, `takeover-only`.

The calendar also answers GS-HAB-9: once GS-HAB-9 is adopted, each of the next
8 weeks (7-day windows from today) must be overlapped by an event with an
`end` after its `start`. An empty week is a warning in `mode: pr` and an error
in `mode: release`, and it is named by the date its week starts.

## The web build

A repository holding a `keel.toml` (outside `target`, `node_modules`, `vendor`,
`dist` and `.git`) is a Keel title, and GS-WEB-1 asks it for a web build:
`[platforms] web_build` names the directory holding the web build's own
`keel.toml` - a path inside the repository, with no `..` leaving it - and that
`keel.toml` must parse and carry a `[web]` table. A legacy client that answers
GS-WEB-1 with `n/a legacy client, replaced by <issue URL>` needs neither, and
neither does a release of one.

`[platforms] web_url` is the title's page on https://www.cubeage.com/, where
the web build plays. Checked whenever it is given, and required once a Keel
title releases.

## Modes

`mode: pr` (the default) fails when the manifest is missing or does not parse,
a declared value is out of range, a MUST of the core or of a declared module
has no status or a malformed one, a status names a rule that is not in
`rules.json`, a kids title breaks the kids module's rules, a budget is over the
standard's limit, a required habit field is missing or out of range, the
calendar is missing or malformed once GS-LIVE-1 or GS-HAB-9 is adopted, a
`keel.toml` `[web.boot] module_kb` is over the web budget, `[platforms]` is not
a table, a Keel title declares no `web_build`, or a `web_build` or `web_url` is
not as above. Warnings, not errors: a manifest on an earlier 1.x `standard`,
a calendar that runs out inside the next 56 days, a week of the next 8
with no event, and `auto_play = "yes"` beside an `n/a` GS-HAB-11.

`mode: release` adds: no MUST may be only `planned`; the calendar must reach at
least 56 days out and cover each of the next 8 weeks; a Keel title must declare
`[platforms] web_url`; and `artifact` must fit the `first_download_mb` its
`artifact-platform` declares.

The standard's version moves with its rules: `rules.json` beside the action
names the version it carries (`standard`), and a rule added or removed raises
that version by one minor. A change that only relaxes the standard, removing or
downgrading obligations without adding any, goes up by one patch instead
(`1.3` to `1.3.1`), and the versions it relaxes stay known. So does a rule
that only points at an owner standard already binding every product (`1.3.2`),
and an owner decision that narrows when an existing rule lets spend happen (`1.3.3`).
The action reads the current version from `rules.json`, so a new version
changes only that file. A
manifest on an earlier 1.x version, patch line included, warns and asks for the
current one; any other version fails.

`rules.json` is the one copy of the rule list. The standard's own repository
(Cubeage/cubeage-platform, private) pins a commit of this file in
`docs/standards/game-standard.rules.lock`, and its guard checks the standard's
text against that commit. A rule change lands here first (generate the file
with `bun scripts/check-game-standard-rules.ts --print` there), then the
standard and the lock move together.

Findings print as GitHub annotations, a status summary goes to the job
summary, and any error exits 1.

## Use it

Copy [`workflow-templates/game-standard.yml`](../../../workflow-templates/game-standard.yml)
into the title repository as `.github/workflows/game-standard.yml` and pin the
`uses:` line to a commit of this repository. A private repository runs it on
`runs-on: sylphx-linux-standard`; a public one may use `ubuntu-latest`.

A release workflow passes the built artifact instead:

```yaml
      - uses: Cubeage/.github/.github/actions/game-standard@<commit>
        with:
          mode: release
          artifact: build/app-release.apk
          artifact-platform: android
```

The checker needs Python 3.11 or newer (stdlib `tomllib`); the action looks for
`python3.13`, `python3.12`, `python3.11` and then `python3`, and fails with a
clear message when none has `tomllib`.

Test: `python3 -m unittest discover -s .github/actions/game-standard/test`.
