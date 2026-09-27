# game-standard

Checks a title repository's `game-standard.toml` against the Cubeage Game
Standard. It carries no business content of its own: the rule ids, levels and
module names come from `rules.json` beside it, and the checker reads nothing
but the repository it is pointed at.

## What a title declares

```toml
standard = "1.1"             # the current version; "1.0" is accepted with a warning
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

## Modes

`mode: pr` (the default) fails when the manifest is missing or does not parse,
a declared value is out of range, a MUST of the core or of a declared module
has no status or a malformed one, a status names a rule that is not in
`rules.json`, a kids title breaks the kids module's rules, a budget is over the
standard's limit, a required habit field is missing or out of range, the
calendar is missing or malformed once GS-LIVE-1 or GS-HAB-9 is adopted, or a
`keel.toml` `[web.boot] module_kb` is over the web budget. Warnings, not
errors: a manifest still on `standard = "1.0"`, a calendar that runs out inside
the next 56 days, a week of the next 8 with no event, and `auto_play = "yes"`
beside an `n/a` GS-HAB-11.

`mode: release` adds: no MUST may be only `planned`; the calendar must reach at
least 56 days out and cover each of the next 8 weeks; and `artifact` must fit
the `first_download_mb` its `artifact-platform` declares.

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
