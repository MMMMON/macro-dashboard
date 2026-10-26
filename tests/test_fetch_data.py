import json
import io
import tempfile
import unittest
import zipfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import fetch_data as feed
import import_macromicro_ois as ois_import


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

    def test_macromicro_csv_incrementally_merges_and_removes_proxy_curve(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            csv_path, data_path = root / "ois.csv", root / "data.json"
            csv_path.write_text(
                "date,1M,3M,6M,1Y,2Y,10Y,30Y\n"
                "2026-01-06,3.68,3.65,3.59,3.44,3.31,3.80,4.18\n",
                encoding="utf-8",
            )
            series = {key: {"data": [{"time": "2026-01-05", "value": 3.5}]}
                      for key in feed.MACROMICRO_OIS}
            series["cms_ois_1y"] = {"data": [{"time": "2026-01-05", "value": 3.4}]}
            data_path.write_text(json.dumps({"liquidity_pqg": {"series": series}}), encoding="utf-8")
            with patch("sys.argv", ["import_macromicro_ois.py", str(csv_path),
                                     "--data-json", str(data_path)]):
                ois_import.main()
            updated = json.loads(data_path.read_text(encoding="utf-8"))["liquidity_pqg"]["series"]
            self.assertEqual([p["time"] for p in updated["ois_1m"]["data"]],
                             ["2026-01-05", "2026-01-06"])
            self.assertNotIn("cms_ois_1y", updated)
            self.assertEqual(updated["ois_1m"]["source"], "MacroMicro（账户授权 CSV）")

    def test_macromicro_csv_drops_partial_dates_across_all_tenors(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            csv_path, data_path = root / "ois.csv", root / "data.json"
            csv_path.write_text(
                "date,1M,3M,6M,1Y,2Y,10Y,30Y\n"
                "2026-01-05,3.68,3.65,3.59,3.44,3.31,3.80,4.18\n"
                "2026-01-06,3.69,3.66,3.60,3.45,3.32,3.81,\n",
                encoding="utf-8",
            )
            data_path.write_text(json.dumps({"liquidity_pqg": {"series": {}}}), encoding="utf-8")
            with patch("sys.argv", ["import_macromicro_ois.py", str(csv_path),
                                     "--data-json", str(data_path)]):
                ois_import.main()
            updated = json.loads(data_path.read_text(encoding="utf-8"))["liquidity_pqg"]["series"]
            for key in feed.MACROMICRO_OIS:
                self.assertEqual([p["time"] for p in updated[key]["data"]], ["2026-01-05"])

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

    def test_european_spread_uses_common_dates_and_keeps_negative_values(self):
        left = [{"time": "2026-01-05", "value": 2.1}, {"time": "2026-01-06", "value": 1.9}]
        right = [{"time": "2026-01-05", "value": 2.3}, {"time": "2026-01-07", "value": 2.0}]
        self.assertEqual(feed.spread_points(left, right), [{"time": "2026-01-05", "value": -20.0}])

    def test_ecb_csv_parser_uses_published_observations_only(self):
        document = "TIME_PERIOD,OBS_VALUE\n2026-01-05,3.25\n2026-01-06,\n2026-02-01,9.9\n"
        self.assertEqual(feed.parse_ecb_csv(document, self.start, self.end),
                         [{"time": "2026-01-05", "value": 3.25}])

    def test_banque_france_parser_selects_supported_tenors(self):
        document = """Title:,one,five,ten,thirty
Series key:,FM.D.FR.EUR.FR2.BB.FRMOYTEC1.HSTA,FM.D.FR.EUR.FR2.BB.FRMOYTEC5.HSTA,FM.D.FR.EUR.FR2.BB.FRMOYTEC10.HSTA,FM.D.FR.EUR.FR2.BB.FRMOYTEC30.HSTA
Unit:,% ,% ,% ,%
Multiplier:,0,0,0,0
Collection:,E,E,E,E
Source:,Euronext,Euronext,Euronext,Euronext
2026-01-05,2.1,2.5,3.0,3.4
2026-01-06,-,2.6,3.1,3.5
"""
        result = feed.parse_banque_france_tec_csv(document, self.start, self.end)
        self.assertEqual(result["fr_1y"], [{"time": "2026-01-05", "value": 2.1}])
        self.assertEqual(result["fr_10y"][-1], {"time": "2026-01-06", "value": 3.1})

    def test_bundesbank_parser_ignores_metadata_and_missing_values(self):
        document = '"",series,flags\nDecimals,2,\n2026-01-05,2.50,\n2026-01-06,.,No value\n'
        self.assertEqual(feed.parse_bundesbank_csv(document, self.start, self.end),
                         [{"time": "2026-01-05", "value": 2.5}])

    def test_banco_espana_parser_maps_dates_and_values(self):
        payload = [{"serie": "D_DTES00B7", "fechas": ["2026-01-05T00:00:00Z"],
                    "valores": [2.25]}]
        result = feed.parse_banco_espana_json(payload, self.start, self.end)
        self.assertEqual(result["es_3m"], [{"time": "2026-01-05", "value": 2.25}])
        self.assertEqual(result["es_10y"], [])

    def test_banca_italia_parser_maps_benchmark_columns(self):
        csv_document = ('"DATA_OSS";"MFN_BMK.D.020.922.0.EUR.205";'
                        '"MFN_BMK.D.020.922.0.EUR.210";"MFN_BMK.D.020.922.0.EUR.230"\n'
                        '"2026/01/05";"2.5";"3.1";"3.8"\n')
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("data.csv", csv_document)
        result = feed.parse_banca_italia_zip(buffer.getvalue(), self.start, self.end)
        self.assertEqual(result["it_10y"], [{"time": "2026-01-05", "value": 3.1}])

    def test_greece_one_year_parser_keeps_sparse_auction_days(self):
        document = """<table><thead><tr><th>Date</th><th>13 Wk</th><th>52 Wk</th></tr></thead>
        <tbody><tr><td>2026-01-07</td><td>2.1</td><td>2.44%</td></tr>
        <tr><td>2026-01-08</td><td>2.2</td><td>—</td></tr>
        <tr><td>2026-01-21</td><td>2.0</td><td>2,38</td></tr></tbody></table>"""
        self.assertEqual(feed.parse_greece_1y_auctions(document, self.start, self.end), [
            {"time": "2026-01-07", "value": 2.44}, {"time": "2026-01-21", "value": 2.38}])

    def test_greece_thirty_year_parser_reads_monthly_last_yield(self):
        document = """<table><tr><th>Month</th><th>3Y Price</th><th>3Y Yield</th><th>30Y Price</th><th>30Y Yield</th></tr>
        <tr><td>January 2026</td><td>99.1</td><td>2.1</td><td>97.4</td><td>4.27</td></tr>
        <tr><td>31/01/2026</td><td>99.2</td><td>2.2</td><td>97.5</td><td>4.20</td></tr></table>"""
        self.assertEqual(feed.parse_greece_30y_monthly(document, self.start, self.end),
                         [{"time": "2026-01-01", "value": 4.27}])

    def test_european_source_failure_preserves_cache_and_degrades_derived_spread(self):
        old_point = [{"time": "2026-01-20", "value": 3.0}]
        previous = {"series": {"fr_10y": {"data": old_point}}, "spreads": {}}
        live_point = [{"time": "2026-01-20", "value": 2.0}]
        def macro(stat_id, start, end):
            if stat_id == feed.MACROMICRO_EUROPEAN_BONDS["fr_10y"][0]:
                raise RuntimeError("licensed source outage")
            return live_point
        core = {"us10y": {"data": live_point, "status": "ok", "stale": False,
                           "source": "Treasury", "source_url": "https://example.test"}}
        with patch.dict("os.environ", {"MACROMICRO_API_KEY": "test"}), \
             patch.object(feed, "fetch_public_european_curves", return_value=({}, {})), \
             patch.object(feed, "fetch_macromicro_series", side_effect=macro), \
             patch.object(feed, "fetch_greece_1y_auctions", return_value=live_point), \
             patch.object(feed, "fetch_greece_30y_monthly", return_value=live_point), \
             patch.object(feed, "fetch_ecb_aaa_10y", return_value=live_point):
            result = feed.build_european_bonds(core, previous, self.start, self.end)
        self.assertEqual(result["series"]["fr_10y"]["status"], "cached")
        self.assertEqual(result["series"]["fr_10y"]["data"], old_point)
        self.assertEqual(result["spreads"]["fr_de_10y"]["status"], "cached")
        self.assertEqual(result["spreads"]["fr_de_10y"]["data"],
                         [{"time": "2026-01-20", "value": 100.0}])

    def test_treasury_xml_filters_null_observations(self):
        document = '''<feed xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices">
        <m:properties><d:NEW_DATE>2026-01-28T00:00:00</d:NEW_DATE><d:TC_10YEAR>1.5</d:TC_10YEAR></m:properties>
        <m:properties><d:NEW_DATE>2026-01-29T00:00:00</d:NEW_DATE><d:TC_10YEAR m:null="true"/></m:properties></feed>'''
        self.assertEqual(feed.parse_treasury_xml(document, "TC_10YEAR", self.start, self.end), self.points[:1])

    def test_treasury_fallback_covers_liquidity_rate_panels(self):
        expected = {"dgs2", "dgs5", "dgs10", "dgs30", "real5y", "tips_10y", "real30y"}
        self.assertTrue(expected.issubset(feed.TREASURY_RATE_FIELDS))
        self.assertEqual(feed.TREASURY_RATE_FIELDS["tips_10y"],
                         ("daily_treasury_real_yield_curve", "TC_10YEAR"))

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
                 patch.object(feed, 'fetch_kw_official', side_effect=RuntimeError), \
                 patch.object(feed, 'fetch_fred_series', side_effect=RuntimeError), \
                 patch.object(feed, 'fetch_ofr_series', side_effect=RuntimeError), \
                 patch.object(feed, 'fetch_tbill_events', side_effect=RuntimeError), \
                 patch.object(feed, 'build_european_bonds', return_value={"state": "unavailable", "series": {}, "spreads": {}, "issues": []}), \
                 patch.object(feed, 'fetch_china10y', side_effect=RuntimeError):
                with self.assertRaises(RuntimeError):
                    feed.main()
            self.assertEqual(path.read_bytes(), before)


class LiquidityPQGTests(unittest.TestCase):
    def test_cme_term_sofr_requires_oauth_credentials(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "CME Term SOFR API"):
                feed.fetch_cme_term_sofr(date(2026, 1, 1), date(2026, 2, 1))

    def test_cme_term_sofr_oauth_and_history_parsing(self):
        class Response:
            def __init__(self, payload):
                self.status_code = 200
                self._payload = payload
            def json(self): return self._payload
        class Session:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def post(self, url, **kwargs):
                self.token_url = url
                return Response({"access_token": "short-lived"})
            def get(self, url, **kwargs):
                self.history_url = url
                return Response({"payload": [
                    {"businessDt": "2026-01-02", "sofrRt": 3.6,
                     "instrument": {"productCode": "TR1"}},
                    {"businessDt": "2026-01-02", "sofrRt": 3.5,
                     "instrument": {"productCode": "TR3"}},
                    {"businessDt": "2026-01-02", "sofrRt": 3.4,
                     "instrument": {"productCode": "TR6"}},
                    {"businessDt": "2026-01-02", "sofrRt": 3.3,
                     "instrument": {"productCode": "T1Y"}},
                ], "metadata": {"totalPages": 1}})
        session = Session()
        with patch.object(feed, 'http_session', return_value=session):
            result = feed.fetch_cme_term_sofr(
                date(2026, 1, 1), date(2026, 2, 1), api_id="id", api_password="password")
        self.assertEqual(result["term_sofr_1m"], [{"time": "2026-01-02", "value": 3.6}])
        self.assertEqual(result["term_sofr_1y"], [{"time": "2026-01-02", "value": 3.3}])
        self.assertNotIn("id", session.history_url)
        self.assertNotIn("password", session.history_url)

    def test_macromicro_chart_csv_maps_all_tenors(self):
        document = (
            "Date,US OIS 1 Month,US OIS 3 Months,US OIS 6 Months,US OIS 1 Year,"
            "US OIS 2 Years,US OIS 10 Years,US OIS 30 Years\n"
            "2026-01-02,1,2,3,4,5,6,7\n"
        )
        parsed = ois_import.parse_chart_csv(document)
        self.assertEqual(parsed["ois_1m"], [("2026-01-02", "1")])
        self.assertEqual(parsed["ois_30y"], [("2026-01-02", "7")])

    def test_macromicro_requires_api_authorization(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "MacroMicro API"):
                feed.fetch_macromicro_series(43599, date(2026, 1, 1), date(2026, 2, 1))

    def test_macromicro_parses_licensed_series(self):
        class Response:
            status_code = 200
            @staticmethod
            def json():
                return {"stat_id": 43599, "series": [
                    {"date": "2026-01-02", "val": "4.5"},
                    {"date": "2026-02-01", "val": "9.9"},
                ]}
        class Session:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def get(self, *args, **kwargs): return Response()
        with patch.object(feed, 'http_session', return_value=Session()):
            result = feed.fetch_macromicro_series(
                43599, date(2026, 1, 1), date(2026, 2, 1), api_key="secret")
        self.assertEqual(result, [{"time": "2026-01-02", "value": 4.5}])

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
                    {"time": "2026-01-01", "reference_start": "2027-07-01", "reference_end": "2027-10-01", "settlement": 95},
                    {"time": "2026-01-01", "reference_start": "2027-10-01", "reference_end": "2028-01-01", "settlement": 94},
                ]}

        class Session:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def get(self, *args, **kwargs): return Response()

        with patch.dict('os.environ', {'CME_SR3_FORWARD_URL': 'https://reviewed.example/sr3.json'}, clear=True), \
             patch.object(feed, 'http_session', return_value=Session()):
            result = feed.fetch_cme_sr3_forward(date(2026, 1, 1), date(2026, 2, 1))
        self.assertEqual(result[0]["time"], "2026-01-01")
        self.assertAlmostEqual(result[0]["value"], 5.005479, places=6)

    def test_yahoo_sr3_derivation_requires_nearly_complete_forward_window(self):
        observed = "2026-01-02"
        complete = [
            (date(2027, 1, 1), date(2027, 4, 1), [{"time": observed, "value": 96.0}]),
            (date(2027, 4, 1), date(2027, 7, 1), [{"time": observed, "value": 95.5}]),
            (date(2027, 7, 1), date(2027, 10, 1), [{"time": observed, "value": 95.0}]),
            (date(2027, 10, 1), date(2028, 1, 5), [{"time": observed, "value": 94.5}]),
        ]
        result = feed.derive_sr3_1y1y(complete, date(2026, 1, 1), date(2026, 2, 1))
        self.assertEqual(len(result), 1)
        self.assertGreater(result[0]["value"], 4.0)
        self.assertLess(result[0]["value"], 5.6)

        missing_quarter = complete[:3]
        self.assertEqual(
            feed.derive_sr3_1y1y(missing_quarter, date(2026, 1, 1), date(2026, 2, 1)),
            [],
        )

    def test_acm_term_premium_parses_official_monthly_download(self):
        class Response:
            status_code = 200
            text = "RunDates,TERMYld,ACMFITYld,GSWYld\n31-Jan-2026,0.42,4.1,4.2\n"

        class Session:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def get(self, *args, **kwargs): return Response()

        with patch.object(feed, 'http_session', return_value=Session()):
            result = feed.fetch_acm_10y_term_premium(date(2026, 1, 1), date(2026, 2, 1))
        self.assertEqual(result, [{"time": "2026-01-31", "value": 0.42}])


if __name__ == '__main__':
    unittest.main()
