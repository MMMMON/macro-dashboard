import json
import tempfile
import unittest
from datetime import date, timedelta
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

    def test_cached_values_retain_actual_source(self):
        result = feed.make_series({**self.meta, "source": "FRED"}, [],
                                  {"data": self.points, "source": "Treasury", "symbol": "BC_10YEAR"},
                                  self.start, self.end)
        self.assertEqual(result["source"], "Treasury")
        self.assertEqual(result["symbol"], "BC_10YEAR")

    def test_breakeven_uses_common_dates_without_fill(self):
        nominal = [{"time": "2026-01-28", "value": 4.2}, {"time": "2026-01-29", "value": 4.1}]
        real = [{"time": "2026-01-28", "value": 1.8}, {"time": "2026-01-30", "value": 1.9}]
        self.assertEqual(feed.breakeven_points(nominal, real), [{"time": "2026-01-28", "value": 2.4}])

    def test_treasury_xml_filters_null_observations(self):
        document = '''<feed xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices">
        <m:properties><d:NEW_DATE>2026-01-28T00:00:00</d:NEW_DATE><d:TC_10YEAR>1.5</d:TC_10YEAR></m:properties>
        <m:properties><d:NEW_DATE>2026-01-29T00:00:00</d:NEW_DATE><d:TC_10YEAR m:null="true"/></m:properties></feed>'''
        self.assertEqual(feed.parse_treasury_xml(document, "TC_10YEAR", self.start, self.end), self.points[:1])

    def test_staleness(self):
        result = feed.make_series(self.meta, [{"time": "2026-01-01", "value": -0.1}], {}, self.start, self.end)
        self.assertTrue(result["stale"])
        self.assertEqual(result["data"][0]["value"], -0.1)

    def test_business_day_staleness_ignores_weekend_but_flags_missing_sessions(self):
        meta = {**self.meta, "stale_business_days": 1}
        weekend = feed.make_series(meta, [{"time": "2026-01-30", "value": 1}], {},
                                   date(2026, 1, 1), date(2026, 2, 2))
        delayed = feed.make_series(meta, [{"time": "2026-01-28", "value": 1}], {},
                                   date(2026, 1, 1), date(2026, 2, 2))
        self.assertFalse(weekend["stale"])
        self.assertTrue(delayed["stale"])

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
                 patch.object(feed, 'fetch_treasury_rates', return_value={}), \
                 patch.object(feed, 'fetch_fred_series', side_effect=RuntimeError), \
                 patch.object(feed, 'fetch_ofr_series', side_effect=RuntimeError), \
                 patch.object(feed, 'fetch_tbill_events', side_effect=RuntimeError), \
                 patch.object(feed, 'fetch_china10y', side_effect=RuntimeError):
                with self.assertRaises(RuntimeError):
                    feed.main()
            self.assertEqual(path.read_bytes(), before)


class LiquidityPQGTests(unittest.TestCase):
    def test_q_score_keeps_template_reserve_change_and_shrink_adjustment(self):
        score, detail = feed.score_q(1, 3.3, .05, True)
        self.assertEqual(score, 47)
        self.assertEqual(detail, {"on_rrp": 18, "reserves": 18, "reserve_change": 14, "balance_sheet_adjustment": -3})

    def test_p_score_uses_35_point_template_weights(self):
        score, detail = feed.score_p(61, 3.5, 1.49)
        self.assertEqual(score, 35)
        self.assertEqual(detail, {"curve": 14, "ois": 9, "tips": 12})

    def test_g_score_and_status_boundaries(self):
        self.assertEqual(feed.score_g(-.1, 1, 1)[0], 15)
        self.assertEqual(feed.status_for_score(80), "🟢宽松")
        self.assertEqual(feed.status_for_score(60), "🟡中性偏宽")
        self.assertEqual(feed.status_for_score(40), "🟠脆弱过渡")
        self.assertEqual(feed.status_for_score(39), "🔴缺氧紧缩")
        self.assertEqual(feed.status_for_score(None), "待核验")

    def test_repo_structure_thresholds(self):
        days = [date(2026, 1, 1) + timedelta(days=i) for i in range(20)]
        repo = {}
        for key in feed.OFR_REPO:
            values = []
            for index, day in enumerate(days):
                value = 100.0 if key.endswith("total") else 92.0
                if key.endswith("overnight") and index >= 10:
                    value = 86.0
                values.append({"time": day.isoformat(), "value": value})
            repo[key] = {"data": values}
        code, details = feed.repo_structure(repo, days[-1])
        self.assertEqual(code, 2)
        self.assertEqual(details["label"], "定期增多")

    def test_tbill_strong_siphon_threshold(self):
        events = {"data": [{"time": "2026-01-05", "value": 50_000_000_000}]}
        code, details = feed.tbill_structure(events, date(2026, 1, 15))
        self.assertEqual(code, 2)
        self.assertEqual(details["label"], "强虹吸")

    def test_cme_forward_requires_explicit_verified_source(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "CME SR3"):
                feed.fetch_cme_sr3_forward(date(2026, 1, 1), date(2026, 2, 1))

    def test_cme_forward_weights_sr3_contract_reference_periods(self):
        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {"contracts": [
                    {"time": "2026-01-01", "reference_start": "2027-01-01", "reference_end": "2027-04-01", "settlement": 96},
                    {"time": "2026-01-01", "reference_start": "2027-04-01", "reference_end": "2027-07-01", "settlement": 95},
                ]}

        class Session:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def get(self, *args, **kwargs): return Response()

        with patch.dict('os.environ', {'CME_SR3_FORWARD_URL': 'https://reviewed.example/sr3.json'}, clear=True), \
             patch.object(feed, 'http_session', return_value=Session()):
            result = feed.fetch_cme_sr3_forward(date(2026, 1, 1), date(2026, 2, 1))
        self.assertEqual(result[0]["time"], "2026-01-01")
        self.assertAlmostEqual(result[0]["value"], 4.502762, places=6)


if __name__ == '__main__':
    unittest.main()
