#!/usr/bin/env python3
"""Refuse a Firebase Test Lab run that would pass the project's daily budget.

Firebase Test Lab bills device time, and the project has a hard daily quota per
device kind (`blaze_physical_tests`, `blaze_virtual_tests`). This script is the
guard CI runs *before* `gcloud firebase test ... run`: it counts the Test Lab
steps the project has already created in the current Test Lab day, adds the
devices the step about to run will use, and fails the job when that would pass
the day's allowance. Spending needs the owner's approval, so the guard never
guesses in the run's favour.

How it counts, all through the Tool Results API (the API Test Lab itself files
its executions in):

  * `GET .../projects/{project}/histories`, then `.../histories/{id}/executions`,
    then `.../executions/{id}/steps` for every execution that could still hold a
    step from today;
  * a step counts when its own `creationTime` is at or after the current Test
    Lab day's start - midnight in America/Los_Angeles, which is the day the Test
    Lab quota resets on;
  * a step whose device is not recognisable counts as physical, and so does a
    step with no `creationTime` at all: both are the direction that refuses a
    run rather than allowing one;
  * the histories, and the executions' step lists, are independent reads and
    are fetched concurrently, so a project with hundreds of executions still
    answers in seconds rather than one call at a time;
  * the device kind comes from the step's `dimensionValue` device id
    (`MediumPhone.arm`, `Pixel2.x86`, `oriole`, ...) and from the testing API's
    device catalog (`form: VIRTUAL | EMULATOR | PHYSICAL`), which is looked up
    best-effort: when it cannot be read the id rule decides alone, and an
    unrecognised id is physical either way.

Everything that stops this script from *proving* the count - no access token, a
transport error, a timeout, an unparseable payload, a body that is not the
listing it should be - exits non-zero with an `::error::` annotation. A guard
that cannot count must not let a run through.

Usage:
  check_ftl_daily_budget.py --project PROJECT --kind physical|virtual
      --max-per-day N [--devices N]

The count is written to $GITHUB_STEP_SUMMARY when the runner sets it. Reads
only the Tool Results and testing APIs; uses only the Python standard library.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zoneinfo
from typing import Callable, Iterable, TypeVar

TOOL_RESULTS_BASE = "https://toolresults.googleapis.com/toolresults/v1beta3"
ANDROID_CATALOG_URL = "https://testing.googleapis.com/v1/testEnvironmentCatalog/ANDROID"

# The Test Lab quota day. Midnight in this zone is when the daily quota resets.
DAY_ZONE = "America/Los_Angeles"
# Emulator device ids: `MediumPhone.arm`, `MediumTablet.arm`, `Pixel2.arm`,
# `Pixel2.x86`, and the `.arm64` spellings of the same family.
VIRTUAL_ID_MARKERS = (".arm", ".x86")
# Device catalog `form` values that mean the run does not use a physical device.
VIRTUAL_FORMS = ("VIRTUAL", "EMULATOR")

REQUEST_TIMEOUT_S = 30.0
# Retries cover a single transient 5xx/429; anything else fails closed at once.
REQUEST_ATTEMPTS = 2
RETRY_BACKOFF_S = 2.0
# The whole count must finish inside this; a run of Test Lab steps is minutes,
# so an execution older than the lookback cannot still be filing a step today.
TOTAL_DEADLINE = datetime.timedelta(minutes=8)
EXECUTION_LOOKBACK = datetime.timedelta(hours=12)
PAGE_SIZE = 100
MAX_PAGES_PER_LISTING = 50
# The reads of different histories, and of different executions' steps, are
# independent; the API answers a call in about a second, so a project with a
# few hundred of them ran for minutes one call at a time.
CONCURRENCY = 16

DEVICE_DIMENSION_KEYS = ("Model",)
KINDS = ("physical", "virtual")

Item = TypeVar("Item")
Answer = TypeVar("Answer")


class BudgetError(RuntimeError):
    """The run is over budget, or the count could not be proven. Both stop it."""


def now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def timestamp(value: object) -> datetime.datetime | None:
    """The UTC instant of a protobuf Timestamp, or None when it is absent."""
    if not isinstance(value, dict):
        return None
    seconds = value.get("seconds")
    if seconds is None:
        return None
    try:
        return datetime.datetime.fromtimestamp(int(seconds), datetime.timezone.utc)
    except (TypeError, ValueError) as exc:
        raise BudgetError(f"a creationTime is not a timestamp: {value!r}") from exc


def day_start(now: datetime.datetime, zone: str = DAY_ZONE) -> datetime.datetime:
    """Midnight of the current Test Lab day, in UTC.

    `now` must be timezone-aware. Midnight always exists in this zone (the US
    transitions happen at 02:00/03:00 local), so the local day's start is exact
    even on the two days a year the offset changes.
    """
    if now.tzinfo is None:
        raise BudgetError("the clock read has no timezone")
    try:
        tzinfo = zoneinfo.ZoneInfo(zone)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError) as exc:
        raise BudgetError(
            f"the time zone database has no {zone}; the Test Lab day cannot be "
            f"resolved (install tzdata on the runner): {exc}"
        ) from exc
    local_midnight = now.astimezone(tzinfo).replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(datetime.timezone.utc)


def is_virtual(model: str | None, catalog_form: str | None) -> bool:
    """Whether a device id is a virtual (emulated) device.

    The catalog, when it could be read, is authoritative; otherwise the id
    decides (`.arm`/`.x86` emulator ids are virtual), and anything unrecognised
    is physical - the fail-closed answer, because physical device time is the
    expensive part of the quota.
    """
    if catalog_form:
        form = str(catalog_form).upper()
        if form in VIRTUAL_FORMS:
            return True
        if form == "PHYSICAL":
            return False
    if not model:
        return False
    lowered = str(model).lower()
    return any(marker in lowered for marker in VIRTUAL_ID_MARKERS)


def step_device(step: object) -> tuple[str | None, bool]:
    """The step's device id, and whether any dimension marks it virtual."""
    if not isinstance(step, dict):
        return None, False
    dimensions = step.get("dimensionValue")
    if not isinstance(dimensions, list):
        return None, False
    model = None
    virtual = False
    for dimension in dimensions:
        if not isinstance(dimension, dict):
            continue
        value = dimension.get("value")
        if not isinstance(value, str):
            continue
        if any(marker in value.lower() for marker in VIRTUAL_ID_MARKERS):
            virtual = True
        if dimension.get("key") in DEVICE_DIMENSION_KEYS and model is None:
            model = value
    return model, virtual


def classify_step(step: object, catalog: dict[str, str]) -> bool:
    """Whether the step's device is virtual. Unknown devices are physical."""
    model, marked_virtual = step_device(step)
    if marked_virtual:
        return True
    return is_virtual(model, catalog.get(model) if model else None)


class HttpTransport:
    """Tool Results/testing API reads with the gcloud access token.

    The token is held here and never printed: errors carry the URL and status
    only, so a failing run cannot leak it into a CI log.
    """

    def __init__(self, token: str, deadline: datetime.datetime | None = None) -> None:
        if not token:
            raise BudgetError("no access token to read the Test Lab count with")
        self._token = token
        self._deadline = deadline
        self._requests = 0

    def get_json(self, url: str) -> object:
        for attempt in range(REQUEST_ATTEMPTS):
            if self._deadline is not None and now_utc() > self._deadline:
                raise BudgetError(f"the Test Lab count ran past its deadline at {url}")
            self._requests += 1
            request = urllib.request.Request(
                url,
                headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
                    body = response.read()
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt + 1 < REQUEST_ATTEMPTS:
                    time.sleep(RETRY_BACKOFF_S)
                    continue
                raise BudgetError(f"{url} answered HTTP {exc.code}: {exc.reason}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt + 1 < REQUEST_ATTEMPTS:
                    time.sleep(RETRY_BACKOFF_S)
                    continue
                raise BudgetError(f"{url} could not be read: {exc}") from exc
            try:
                return json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BudgetError(f"{url} did not answer with JSON: {exc}") from exc
        raise BudgetError(f"{url} could not be read")  # unreachable: the loop returns or raises


def listing_page(transport: HttpTransport, url: str, key: str, page_token: str | None) -> tuple[list[object], str | None]:
    """One page of a Tool Results listing, or a fail-closed error."""
    page_url = f"{url}?pageSize={PAGE_SIZE}"
    if page_token:
        page_url += f"&pageToken={urllib.parse.quote(page_token, safe='')}"
    payload = transport.get_json(page_url)
    if not isinstance(payload, dict):
        raise BudgetError(f"{page_url} did not answer with an object")
    page = payload.get(key)
    if not isinstance(page, list):
        raise BudgetError(f"{page_url} did not answer with a '{key}' list")
    next_token = payload.get("nextPageToken")
    return page, next_token if isinstance(next_token, str) and next_token else None


def list_all(transport: HttpTransport, url: str, key: str) -> list[object]:
    """Every page of a Tool Results listing, or a fail-closed error."""
    items: list[object] = []
    page_token: str | None = None
    for _ in range(MAX_PAGES_PER_LISTING):
        page, page_token = listing_page(transport, url, key, page_token)
        items.extend(page)
        if not page_token:
            return items
    raise BudgetError(f"{url} answered more pages than this guard reads; refusing to undercount")


def executions_since(transport: HttpTransport, url: str, window_start: datetime.datetime) -> list[dict]:
    """Executions that could still hold a step from today's Test Lab day.

    A project's histories carry every execution it has ever run, and reading all
    of them is minutes of API calls per CI job. The API lists executions
    newest first, so the walk can stop at the first execution older than
    `window_start` - but only while the listing is *verified* to be in that
    order as it is read (each page descending, and each page older than the last
    one). A listing that breaks the order is read to its end instead, so no
    execution from today can hide behind a page the walk decided to skip.
    """
    kept: list[dict] = []
    page_token: str | None = None
    ordered = True
    oldest_so_far: datetime | None = None
    for _ in range(MAX_PAGES_PER_LISTING):
        rows, page_token = listing_page(transport, url, "executions", page_token)
        moments: list[datetime] = []
        for row in rows:
            created = timestamp(row.get("creationTime") if isinstance(row, dict) else None)
            if created is None:
                raise BudgetError(f"an execution in {url} has no creationTime")
            moments.append(created)
        if any(earlier < later for earlier, later in zip(moments, moments[1:])):
            ordered = False
        if oldest_so_far is not None and moments and moments[0] > oldest_so_far:
            ordered = False
        if moments:
            oldest_so_far = moments[-1]
        kept.extend(row for row, created in zip(rows, moments) if created >= window_start)
        if not page_token:
            return kept
        if ordered and oldest_so_far is not None and oldest_so_far < window_start:
            return kept
    raise BudgetError(f"{url} answered more pages than this guard reads; refusing to undercount")


def parallel_map(work: Callable[[Item], Answer], items: Iterable[Item]) -> list[Answer]:
    """Apply `work` to every item concurrently, in input order.

    Every read here is an independent HTTP call and any failure is a
    `BudgetError` that fails the guard closed, so the failures need no
    ordering: the first one raised stops the guard, the pool drains, and the
    guard reports that it could not prove the count.
    """
    ordered = list(items)
    if not ordered:
        return []
    workers = min(CONCURRENCY, len(ordered))
    if workers <= 1:
        return [work(item) for item in ordered]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ftl-budget") as pool:
        futures = [pool.submit(work, item) for item in ordered]
        return [future.result() for future in futures]


def history_id_of(history: object) -> str:
    history_id = history.get("historyId") if isinstance(history, dict) else None
    if not isinstance(history_id, str) or not history_id:
        raise BudgetError("a history has no historyId; the Test Lab count cannot be trusted")
    return history_id


def execution_id_of(execution: object, history_id: str) -> str:
    execution_id = execution.get("executionId") if isinstance(execution, dict) else None
    if not isinstance(execution_id, str) or not execution_id:
        raise BudgetError(f"an execution in {history_id} has no executionId")
    return execution_id


def executions_in(
    transport: HttpTransport,
    project_path: str,
    history_id: str,
    window_start: datetime.datetime,
) -> list[dict]:
    """The executions of one history that could still hold a step from today."""
    history_path = urllib.parse.quote(history_id, safe="")
    return executions_since(
        transport,
        f"{TOOL_RESULTS_BASE}/projects/{project_path}/histories/{history_path}/executions",
        window_start,
    )


def steps_in(transport: HttpTransport, project_path: str, history_id: str, execution_id: str) -> list[object]:
    """Every step of one execution."""
    history_path = urllib.parse.quote(history_id, safe="")
    execution_path = urllib.parse.quote(execution_id, safe="")
    return list_all(
        transport,
        f"{TOOL_RESULTS_BASE}/projects/{project_path}/histories/{history_path}"
        f"/executions/{execution_path}/steps",
        "steps",
    )


def android_device_catalog(transport: HttpTransport, project: str) -> tuple[dict[str, str], str | None]:
    """Device id -> catalog form, best effort. Returns (catalog, warning)."""
    url = f"{ANDROID_CATALOG_URL}?projectId={urllib.parse.quote(project, safe='')}"
    try:
        payload = transport.get_json(url)
    except BudgetError as exc:
        return {}, f"the device catalog could not be read ({exc}); device ids alone decided the kind"
    if not isinstance(payload, dict):
        return {}, "the device catalog did not answer with an object; device ids alone decided the kind"
    models = payload.get("androidDeviceCatalog", {})
    models = models.get("models") if isinstance(models, dict) else None
    if not isinstance(models, list):
        return {}, "the device catalog carried no model list; device ids alone decided the kind"
    catalog = {
        model["id"]: model.get("form")
        for model in models
        if isinstance(model, dict) and isinstance(model.get("id"), str)
    }
    return catalog, None


def count_device_steps(
    transport: HttpTransport,
    project: str,
    kind: str,
    day_start_utc: datetime.datetime,
    catalog: dict[str, str],
) -> int:
    """How many steps of this kind the project created in the Test Lab day.

    The histories are read concurrently, then the steps of every execution that
    could still hold today's steps. The walk inside one history stays
    sequential, because its pages are.
    """
    project_path = urllib.parse.quote(project, safe="")
    histories = list_all(transport, f"{TOOL_RESULTS_BASE}/projects/{project_path}/histories", "histories")
    history_ids = [history_id_of(history) for history in histories]
    window_start = day_start_utc - EXECUTION_LOOKBACK
    per_history = parallel_map(
        lambda history_id: executions_in(transport, project_path, history_id, window_start),
        history_ids,
    )
    executions = [
        (history_id, execution_id_of(execution, history_id))
        for history_id, history_executions in zip(history_ids, per_history)
        for execution in history_executions
    ]
    step_lists = parallel_map(lambda target: steps_in(transport, project_path, *target), executions)
    counted = 0
    for steps in step_lists:
        for step in steps:
            step_created = timestamp(step.get("creationTime") if isinstance(step, dict) else None)
            # A step with no creationTime is counted: failing towards a
            # refusal is the safe direction.
            if step_created is not None and step_created < day_start_utc:
                continue
            if classify_step(step, catalog) == (kind == "virtual"):
                counted += 1
    return counted


def gcloud_access_token() -> str:
    """`gcloud auth print-access-token`, which the workflow has authenticated.

    Only the exit status and the first line of stderr are ever printed - the
    token itself is not.
    """
    try:
        proc = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BudgetError(f"gcloud could not be run: {exc}") from exc
    if proc.returncode != 0:
        detail = [line for line in (proc.stderr or "").strip().splitlines() if line.strip()]
        raise BudgetError(
            "gcloud auth print-access-token failed "
            f"(exit {proc.returncode}): {detail[0][:200] if detail else 'no message'}"
        )
    token = (proc.stdout or "").strip()
    if not token:
        raise BudgetError("gcloud auth print-access-token printed no token")
    return token


def append_summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError as exc:
        print(f"::warning::could not write the step summary: {exc}")


def positive_ints(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from exc
    if number < 0:
        raise argparse.ArgumentTypeError(f"{value!r} is negative")
    return number


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refuse a Test Lab run over the project's daily budget.")
    parser.add_argument("--project", required=True, help="the Google Cloud project Test Lab runs in")
    parser.add_argument("--kind", required=True, choices=KINDS, help="the device kind this run adds")
    parser.add_argument("--max-per-day", required=True, type=positive_ints, help="this kind's daily allowance")
    parser.add_argument("--devices", type=positive_ints, default=1, help="devices this run adds (default 1)")
    args = parser.parse_args(argv)
    if not args.project.strip() or any(char.isspace() or char in "/?#" for char in args.project):
        parser.error(f"--project {args.project!r} is not a Google Cloud project id")
    return args


def run(args: argparse.Namespace, transport: HttpTransport, now: datetime.datetime) -> int:
    start = day_start(now)
    catalog, warning = android_device_catalog(transport, args.project)
    counted = count_device_steps(transport, args.project, args.kind, start, catalog)
    allowed = counted + args.devices <= args.max_per_day

    used = f"{counted}/{args.max_per_day} {args.kind} device steps today"
    summary = [
        f"## Test Lab daily budget ({args.kind})",
        "",
        f"- project: `{args.project}`",
        f"- Test Lab day: since {start.isoformat()} (midnight {DAY_ZONE})",
        f"- used today: {counted}/{args.max_per_day}",
        f"- this run adds: {args.devices}",
        f"- verdict: {'within budget' if allowed else 'refused'}",
    ]
    if warning:
        summary.append(f"- warning: {warning}")
    append_summary(summary)

    if warning:
        print(f"::warning::{warning}")
    if not allowed:
        raise BudgetError(
            f"Test Lab daily budget reached ({counted}/{args.max_per_day} {args.kind} today); "
            "spending needs owner approval"
        )
    print(f"Test Lab daily budget: {used} in {args.project}; this run adds {args.devices}.")
    return 0


def main(argv: list[str] | None = None, transport: HttpTransport | None = None, now: datetime.datetime | None = None) -> int:
    args = parse_args(argv)
    moment = now or now_utc()
    try:
        if transport is None:
            transport = HttpTransport(gcloud_access_token(), deadline=moment + TOTAL_DEADLINE)
        return run(args, transport, moment)
    except BudgetError as exc:
        print(f"::error::{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
