import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import fetch_data as feed


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.start, self.end = date(2026, 1, 1), date(2026, 2, 1)
        self.meta = {"name": "test", "unit": "%"}
        self.points = [{"time": "2026-01-28", "value": 1.5}, {"time": "2026-01-30", "value": 1.6}]

    def test_clean_filters_deduplicates_and_orders(self):
        points = feed.clean_points([
            ("2026-01-30", 2), ("2026-01-28", 1), ("2026-01-30", 3),
            ("2026-01-29", "nan"), ("2026-01-27", "inf"), ("bad", 7),
            ("2026-02-01", 8), ("2025-12-31", 9), ("2026-01-26", "."),
        ], self.start, self.end)
        self.assertEqual(points, [{"time": "2026-01-28", "value": 1.0}, {"time": "2026-01-30", "value": 3.0}])

    def test_outage_preserves_values(self):
        result = feed.make_series(self.meta, [], {"data": self.points}, self.start, self.end, "outage")
        self.assertEqual(result["data"], self.points)
        self.assertEqual(result["status"], "cached")
        self.assertEqual(result["message"], "outage")

    def test_regressive_response_preserves_history(self):
        result = feed.make_series(self.meta, self.points[:1], {"data": self.points}, self.start, self.end)
        self.assertEqual(result["data"], self.points)
        self.assertEqual(result["status"], "cached")

    def test_missing_is_not_zero(self):
        result = feed.make_series(self.meta, [], {}, self.start, self.end)
        self.assertEqual(result["data"], [])
        self.assertIsNone(result["last_date"])
        self.assertEqual(result["status"], "unavailable")

    def test_staleness(self):
        result = feed.make_series(self.meta, [{"time": "2026-01-01", "value": -0.1}], {}, self.start, self.end)
        self.assertTrue(result["stale"])
        self.assertEqual(result["data"][0]["value"], -0.1)

    def test_unchanged_snapshot_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "data.json"
            payload = {"schema_version": 1, "series": {}}
            self.assertTrue(feed.write_snapshot(path, payload, {}))
            before = path.read_bytes()
            self.assertFalse(feed.write_snapshot(path, {"schema_version": 1, "series": {}}, json.loads(before)))
            self.assertEqual(path.read_bytes(), before)

    def test_all_failed_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "data.json"
            before = b'{"series": {}}'
            path.write_bytes(before)
            with patch('sys.argv', ['fetch_data.py', '--output', str(path)]), \
                 patch.object(feed, 'fetch_yahoo', return_value={}), \
                 patch.object(feed, 'fetch_fred_series', side_effect=RuntimeError), \
                 patch.object(feed, 'fetch_china10y', side_effect=RuntimeError):
                with self.assertRaises(RuntimeError):
                    feed.main()
            self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
