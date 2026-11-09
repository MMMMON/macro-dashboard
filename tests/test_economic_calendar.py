import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import fetch_economic_calendar as calendar


def instant(value):
    return datetime.fromisoformat(value)


def event(date="2026-11-10T08:30:00-05:00", **fields):
    return dict(title="CPI m/m", country="USD", impact="High", date=date, **fields)


class CalendarTests(unittest.TestCase):
    def setUp(self):
        printer = patch("builtins.print")
        printer.start()
        self.addCleanup(printer.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / "calendar.json"
        self.output.write_text('{"original": true}\n', encoding="utf-8")
        self.before = self.output.read_bytes()
        self.now = instant("2026-11-09T00:00:00+00:00")
        self.patch = patch.object(calendar, "OUTPUT", self.output)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.environment = patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(Path(self.directory.name) / "summary.md")})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def update(self, rows, now=None):
        with patch.object(calendar, "fetch_feed", return_value=rows):
            calendar.update(now or self.now)

    def test_rollover_and_dst(self):
        for now, expected in [
            ("2026-11-07T15:59:00+00:00", "2026-11-02"),
            ("2026-11-07T16:00:00+00:00", "2026-11-09"),
            ("2026-11-08T23:30:00+00:00", "2026-11-09"),
            ("2026-07-04T17:00:00+00:00", "2026-07-06"),
            ("2026-03-28T17:00:00+00:00", "2026-03-30"),
            ("2026-10-24T17:00:00+00:00", "2026-10-26"),
        ]:
            with self.subTest(now=now):
                self.assertEqual(calendar.target_week(instant(now))[0].date().isoformat(), expected)

    def test_previous_week_waits_before_deadline_without_writing(self):
        self.update([event("2026-11-03T08:30:00-05:00")], instant("2026-11-08T23:59:59+00:00"))
        self.assertEqual(self.output.read_bytes(), self.before)
        self.assertIn("WAITING", Path(os.environ["GITHUB_STEP_SUMMARY"]).read_text(encoding="utf-8"))

    def test_previous_week_fails_at_deadline(self):
        with self.assertRaises(calendar.NoMatchingEvents):
            self.update([event("2026-11-03T08:30:00-05:00")])
        self.assertEqual(self.output.read_bytes(), self.before)

    def test_empty_wrong_week_and_filtered_feed_fail(self):
        for rows in [[], [event("2026-10-20T08:30:00-04:00")], [dict(event(), impact="Low", title="Speech")]]:
            with self.subTest(rows=rows), self.assertRaises(RuntimeError):
                self.update(rows, instant("2026-11-07T17:00:00+00:00"))
            self.assertEqual(self.output.read_bytes(), self.before)

    def test_malformed_feed_fails(self):
        for rows in [{}, [None], [dict(event(), date="bad")], [dict(event(), date="2026-11-10T12:00:00")], [dict(event(), impact="Unknown")]]:
            with self.subTest(rows=rows), self.assertRaises(calendar.FeedFormatError):
                calendar.normalize(rows, self.now)

    def test_success_and_unchanged_preserve_timestamp(self):
        self.update([event()])
        first = self.output.read_bytes()
        self.update([event()], instant("2026-11-09T01:00:00+00:00"))
        self.assertEqual(first, self.output.read_bytes())
        self.assertIsNotNone(calendar.valid_cache(self.now))

    def test_cache_wrong_week_or_outside_event_is_rejected(self):
        self.update([event()])
        data = json.loads(self.output.read_text(encoding="utf-8"))
        for changes in [{"weekStart": "2026-11-02"}, {"weekEnd": "2026-11-14"}, {"events": [dict(data["events"][0], timestamp="2026-11-03T12:00:00Z")]}]:
            self.output.write_text(json.dumps(dict(data, **changes)), encoding="utf-8")
            self.assertIsNone(calendar.valid_cache(self.now))

    def test_network_failure_keeps_valid_cache_and_reports_failure(self):
        self.update([event()])
        before = self.output.read_bytes()
        with patch.object(calendar, "fetch_feed", side_effect=calendar.FeedUnavailable("timeout")):
            with self.assertRaises(calendar.FeedUnavailable):
                calendar.update(self.now)
        self.assertEqual(before, self.output.read_bytes())
        self.assertIn("FAILED", Path(os.environ["GITHUB_STEP_SUMMARY"]).read_text(encoding="utf-8"))

    def test_delayed_scheduled_flag_does_not_skip(self):
        with patch("sys.argv", ["calendar", "--scheduled"]), patch.object(calendar, "update") as update:
            calendar.main()
        update.assert_called_once()

    def test_atomic_replace_failure_preserves_file_and_cleans_temp(self):
        with patch.object(calendar.os, "replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.update([event()])
        self.assertEqual(self.output.read_bytes(), self.before)
        self.assertEqual(sorted(p.name for p in self.output.parent.iterdir()), ["calendar.json", "summary.md"])

    def test_invalid_json_is_format_error_without_network_retries(self):
        with patch.object(calendar.urllib.request, "urlopen") as request:
            request.return_value.__enter__.return_value.read.return_value = b"not json"
            with self.assertRaises(calendar.FeedFormatError):
                calendar.fetch_feed()
            request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
