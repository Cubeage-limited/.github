#!/usr/bin/env python3
"""Offline tests for the Test Lab daily budget guard.

The Tool Results and testing API answers are fixtures, so every case here runs
without a network, a token or a Test Lab run: the day boundary (including both
DST transitions), the physical/virtual classification, the counted steps, the
refusal message, and the fail-closed paths (transport error, unparseable body,
unknown device, missing timestamp, missing token).
"""
from __future__ import annotations

import datetime
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
import unittest.mock
import urllib.error

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import check_ftl_daily_budget as guard  # noqa: E402

PACIFIC = guard.DAY_ZONE


def utc(*parts: int) -> datetime.datetime:
    return datetime.datetime(*parts, tzinfo=datetime.timezone.utc)


def stamp(moment: datetime.datetime) -> dict[str, str]:
    return {"seconds": str(int(moment.timestamp()))}


class FakeTransport:
    """A Tool Results/testing API that answers from fixtures, in order."""

    def __init__(self, routes: list[tuple[str, object]], fail: str | None = None) -> None:
        self.routes = routes
        self.fail = fail
        self.requested: list[str] = []

    def get_json(self, url: str) -> object:
        self.requested.append(url)
        if self.fail and self.fail in url:
            raise guard.BudgetError(f"{url} answered HTTP 403: Forbidden")
        for fragment, payload in self.routes:
            if fragment in url:
                return payload
        raise AssertionError(f"no fixture for {url}")


def fixture(*, histories: list[dict], executions: dict[str, list[dict]], steps: dict[str, list[dict]]) -> list[tuple[str, object]]:
    routes: list[tuple[str, object]] = [("/histories?", {"histories": histories})]
    for history_id, rows in executions.items():
        routes.append((f"/histories/{history_id}/executions?", {"executions": rows}))
    for execution_id, rows in steps.items():
        routes.append((f"/executions/{execution_id}/steps?", {"steps": rows}))
    return routes


def execution(execution_id: str, created: datetime.datetime) -> dict:
    return {"executionId": execution_id, "creationTime": stamp(created)}


def step(model: str | None, created: datetime.datetime | None, *, virtual_marker: str | None = None) -> dict:
    dimensions = []
    if model is not None:
        dimensions.append({"key": "Model", "value": model})
    if virtual_marker is not None:
        dimensions.append({"key": "Device", "value": virtual_marker})
    dimensions.append({"key": "Version", "value": "33"})
    payload: dict = {"name": "Robo test", "dimensionValue": dimensions}
    if created is not None:
        payload["creationTime"] = stamp(created)
    return payload


def catalog_payload(forms: dict[str, str]) -> dict:
    return {
        "androidDeviceCatalog": {
            "models": [{"id": model_id, "form": form} for model_id, form in forms.items()]
        }
    }


class DayBoundaryTests(unittest.TestCase):
    def test_midnight_pacific_summer(self) -> None:
        # 2026-09-28 06:59Z is 2026-09-27 23:59 PDT: still the previous day.
        self.assertEqual(guard.day_start(utc(2026, 9, 28, 6, 59)), utc(2026, 9, 27, 7))
        self.assertEqual(guard.day_start(utc(2026, 9, 28, 7, 0)), utc(2026, 9, 28, 7))
        self.assertEqual(guard.day_start(utc(2026, 9, 28, 23, 59)), utc(2026, 9, 28, 7))

    def test_midnight_pacific_winter(self) -> None:
        # PST is UTC-8: the day rolls over at 08:00Z.
        self.assertEqual(guard.day_start(utc(2026, 1, 15, 7, 59)), utc(2026, 1, 14, 8))
        self.assertEqual(guard.day_start(utc(2026, 1, 15, 8, 0)), utc(2026, 1, 15, 8))

    def test_spring_forward_day(self) -> None:
        # 2026-03-08: 02:00 PST becomes 03:00 PDT. Local midnight is PST (08:00Z).
        self.assertEqual(guard.day_start(utc(2026, 3, 8, 8, 0)), utc(2026, 3, 8, 8))
        self.assertEqual(guard.day_start(utc(2026, 3, 8, 10, 0)), utc(2026, 3, 8, 8))
        self.assertEqual(guard.day_start(utc(2026, 3, 9, 6, 59)), utc(2026, 3, 8, 8))

    def test_fall_back_day(self) -> None:
        # 2026-11-01: 02:00 PDT becomes 01:00 PST. Local midnight is PDT (07:00Z).
        self.assertEqual(guard.day_start(utc(2026, 11, 1, 7, 0)), utc(2026, 11, 1, 7))
        self.assertEqual(guard.day_start(utc(2026, 11, 1, 9, 30)), utc(2026, 11, 1, 7))
        self.assertEqual(guard.day_start(utc(2026, 11, 2, 6, 59)), utc(2026, 11, 1, 7))

    def test_other_offsets_are_converted(self) -> None:
        hong_kong = datetime.timezone(datetime.timedelta(hours=8))
        self.assertEqual(guard.day_start(utc(2026, 9, 28, 7).astimezone(hong_kong)), utc(2026, 9, 28, 7))

    def test_naive_clock_fails_closed(self) -> None:
        with self.assertRaises(guard.BudgetError):
            guard.day_start(datetime.datetime(2026, 9, 28, 7))


class ClassificationTests(unittest.TestCase):
    def test_emulator_ids_are_virtual(self) -> None:
        for model in ("MediumPhone.arm", "MediumTablet.arm", "Pixel2.arm", "Pixel2.x86", "MediumPhone.arm64"):
            with self.subTest(model=model):
                self.assertTrue(guard.is_virtual(model, None))

    def test_physical_ids_are_physical(self) -> None:
        for model in ("oriole", "akita", "a16x", "a05s", "a10", "blazer", "CPH2449", "SC-51E", "iphone14pro", "iphone16pro"):
            with self.subTest(model=model):
                self.assertFalse(guard.is_virtual(model, None))

    def test_catalog_decides_when_it_answers(self) -> None:
        self.assertTrue(guard.is_virtual("weirdvirtualid", "VIRTUAL"))
        self.assertTrue(guard.is_virtual("weirdvirtualid", "EMULATOR"))
        self.assertFalse(guard.is_virtual("oriole", "PHYSICAL"))
        # An unknown form falls back to the id rule.
        self.assertTrue(guard.is_virtual("MediumPhone.arm", "DEVICE_FORM_UNSPECIFIED"))

    def test_unknown_device_is_physical(self) -> None:
        self.assertFalse(guard.is_virtual(None, None))
        self.assertFalse(guard.is_virtual("", None))
        self.assertFalse(guard.is_virtual("some-new-device", None))

    def test_step_without_a_device_dimension_is_physical(self) -> None:
        self.assertFalse(guard.classify_step(step(None, utc(2026, 9, 28, 8)), {}))
        self.assertFalse(guard.classify_step({"dimensionValue": []}, {}))
        self.assertFalse(guard.classify_step("not a step", {}))

    def test_step_dimensions_classify_the_device(self) -> None:
        virtual = step("MediumPhone.arm", utc(2026, 9, 28, 8))
        self.assertTrue(guard.classify_step(virtual, {}))
        physical = step("oriole", utc(2026, 9, 28, 8))
        self.assertFalse(guard.classify_step(physical, {}))

    def test_a_virtual_marker_in_any_dimension_counts(self) -> None:
        marked = step("unknown-id", utc(2026, 9, 28, 8), virtual_marker="Pixel2.arm")
        self.assertTrue(guard.classify_step(marked, {}))

    def test_catalog_lookup_uses_the_model_dimension(self) -> None:
        self.assertTrue(guard.classify_step(step("htc-virtual", utc(2026, 9, 28, 8)), {"htc-virtual": "VIRTUAL"}))
        self.assertFalse(guard.classify_step(step("htc-physical", utc(2026, 9, 28, 8)), {"htc-physical": "PHYSICAL"}))


class CountTests(unittest.TestCase):
    def routes(self, **kwargs: object) -> FakeTransport:
        return FakeTransport(fixture(**kwargs))  # type: ignore[arg-type]

    def count(self, transport: FakeTransport, kind: str, start: datetime.datetime, catalog: dict | None = None) -> int:
        return guard.count_device_steps(transport, "lavapot-1292", kind, start, catalog or {})

    def test_counts_only_steps_inside_the_day(self) -> None:
        start = utc(2026, 9, 28, 7)
        transport = self.routes(
            histories=[{"historyId": "bh.one"}],
            executions={"bh.one": [execution("1", utc(2026, 9, 28, 6, 50)), execution("2", utc(2026, 9, 28, 8))]},
            steps={
                "1": [step("MediumPhone.arm", utc(2026, 9, 28, 6, 55))],
                "2": [
                    step("MediumPhone.arm", utc(2026, 9, 28, 6, 59)),
                    step("MediumPhone.arm", utc(2026, 9, 28, 7)),
                    step("MediumPhone.arm", utc(2026, 9, 28, 9, 30)),
                ],
            },
        )
        self.assertEqual(self.count(transport, "virtual", start), 2)

    def test_kind_selects_the_device_class(self) -> None:
        start = utc(2026, 9, 28, 7)
        transport = self.routes(
            histories=[{"historyId": "bh.one"}],
            executions={"bh.one": [execution("1", utc(2026, 9, 28, 8))]},
            steps={"1": [step("MediumPhone.arm", utc(2026, 9, 28, 8)), step("oriole", utc(2026, 9, 28, 8, 30))]},
        )
        self.assertEqual(self.count(transport, "virtual", start), 1)
        self.assertEqual(self.count(transport, "physical", start), 1)

    def test_executions_older_than_the_lookback_are_skipped(self) -> None:
        start = utc(2026, 9, 28, 7)
        transport = self.routes(
            histories=[{"historyId": "bh.one"}],
            executions={"bh.one": [execution("1", start - guard.EXECUTION_LOOKBACK - datetime.timedelta(minutes=1))]},
            steps={},
        )
        self.assertEqual(self.count(transport, "physical", start), 0)
        self.assertEqual([url for url in transport.requested if "/steps" in url], [])

    def test_a_newest_first_listing_stops_at_the_lookback(self) -> None:
        start = utc(2026, 9, 28, 7)
        now = utc(2026, 9, 28, 7, 30)
        transport = FakeTransport(
            [
                ("pageToken=page2", {"executions": [execution("2", start - datetime.timedelta(days=3))]}),
                (
                    "/histories/bh.one/executions?",
                    {
                        "executions": [
                            execution("1", now),
                            execution("old", start - guard.EXECUTION_LOOKBACK - datetime.timedelta(minutes=1)),
                        ],
                        "nextPageToken": "page2",
                    },
                ),
                ("/executions/1/steps?", {"steps": [step("oriole", now)]}),
                ("/histories?", {"histories": [{"historyId": "bh.one"}]}),
            ]
        )
        self.assertEqual(self.count(transport, "physical", start), 1)
        self.assertFalse(any("pageToken=page2" in url for url in transport.requested))

    def test_an_unordered_listing_is_read_to_its_end(self) -> None:
        start = utc(2026, 9, 28, 7)
        now = utc(2026, 9, 28, 7, 30)
        transport = FakeTransport(
            [
                # Page 2 holds a newer execution than page 1: the listing is not
                # ordered, so the walk must not stop at page 1.
                ("pageToken=page2", {"executions": [execution("2", now - datetime.timedelta(minutes=5))]}),
                (
                    "/histories/bh.one/executions?",
                    {
                        "executions": [
                            execution("old", start - guard.EXECUTION_LOOKBACK - datetime.timedelta(minutes=1)),
                            execution("1", now),
                        ],
                        "nextPageToken": "page2",
                    },
                ),
                ("/executions/1/steps?", {"steps": [step("oriole", now)]}),
                ("/executions/2/steps?", {"steps": [step("akita", now)]}),
                ("/histories?", {"histories": [{"historyId": "bh.one"}]}),
            ]
        )
        self.assertEqual(self.count(transport, "physical", start), 2)
        self.assertTrue(any("pageToken=page2" in url for url in transport.requested))

    def test_a_page_that_is_not_descending_inside_itself_reads_to_the_end(self) -> None:
        start = utc(2026, 9, 28, 7)
        now = utc(2026, 9, 28, 7, 30)
        transport = FakeTransport(
            [
                ("pageToken=page2", {"executions": []}),
                (
                    "/histories/bh.one/executions?",
                    {
                        "executions": [
                            execution("old", start - datetime.timedelta(days=2)),
                            execution("1", now),
                        ],
                        "nextPageToken": "page2",
                    },
                ),
                ("/executions/1/steps?", {"steps": [step("oriole", now)]}),
                ("/histories?", {"histories": [{"historyId": "bh.one"}]}),
            ]
        )
        self.assertEqual(self.count(transport, "physical", start), 1)
        self.assertTrue(any("pageToken=page2" in url for url in transport.requested))

    def test_all_histories_are_walked(self) -> None:
        start = utc(2026, 9, 28, 7)
        transport = self.routes(
            histories=[{"historyId": "bh.one"}, {"historyId": "bh.two"}],
            executions={
                "bh.one": [execution("1", utc(2026, 9, 28, 8))],
                "bh.two": [execution("2", utc(2026, 9, 28, 9))],
            },
            steps={
                "1": [step("oriole", utc(2026, 9, 28, 8))],
                "2": [step("oriole", utc(2026, 9, 28, 9)), step("akita", utc(2026, 9, 28, 9))],
            },
        )
        self.assertEqual(self.count(transport, "physical", start), 3)

    def test_pagination_is_followed(self) -> None:
        start = utc(2026, 9, 28, 7)
        transport = FakeTransport(
            [
                ("pageToken=page2", {"histories": []}),
                ("/histories?", {"histories": [{"historyId": "bh.one"}], "nextPageToken": "page2"}),
                ("/histories/bh.one/executions?", {"executions": [execution("1", utc(2026, 9, 28, 8))]}),
                ("/executions/1/steps?", {"steps": [step("oriole", utc(2026, 9, 28, 8))]}),
            ]
        )
        self.assertEqual(self.count(transport, "physical", start), 1)
        self.assertTrue(any("pageToken=page2" in url for url in transport.requested))

    def test_a_step_without_a_timestamp_is_counted(self) -> None:
        start = utc(2026, 9, 28, 7)
        transport = self.routes(
            histories=[{"historyId": "bh.one"}],
            executions={"bh.one": [execution("1", utc(2026, 9, 28, 8))]},
            steps={"1": [step("oriole", None)]},
        )
        self.assertEqual(self.count(transport, "physical", start), 1)

    def test_an_unreadable_listing_fails_closed(self) -> None:
        start = utc(2026, 9, 28, 7)
        for payload in ([], {"histories": {}}, {"nope": []}, "text"):
            with self.subTest(payload=payload):
                transport = FakeTransport([("/histories?", payload)])
                with self.assertRaises(guard.BudgetError):
                    self.count(transport, "physical", start)

    def test_an_execution_without_a_timestamp_fails_closed(self) -> None:
        start = utc(2026, 9, 28, 7)
        transport = self.routes(histories=[{"historyId": "bh.one"}], executions={"bh.one": [{"executionId": "1"}]}, steps={})
        with self.assertRaises(guard.BudgetError):
            self.count(transport, "physical", start)

    def test_a_history_without_an_id_fails_closed(self) -> None:
        start = utc(2026, 9, 28, 7)
        transport = self.routes(histories=[{"displayName": "no id"}], executions={}, steps={})
        with self.assertRaises(guard.BudgetError):
            self.count(transport, "physical", start)


class CatalogTests(unittest.TestCase):
    def test_catalog_is_read_as_model_to_form(self) -> None:
        transport = FakeTransport([("testEnvironmentCatalog", catalog_payload({"oriole": "PHYSICAL", "MediumPhone.arm": "VIRTUAL"}))])
        catalog, warning = guard.android_device_catalog(transport, "lavapot-1292")
        self.assertEqual(catalog, {"oriole": "PHYSICAL", "MediumPhone.arm": "VIRTUAL"})
        self.assertIsNone(warning)

    def test_catalog_failure_is_a_warning_not_a_refusal(self) -> None:
        transport = FakeTransport([], fail="testEnvironmentCatalog")
        catalog, warning = guard.android_device_catalog(transport, "lavapot-1292")
        self.assertEqual(catalog, {})
        self.assertIn("device catalog could not be read", warning or "")

    def test_unparseable_catalog_is_a_warning(self) -> None:
        for payload in ({"androidDeviceCatalog": {}}, {"androidDeviceCatalog": "x"}, "text"):
            with self.subTest(payload=payload):
                transport = FakeTransport([("testEnvironmentCatalog", payload)])
                catalog, warning = guard.android_device_catalog(transport, "lavapot-1292")
                self.assertEqual(catalog, {})
                self.assertIsNotNone(warning)


class TransportTests(unittest.TestCase):
    def http_error(self, code: int, message: str) -> urllib.error.HTTPError:
        """An HTTPError fixture that is closed when the test ends.

        HTTPError extends the response base class, which owns a temporary
        file; leaving instances open makes the interpreter warn at exit.
        """
        error = urllib.error.HTTPError("u", code, message, {}, io.BytesIO())
        self.addCleanup(error.close)
        return error

    def test_retries_one_transient_failure(self) -> None:
        responses = [
            self.http_error(503, "unavailable"),
            io.BytesIO(json.dumps({"ok": True}).encode()),
        ]
        with unittest.mock.patch.object(guard.urllib.request, "urlopen", side_effect=responses):
            with unittest.mock.patch.object(guard.time, "sleep"):
                self.assertEqual(guard.HttpTransport("token").get_json("https://example.invalid"), {"ok": True})

    def test_gives_up_on_a_client_error(self) -> None:
        error = self.http_error(403, "forbidden")
        with unittest.mock.patch.object(guard.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(guard.BudgetError) as caught:
                guard.HttpTransport("token").get_json("https://example.invalid")
        self.assertIn("403", str(caught.exception))

    def test_gives_up_after_repeated_server_errors(self) -> None:
        error = self.http_error(500, "boom")
        with unittest.mock.patch.object(guard.urllib.request, "urlopen", side_effect=error):
            with unittest.mock.patch.object(guard.time, "sleep"):
                with self.assertRaises(guard.BudgetError):
                    guard.HttpTransport("token").get_json("https://example.invalid")

    def test_timeout_fails_closed(self) -> None:
        with unittest.mock.patch.object(guard.urllib.request, "urlopen", side_effect=TimeoutError("timed out")):
            with unittest.mock.patch.object(guard.time, "sleep"):
                with self.assertRaises(guard.BudgetError):
                    guard.HttpTransport("token").get_json("https://example.invalid")

    def test_unparseable_body_fails_closed(self) -> None:
        with unittest.mock.patch.object(guard.urllib.request, "urlopen", return_value=io.BytesIO(b"<html>not json</html>")):
            with self.assertRaises(guard.BudgetError):
                guard.HttpTransport("token").get_json("https://example.invalid")

    def test_the_token_never_appears_in_an_error(self) -> None:
        secret = "ya29.super-secret-token"
        error = self.http_error(403, "forbidden")
        with unittest.mock.patch.object(guard.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(guard.BudgetError) as caught:
                guard.HttpTransport(secret).get_json("https://example.invalid")
        self.assertNotIn(secret, str(caught.exception))

    def test_deadline_stops_the_read(self) -> None:
        expired = guard.HttpTransport("token", deadline=utc(2026, 9, 28, 7))
        with unittest.mock.patch.object(guard, "now_utc", return_value=utc(2026, 9, 28, 8)):
            with self.assertRaises(guard.BudgetError) as caught:
                expired.get_json("https://example.invalid")
        self.assertIn("deadline", str(caught.exception))


class ParallelTests(unittest.TestCase):
    """The concurrent reads, which is what keeps the guard inside its budget."""

    def test_results_keep_the_input_order(self) -> None:
        self.assertEqual(guard.parallel_map(lambda n: n * 2, [3, 1, 2]), [6, 2, 4])

    def test_every_item_is_read(self) -> None:
        seen: list[int] = []
        with unittest.mock.patch.object(guard, "CONCURRENCY", 4):
            self.assertEqual(guard.parallel_map(lambda n: seen.append(n) or n, list(range(20))), list(range(20)))
        self.assertEqual(sorted(seen), list(range(20)))

    def test_no_items_means_no_reads(self) -> None:
        def never(item: object) -> object:
            raise AssertionError("nothing to read")

        self.assertEqual(guard.parallel_map(never, []), [])

    def test_a_failure_fails_closed(self) -> None:
        def unreliable(n: int) -> int:
            if n == 3:
                raise guard.BudgetError("the read failed")
            return n

        with unittest.mock.patch.object(guard, "CONCURRENCY", 4):
            with self.assertRaises(guard.BudgetError) as caught:
                guard.parallel_map(unreliable, list(range(10)))
        self.assertIn("the read failed", str(caught.exception))

    def test_a_failing_history_fails_the_count_closed(self) -> None:
        start = utc(2026, 9, 28, 7)
        transport = FakeTransport(
            [
                ("/histories?", {"histories": [{"historyId": "bh.one"}, {"historyId": "bh.bad"}]}),
                ("/histories/bh.one/executions?", {"executions": [execution("1", utc(2026, 9, 28, 8))]}),
                ("/executions/1/steps?", {"steps": [step("oriole", utc(2026, 9, 28, 8))]}),
            ],
            fail="bh.bad",
        )
        with self.assertRaises(guard.BudgetError):
            guard.count_device_steps(transport, "lavapot-1292", "physical", start, {})


class TokenTests(unittest.TestCase):
    def test_reads_the_token_without_printing_it(self) -> None:
        secret = "ya29.secret"
        done = unittest.mock.Mock(returncode=0, stdout=secret + "\n", stderr="")
        captured = io.StringIO()
        with unittest.mock.patch.object(guard.subprocess, "run", return_value=done):
            with unittest.mock.patch.object(sys, "stdout", captured):
                self.assertEqual(guard.gcloud_access_token(), secret)
        self.assertNotIn(secret, captured.getvalue())

    def test_gcloud_failure_is_a_budget_error(self) -> None:
        done = unittest.mock.Mock(returncode=1, stdout="", stderr="ERROR: no active account\nmore")
        with unittest.mock.patch.object(guard.subprocess, "run", return_value=done):
            with self.assertRaises(guard.BudgetError) as caught:
                guard.gcloud_access_token()
        self.assertIn("no active account", str(caught.exception))

    def test_missing_gcloud_is_a_budget_error(self) -> None:
        with unittest.mock.patch.object(guard.subprocess, "run", side_effect=FileNotFoundError("gcloud")):
            with self.assertRaises(guard.BudgetError):
                guard.gcloud_access_token()

    def test_empty_token_is_a_budget_error(self) -> None:
        done = unittest.mock.Mock(returncode=0, stdout="  \n", stderr="")
        with unittest.mock.patch.object(guard.subprocess, "run", return_value=done):
            with self.assertRaises(guard.BudgetError):
                guard.gcloud_access_token()


class MainTests(unittest.TestCase):
    def run_guard(self, transport: FakeTransport, argv: list[str], now: datetime.datetime, summary: bool = False):
        captured = io.StringIO()
        env = {"GITHUB_STEP_SUMMARY": self.summary_path} if summary else {}
        with unittest.mock.patch.dict(os.environ, env, clear=False):
            with unittest.mock.patch.object(sys, "stdout", captured):
                code = guard.main(argv, transport=transport, now=now)
        return code, captured.getvalue()

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.summary_path = os.path.join(self.temp.name, "summary.md")

    def day(self, used_physical: int, used_virtual: int = 0) -> FakeTransport:
        now = utc(2026, 9, 28, 7, 30)
        steps = [step("oriole", now - datetime.timedelta(minutes=i + 1)) for i in range(used_physical)]
        steps += [step("MediumPhone.arm", now - datetime.timedelta(minutes=i + 1)) for i in range(used_virtual)]
        return FakeTransport(
            [
                ("/histories?", {"histories": [{"historyId": "bh.one"}]}),
                ("/histories/bh.one/executions?", {"executions": [execution("1", now - datetime.timedelta(hours=1))]}),
                ("/executions/1/steps?", {"steps": steps}),
                ("testEnvironmentCatalog", catalog_payload({"oriole": "PHYSICAL", "MediumPhone.arm": "VIRTUAL"})),
            ]
        )

    def test_within_budget_passes(self) -> None:
        code, out = self.run_guard(self.day(2), ["--project", "lavapot-1292", "--kind", "physical", "--max-per-day", "3"], utc(2026, 9, 28, 7, 30))
        self.assertEqual(code, 0)
        self.assertIn("2/3", out)
        self.assertNotIn("::error::", out)

    def test_exactly_at_the_limit_passes(self) -> None:
        code, out = self.run_guard(
            self.day(2), ["--project", "lavapot-1292", "--kind", "physical", "--max-per-day", "3", "--devices", "1"], utc(2026, 9, 28, 7, 30)
        )
        self.assertEqual(code, 0, out)

    def test_over_budget_fails_with_the_owner_approval_message(self) -> None:
        code, out = self.run_guard(self.day(3), ["--project", "lavapot-1292", "--kind", "physical", "--max-per-day", "3"], utc(2026, 9, 28, 7, 30))
        self.assertEqual(code, 1)
        self.assertIn(
            "::error::Test Lab daily budget reached (3/3 physical today); spending needs owner approval",
            out,
        )

    def test_devices_are_added_to_the_count(self) -> None:
        code, out = self.run_guard(self.day(2), ["--project", "lavapot-1292", "--kind", "physical", "--max-per-day", "3", "--devices", "2"], utc(2026, 9, 28, 7, 30))
        self.assertEqual(code, 1)
        self.assertIn("(2/3 physical today)", out)

    def test_virtual_devices_have_their_own_budget(self) -> None:
        code, out = self.run_guard(self.day(3, 9), ["--project", "lavapot-1292", "--kind", "virtual", "--max-per-day", "10"], utc(2026, 9, 28, 7, 30))
        self.assertEqual(code, 0, out)
        self.assertIn("9/10", out)
        code, out = self.run_guard(self.day(3, 10), ["--project", "lavapot-1292", "--kind", "virtual", "--max-per-day", "10"], utc(2026, 9, 28, 7, 30))
        self.assertEqual(code, 1)
        self.assertIn("(10/10 virtual today)", out)

    def test_count_is_written_to_the_summary(self) -> None:
        code, _ = self.run_guard(
            self.day(1), ["--project", "lavapot-1292", "--kind", "physical", "--max-per-day", "3"], utc(2026, 9, 28, 7, 30), summary=True
        )
        self.assertEqual(code, 0)
        written = pathlib.Path(self.summary_path).read_text(encoding="utf-8")
        self.assertIn("Test Lab daily budget (physical)", written)
        self.assertIn("used today: 1/3", written)
        self.assertIn("within budget", written)

    def test_an_api_error_fails_closed(self) -> None:
        transport = FakeTransport([], fail="googleapis")
        code, out = self.run_guard(transport, ["--project", "lavapot-1292", "--kind", "physical", "--max-per-day", "3"], utc(2026, 9, 28, 7, 30))
        self.assertEqual(code, 1)
        self.assertIn("::error::", out)

    def test_a_broken_catalog_still_counts_and_warns(self) -> None:
        now = utc(2026, 9, 28, 7, 30)
        transport = FakeTransport(
            [
                ("/histories?", {"histories": [{"historyId": "bh.one"}]}),
                ("/histories/bh.one/executions?", {"executions": [execution("1", now)]}),
                ("/executions/1/steps?", {"steps": [step("MediumPhone.arm", now)]}),
            ],
            fail="testEnvironmentCatalog",
        )
        code, out = self.run_guard(transport, ["--project", "lavapot-1292", "--kind", "virtual", "--max-per-day", "10"], now)
        self.assertEqual(code, 0, out)
        self.assertIn("::warning::", out)
        self.assertIn("1/10", out)

    def test_a_missing_summary_file_is_not_fatal(self) -> None:
        code, _ = self.run_guard(self.day(0), ["--project", "lavapot-1292", "--kind", "physical", "--max-per-day", "3"], utc(2026, 9, 28, 7, 30), summary=True)
        self.assertEqual(code, 0)

    def test_bad_input_is_rejected(self) -> None:
        cases = [
            ["--project", "lavapot-1292", "--kind", "phone", "--max-per-day", "3"],
            ["--project", "lavapot-1292", "--kind", "physical", "--max-per-day", "three"],
            ["--project", "lavapot-1292", "--kind", "physical", "--max-per-day", "-1"],
            ["--project", "a/b", "--kind", "physical", "--max-per-day", "3"],
            ["--kind", "physical", "--max-per-day", "3"],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit):
                    guard.parse_args(argv)

    def test_missing_gcloud_fails_closed(self) -> None:
        captured = io.StringIO()
        with unittest.mock.patch.object(guard, "now_utc", return_value=utc(2026, 9, 28, 7, 30)):
            with unittest.mock.patch.object(guard.subprocess, "run", side_effect=FileNotFoundError("gcloud")):
                with unittest.mock.patch.object(sys, "stdout", captured):
                    code = guard.main(["--project", "lavapot-1292", "--kind", "physical", "--max-per-day", "3"])
        self.assertEqual(code, 1)
        self.assertIn("::error::", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
