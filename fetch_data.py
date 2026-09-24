"""Daily static snapshot. No credentials or upstream requests are exposed to browsers."""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent
TREASURY_URL = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
YAHOO = {
    "btc": ("BTC-USD", "Bitcoin", "USD"),
    "eth": ("ETH-USD", "Ethereum", "USD"),
    "oil": ("CL=F", "WTI 原油期货", "USD/桶"),
    "sp500": ("^GSPC", "标普 500", "点"),
    "gold": ("GC=F", "COMEX 黄金期货", "USD/盎司"),
    "dxy": ("DX-Y.NYB", "美元指数 DXY", "点"),
    "aapl": ("AAPL", "Apple", "USD"),
    "msft": ("MSFT", "Microsoft", "USD"),
    "nvda": ("NVDA", "NVIDIA", "USD"),
    "amzn": ("AMZN", "Amazon", "USD"),
    "googl": ("GOOGL", "Alphabet", "USD"),
    "meta": ("META", "Meta", "USD"),
    "tsla": ("TSLA", "Tesla", "USD"),
    "nasdaq": ("^IXIC", "纳斯达克综合", "点"),
    "dax": ("^GDAXI", "德国 DAX", "点"),
    "nikkei": ("^N225", "日经 225", "点"),
    "hsi": ("^HSI", "恒生指数", "点"),
    "shanghai": ("000001.SS", "上证综指", "点"),
}
FRED = {
    "us10y": ("DGS10", "美国 10Y 国债收益率"),
    "real10y": ("DFII10", "美国 10Y 实际利率"),
    "breakeven10y": ("T10YIE", "美国 10Y 盈亏平衡通胀率"),
}
LIQUIDITY_FRED = {
    "on_rrp": ("RRPONTSYD", "ON RRP", "T", 1 / 1000),
    "reserves": ("WRESBAL", "准备金", "T", 1 / 1_000_000),
    "curve_2s10s": ("T10Y2Y", "2Y–10Y", "bp", 100),
    "tips_10y": ("DFII10", "10Y TIPS", "%", 1),
    "sofr": ("SOFR", "SOFR", "%", 1),
    "iorb": ("IORB", "IORB", "%", 1),
    "fed_assets": ("WALCL", "Fed 总资产", "T", 1 / 1_000_000),
}
OFR_BASE = "https://data.financialresearch.gov/v1"
OFR_REPO = {
    "repo_dvp_total": "REPO-DVP_TV_TOT-P",
    "repo_dvp_overnight": "REPO-DVP_TV_OO-P",
    "repo_gcf_total": "REPO-GCF_TV_TOT-P",
    "repo_gcf_overnight": "REPO-GCF_TV_OO-P",
    "repo_tri_total": "REPO-TRIV1_TV_TOT-P",
    "repo_tri_overnight": "REPO-TRIV1_TV_OO-P",
}


def http_session():
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=Retry(
        total=2, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])))
    session.headers["User-Agent"] = "Mozilla/5.0 MacroDashboard/1.0"
    return session


def clean_points(rows, start: date, end: date):
    """Finite, unique, sorted ISO days in [start, end); never fill closed markets."""
    by_day = {}
    for raw_day, raw_value in rows:
        try:
            day = pd.Timestamp(raw_day).date()
            value = float(raw_value)
            if start <= day < end and math.isfinite(value):
                by_day[day.isoformat()] = round(value, 6)
        except (ValueError, TypeError, OverflowError):
            continue
    return [{"time": day, "value": by_day[day]} for day in sorted(by_day)]


def fetch_fred_series(series_id: str, start: date, end: date, api_key=None):
    """Official FRED API interface; public graph CSV works without an API key."""
    key = api_key or os.getenv("FRED_API_KEY")
    with http_session() as session:
        if key:
            response = session.get("https://api.stlouisfed.org/fred/series/observations", params={
                "series_id": series_id, "api_key": key, "file_type": "json",
                "observation_start": start.isoformat(),
                "observation_end": (end - timedelta(days=1)).isoformat(),
            }, timeout=(10, 35))
            # Avoid exception messages containing the credential-bearing request URL.
            if response.status_code != 200:
                raise RuntimeError(f"FRED API HTTP {response.status_code}")
            rows = [(x["date"], x["value"]) for x in response.json()["observations"]]
        else:
            response = session.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={
                "id": series_id, "cosd": start.isoformat(),
                "coed": (end - timedelta(days=1)).isoformat(),
            }, timeout=(10, 35))
            if response.status_code != 200:
                raise RuntimeError(f"FRED CSV HTTP {response.status_code}")
            reader = csv.DictReader(io.StringIO(response.text))
            rows = [(r.get("observation_date", r.get("DATE")), r.get(series_id)) for r in reader]
    return clean_points(rows, start, end)


def parse_treasury_xml(document, field, start, end):
    ns = {"m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
          "d": "http://schemas.microsoft.com/ado/2007/08/dataservices"}
    root = ET.fromstring(document)
    rows = [(p.findtext("d:NEW_DATE", namespaces=ns), p.findtext(f"d:{field}", namespaces=ns))
            for p in root.findall(".//m:properties", ns)]
    return clean_points(rows, start, end)


def breakeven_points(nominal, real):
    """Nominal minus real yields in percentage points, only on common dates."""
    real_by_day = {p["time"]: p["value"] for p in real}
    return [{"time": p["time"], "value": round(p["value"] - real_by_day[p["time"]], 6)}
            for p in nominal if p["time"] in real_by_day]


def fetch_treasury_rates(start, end):
    """Official keyless Treasury rates; replace whole histories, never splice sources."""
    tasks = [(kind, field, year) for kind, field in [
        ("daily_treasury_yield_curve", "BC_10YEAR"),
        ("daily_treasury_real_yield_curve", "TC_10YEAR")]
        for year in range(start.year, (end - timedelta(days=1)).year + 1)]

    def fetch_year(task):
        kind, field, year = task
        with http_session() as session:
            response = session.get(TREASURY_URL, params={
                "data": kind, "field_tdr_date_value": year}, timeout=(10, 35))
            if response.status_code != 200:
                raise RuntimeError(f"Treasury HTTP {response.status_code}")
            # Parse the whole requested year, then trim the assembled history below.
            points = parse_treasury_xml(response.content, field, date(year, 1, 1), date(year + 1, 1, 1))
            if not points:
                raise RuntimeError(f"Treasury {year} returned no observations")
            return field, points

    grouped = {"BC_10YEAR": [], "TC_10YEAR": []}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for field, points in pool.map(fetch_year, tasks):
            grouped[field].extend(points)
    nominal, real = [clean_points(((p["time"], p["value"]) for p in grouped[field]), start, end)
                     for field in ["BC_10YEAR", "TC_10YEAR"]]
    return {"us10y": nominal, "real10y": real,
            "breakeven10y": breakeven_points(nominal, real)}


def fetch_china10y(start: date, end: date):
    """Daily ChinaBond sovereign 10Y curve; query in windows shorter than a year."""
    rows = []
    cursor = start
    with http_session() as session:
        while cursor < end:
            stop = min(cursor + timedelta(days=300), end)
            response = session.get(
                "https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/historyQuery",
                params={"startDate": cursor.isoformat(),
                        "endDate": (stop - timedelta(days=1)).isoformat(),
                        "gjqx": "0", "qxId": "ycqx", "locale": "cn_ZH"},
                timeout=(10, 35),
            )
            if response.status_code != 200:
                raise RuntimeError(f"ChinaBond HTTP {response.status_code}")
            response.encoding = "utf-8"
            tables = pd.read_html(io.StringIO(response.text), header=0)
            matched = False
            for table in tables:
                if {"曲线名称", "日期", "10年"}.issubset(table.columns):
                    selected = table[table["曲线名称"].astype(str).str.strip() == "中债国债收益率曲线"]
                    rows.extend(zip(selected["日期"], selected["10年"]))
                    matched = True
            if not matched:
                raise RuntimeError("ChinaBond response schema changed")
            cursor = stop
    return clean_points(rows, start, end)


def fetch_yahoo(start: date, end: date):
    symbols = [spec[0] for spec in YAHOO.values()]
    # Optional ASCII certificate path for curl on Windows with a Unicode venv path.
    session = None
    if os.getenv("YFINANCE_CA_BUNDLE"):
        from curl_cffi.requests import Session
        session = Session(impersonate="chrome", verify=os.environ["YFINANCE_CA_BUNDLE"])
    # Explicitly adjusted closes for splits/dividends; end excludes unfinished days.
    frame = yf.download(symbols, start=start.isoformat(), end=end.isoformat(),
                        auto_adjust=True, group_by="ticker", threads=False, session=session,
                        progress=False, timeout=25, multi_level_index=True)
    result = {}
    for key, (symbol, _, _) in YAHOO.items():
        try:
            result[key] = clean_points(frame[symbol]["Close"].items(), start, end)
        except (KeyError, TypeError):
            result[key] = []
        if not result[key]:
            for attempt in range(2):
                try:
                    retry = yf.download(symbol, start=start.isoformat(), end=end.isoformat(),
                                        auto_adjust=True, progress=False, threads=False,
                                        timeout=25, multi_level_index=False, session=session)
                    result[key] = clean_points(retry["Close"].items(), start, end)
                    if result[key]:
                        break
                except Exception:
                    pass
                time.sleep(attempt + 1)
    return result


def make_series(meta, points, old, start, end, error=None):
    previous = clean_points(((p["time"], p["value"]) for p in old.get("data", [])), start, end)
    # Regressive/short responses are outages, not permission to erase history.
    if points and previous and (points[-1]["time"] < previous[-1]["time"] or
                               len(points) < len(previous) * 0.8):
        error, points = "上游返回不完整，保留上次有效数据", []
    if points:
        status = "ok"
    else:
        points = previous
        status = "cached" if points else "unavailable"
        if previous:
            # Cached values retain the provenance of the source that produced them.
            meta = {**meta, **{k: old[k] for k in ["source", "source_url", "symbol", "note"] if k in old}}
    last_date = points[-1]["time"] if points else None
    stale = False
    if last_date:
        last_day = date.fromisoformat(last_date)
        if "stale_business_days" in meta:
            missing_business_days = sum(
                (last_day + timedelta(days=offset)).weekday() < 5
                for offset in range(1, (end - last_day).days)
            )
            stale = missing_business_days > meta["stale_business_days"]
        else:
            stale = (end - last_day).days > meta.get("stale_days", 7)
    return {**meta, "status": status, "stale": stale, "last_date": last_date,
            "message": error if status != "ok" else None, "data": points}


def scale_points(points, multiplier):
    return [{"time": point["time"], "value": round(point["value"] * multiplier, 6)} for point in points]


def fetch_ofr_series(mnemonic: str, start: date, end: date):
    """Read one published OFR STFM series without filling disclosure-related gaps."""
    with http_session() as session:
        response = session.get(f"{OFR_BASE}/series/full", params={"mnemonic": mnemonic}, timeout=(10, 35))
        if response.status_code != 200:
            raise RuntimeError(f"OFR HTTP {response.status_code}")
        payload = response.json()
    rows = payload.get(mnemonic, {}).get("timeseries", {}).get("aggregation", [])
    return clean_points(rows, start, end)


def fetch_tbill_events(start: date, end: date):
    """Net bill issuance events from official Treasury auction results.

    A bill's offering amount is added on issue date and removed on maturity date.
    This is a transparent net-supply proxy, not a claim about all outstanding debt.
    """
    query_start = start - timedelta(days=400)
    endpoint = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query"
    with http_session() as session:
        response = session.get(endpoint, params={
            "filter": f"security_type:eq:Bill,issue_date:gte:{query_start.isoformat()},issue_date:lte:{end.isoformat()}",
            "fields": "issue_date,maturity_date,offering_amt",
            "sort": "issue_date", "page[size]": 10000,
        }, timeout=(10, 35))
        if response.status_code != 200:
            raise RuntimeError(f"Treasury auctions HTTP {response.status_code}")
        rows = response.json().get("data", [])
    events = defaultdict(float)
    for row in rows:
        try:
            issue, maturity, amount = row["issue_date"], row["maturity_date"], float(row["offering_amt"])
            events[issue] += amount
            events[maturity] -= amount
        except (KeyError, TypeError, ValueError):
            continue
    return clean_points(events.items(), start, end)


def fetch_cme_sr3_forward(start: date, end: date):
    """Derive a 1Y1Y SOFR proxy from a reviewed CME SR3 settlement feed.

    CME's historical-settlement API is entitlement-gated, so the deployment may
    set ``CME_SR3_FORWARD_URL`` to a reviewed public export.  Each raw row must
    identify its observation day, the SOFR reference-period start/end, and its
    quarterly SR3 settlement.  We weight ``100 - settlement`` only by the days
    overlapping the 13th--24th months after that observation day.  A pre-derived
    ``time``/``value`` feed is retained for backwards-compatible reviewed feeds.
    No other interest-rate series is ever substituted.
    """
    url = os.getenv("CME_SR3_FORWARD_URL")
    if not url:
        raise RuntimeError("未配置可验证的 CME SR3 远期结算源")
    with http_session() as session:
        response = session.get(url, timeout=(10, 35))
        if response.status_code != 200:
            raise RuntimeError(f"CME forward feed HTTP {response.status_code}")
        payload = response.json()
    rows = payload.get("data", payload.get("contracts", [])) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise RuntimeError("CME forward feed 格式无效")
    if rows and all("value" in row for row in rows):
        return clean_points(((row.get("time"), row.get("value")) for row in rows), start, end)

    grouped = defaultdict(list)
    for row in rows:
        try:
            observed = row.get("time") or row.get("observation_date") or row.get("trade_date")
            period_start = row.get("reference_start") or row.get("period_start")
            period_end = row.get("reference_end") or row.get("period_end")
            settlement = float(row.get("settlement", row.get("settle")))
            observed_day = date.fromisoformat(observed[:10])
            start_day, end_day = date.fromisoformat(period_start[:10]), date.fromisoformat(period_end[:10])
            if end_day <= start_day or not 0 < settlement < 100:
                continue
            grouped[observed_day].append((start_day, end_day, settlement))
        except (AttributeError, TypeError, ValueError):
            continue
    points = []
    for observed_day, contracts in grouped.items():
        window_start, window_end = observed_day + timedelta(days=365), observed_day + timedelta(days=730)
        weighted_rate = weight = 0
        for contract_start, contract_end, settlement in contracts:
            overlap = max(0, (min(contract_end, window_end) - max(contract_start, window_start)).days)
            weighted_rate += overlap * (100 - settlement)
            weight += overlap
        if weight:
            points.append({"time": observed_day.isoformat(), "value": weighted_rate / weight})
    if not points:
        raise RuntimeError("CME SR3 结算源缺少可推导的季度合约")
    return clean_points(((point["time"], point["value"]) for point in points), start, end)


def point_in_window(series, start: date, end: date):
    candidates = [point for point in series.get("data", [])
                  if start.isoformat() <= point["time"] <= end.isoformat()]
    return candidates[-1] if candidates else None


def week_key(day: date):
    year, week, _ = day.isocalendar()
    return f"{year}-{week:02d}"


def score_q(on_rrp, reserves, reserve_change, balance_sheet_shrinking):
    if None in (on_rrp, reserves, reserve_change):
        return None, None
    rrp_points = 18 if on_rrp >= 1 else 14 if on_rrp >= .5 else 9 if on_rrp >= .3 else 4
    reserve_points = 18 if reserves >= 3.3 else 15 if reserves >= 3 else 10 if reserves >= 2.8 else 5
    change_points = 14 if reserve_change >= .05 else 10 if reserve_change >= 0 else 6 if reserve_change >= -.05 else 2
    adjustment = -3 if balance_sheet_shrinking else 0
    return rrp_points + reserve_points + change_points + adjustment, {
        "on_rrp": rrp_points, "reserves": reserve_points, "reserve_change": change_points,
        "balance_sheet_adjustment": adjustment,
    }


def score_p(curve, ois, tips):
    if None in (curve, ois, tips):
        return None, None
    curve_points = 14 if curve > 60 else 11 if curve >= 20 else 6 if curve >= -10 else 3
    ois_points = 9 if ois <= 3.5 else 7 if ois <= 3.7 else 5 if ois <= 4 else 3
    tips_points = 12 if tips < 1.5 else 10 if tips < 1.8 else 7 if tips < 2 else 3
    return curve_points + ois_points + tips_points, {"curve": curve_points, "ois": ois_points, "tips": tips_points}


def score_g(sofr_iorb, repo, tbill):
    if None in (sofr_iorb, repo, tbill):
        return None, None
    spread_points = 10 if sofr_iorb < 0 else 8 if sofr_iorb <= 2 else 5 if sofr_iorb <= 5 else 2
    repo_points = {1: 3, 2: 2, 3: 1}[repo]
    bill_points = {1: 2, 2: 1}[tbill]
    return spread_points + repo_points + bill_points, {"sofr_iorb": spread_points, "repo": repo_points, "tbill": bill_points}


def status_for_score(total):
    if total is None:
        return "待核验"
    if total >= 80:
        return "🟢宽松"
    if total >= 60:
        return "🟡中性偏宽"
    if total >= 40:
        return "🟠脆弱过渡"
    return "🔴缺氧紧缩"


def trend_tag(value, previous, neutral=0.01):
    if value is None or previous is None:
        return "待核验"
    change = value - previous
    if change > neutral:
        return "回升"
    if change < -neutral:
        return "续降"
    return "持平"


def repo_structure(repo, as_of: date):
    """Classify rolling ten-business-day tenor and venue structure."""
    keys = list(OFR_REPO)
    by_date = {key: {p["time"]: p["value"] for p in repo[key]["data"]} for key in keys}
    common = sorted(set.intersection(*(set(points) for points in by_date.values())) if by_date else set())
    common = [day for day in common if day <= as_of.isoformat()]
    if len(common) < 20 or (as_of - date.fromisoformat(common[-1])).days > 14:
        return None, None
    current, prior = common[-10:], common[-20:-10]

    def shares(days):
        total = sum(sum(by_date[key][day] for key in ("repo_dvp_total", "repo_gcf_total", "repo_tri_total")) for day in days)
        overnight = sum(sum(by_date[key][day] for key in (
            "repo_dvp_overnight", "repo_gcf_overnight", "repo_tri_overnight")) for day in days)
        term = total - overnight
        cleared = sum(sum(by_date[key][day] for key in ("repo_dvp_total", "repo_gcf_total")) for day in days)
        if total <= 0:
            return None, None
        return term / total, cleared / total

    current_term, current_cleared = shares(current)
    prior_term, prior_cleared = shares(prior)
    if None in (current_term, current_cleared, prior_term, prior_cleared):
        return None, None
    term_change, cleared_change = current_term - prior_term, current_cleared - prior_cleared
    if term_change >= .02:
        return 2, {"label": "定期增多", "term_share_change_bp": round(term_change * 10_000, 1),
                   "cleared_share_change_bp": round(cleared_change * 10_000, 1)}
    if abs(cleared_change) >= .05:
        return 3, {"label": "结构分化", "term_share_change_bp": round(term_change * 10_000, 1),
                   "cleared_share_change_bp": round(cleared_change * 10_000, 1)}
    return 1, {"label": "隔夜偏多", "term_share_change_bp": round(term_change * 10_000, 1),
               "cleared_share_change_bp": round(cleared_change * 10_000, 1)}


def tbill_structure(events, as_of: date):
    start = as_of - timedelta(days=14)
    value = sum(point["value"] for point in events.get("data", []) if start.isoformat() < point["time"] <= as_of.isoformat())
    return (2 if value >= 50_000_000_000 else 1), {"label": "强虹吸" if value >= 50_000_000_000 else "轻虹吸",
                                                     "net_issuance_t": round(value / 1_000_000_000_000, 4)}


def build_liquidity_pqg(series, repo, tbill_events, end: date):
    """Create the eight weekly PQG rows, keeping unavailable inputs visibly unavailable."""
    weeks = []
    monday = end - timedelta(days=end.weekday())
    if end < monday + timedelta(days=3):
        monday -= timedelta(days=7)
    for offset in range(8):
        week_monday = monday - timedelta(days=7 * offset)
        week_thursday, week_friday = week_monday + timedelta(days=3), week_monday + timedelta(days=4)
        # Thursday/Friday is preferred; when an official daily release has not
        # posted yet, retain the most recent value earlier in the same week.
        daily = {key: point_in_window(item, week_thursday, week_friday) or point_in_window(item, week_monday, week_thursday - timedelta(days=1))
                 for key, item in series.items() if key != "fed_assets"}
        reserve = point_in_window(series["reserves"], week_monday, week_monday + timedelta(days=2))
        assets = point_in_window(series["fed_assets"], week_monday, week_monday + timedelta(days=2))
        anchor = max((point["time"] for point in daily.values() if point), default=reserve["time"] if reserve else None)
        if not anchor:
            continue
        weeks.append({"week": week_key(week_monday), "week_start": week_monday.isoformat(), "as_of": anchor,
                      "daily": daily, "reserves": reserve, "fed_assets": assets})
    weeks.reverse()
    output = []
    for index, row in enumerate(weeks):
        values = {key: point["value"] if point else None for key, point in row["daily"].items()}
        values["reserves"] = row["reserves"]["value"] if row["reserves"] else None
        values["fed_assets"] = row["fed_assets"]["value"] if row["fed_assets"] else None
        prior = output[-1]["metrics"] if output else {}
        reserve_change = values["reserves"] - prior["reserves"] if values["reserves"] is not None and prior.get("reserves") is not None else None
        assets = [candidate["fed_assets"]["value"] if candidate["fed_assets"] else None for candidate in weeks[max(0, index - 4):index + 1]]
        shrinking = len(assets) == 5 and all(a is not None for a in assets) and all(assets[i] < assets[i - 1] for i in range(1, 5))
        sofr_iorb = (values.get("sofr") - values.get("iorb")) * 100 if values.get("sofr") is not None and values.get("iorb") is not None else None
        repo_code, repo_details = repo_structure(repo, date.fromisoformat(row["as_of"]))
        tbill_code, tbill_details = tbill_structure(tbill_events, date.fromisoformat(row["as_of"]))
        q, q_breakdown = score_q(values.get("on_rrp"), values.get("reserves"), reserve_change, shrinking)
        p, p_breakdown = score_p(values.get("curve_2s10s"), values.get("ois_1y1y"), values.get("tips_10y"))
        g, g_breakdown = score_g(sofr_iorb, repo_code, tbill_code)
        total = q + p + g if None not in (q, p, g) else None
        metrics = {**values, "reserve_change": reserve_change, "sofr_iorb": sofr_iorb,
                   "repo": repo_code, "tbill": tbill_code}
        labels = {"on_rrp": trend_tag(values.get("on_rrp"), prior.get("on_rrp"), .01),
                  "reserves": trend_tag(values.get("reserves"), prior.get("reserves"), .01),
                  "curve_2s10s": trend_tag(values.get("curve_2s10s"), prior.get("curve_2s10s"), 2),
                  "ois_1y1y": trend_tag(values.get("ois_1y1y"), prior.get("ois_1y1y"), .01),
                  "tips_10y": trend_tag(values.get("tips_10y"), prior.get("tips_10y"), .01),
                  "sofr_iorb": trend_tag(sofr_iorb, prior.get("sofr_iorb"), .5),
                  "repo": repo_details["label"] if repo_details else "待核验",
                  "tbill": tbill_details["label"] if tbill_details else "待核验"}
        output.append({"week": row["week"], "as_of": row["as_of"], "metrics": metrics, "labels": labels,
                       "scores": {"q": q, "p": p, "g": g, "total": total, "status": status_for_score(total),
                                  "breakdown": {"q": q_breakdown, "p": p_breakdown, "g": g_breakdown}},
                       "signals": {"balance_sheet_shrinking": shrinking, "repo": repo_details, "tbill": tbill_details}})
    return output


def write_snapshot(path: Path, payload, previous):
    comparable = lambda x: {k: v for k, v in x.items() if k != "generated_at"}
    if comparable(previous) == comparable(payload):
        return False
    payload["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "data.json")
    parser.add_argument("--previous", type=Path, help="read the prior snapshot from a separate path before writing output")
    parser.add_argument("--days", type=int, default=1095)
    parser.add_argument("--skip-yahoo", action="store_true", help="keep the prior Yahoo series while refreshing non-Yahoo sources")
    args = parser.parse_args()
    if args.days < 30:
        parser.error("--days must be at least 30")
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=args.days)
    previous_path = args.previous or args.output
    previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.exists() else {}
    series = {}
    old = previous.get("series", {})
    yahoo = {}
    if args.skip_yahoo:
        print("Skipping Yahoo; retaining prior macro histories...", flush=True)
    else:
        print("Fetching Yahoo daily closes...", flush=True)
        try:
            yahoo = fetch_yahoo(start, end)
        except Exception as exc:
            print(f"::warning::Yahoo batch failed ({type(exc).__name__})")
    for key, (symbol, name, unit) in YAHOO.items():
        meta = {"name": name, "symbol": symbol, "unit": unit, "frequency": "daily",
                "source": "Yahoo Finance / yfinance", "stale_days": 1 if key in {"btc", "eth"} else 7,
                "source_url": f"https://finance.yahoo.com/quote/{symbol}/",
                "note": "日线复权收盘；期货为连续近月合约，换月可能跳变" if key in {"oil", "gold"} else "日线复权收盘"}
        if key not in {"btc", "eth"}:
            meta["stale_business_days"] = 1
        series[key] = make_series(meta, yahoo.get(key, []), old.get(key, {}), start, end,
                                  "Yahoo 暂不可用")
    treasury = {}
    treasury_attempted = False
    # With no API key, Treasury's official feed avoids FRED CSV blocking of hosted runners.
    if not os.getenv("FRED_API_KEY"):
        treasury_attempted = True
        print("Fetching official Treasury nominal and real rates...", flush=True)
        try:
            treasury = fetch_treasury_rates(start, end)
        except Exception as exc:
            print(f"::warning::Treasury unavailable ({type(exc).__name__}); trying FRED")
    for key, (symbol, name) in FRED.items():
        error, points = None, []
        from_treasury = bool(treasury.get(key)) and not os.getenv("FRED_API_KEY")
        if from_treasury:
            points = treasury[key]
        else:
            try:
                points = fetch_fred_series(symbol, start, end)
            except Exception as exc:
                error = f"FRED 暂不可用（{type(exc).__name__}）"
        if not points:
            if not treasury_attempted:
                treasury_attempted = True
                try:
                    treasury = fetch_treasury_rates(start, end)
                except Exception as exc:
                    print(f"::warning::Treasury fallback failed ({type(exc).__name__})")
            points = treasury.get(key, [])
            from_treasury = bool(points)
        meta = {"name": name, "symbol": symbol, "unit": "%", "frequency": "daily",
                "source": "FRED", "source_url": f"https://fred.stlouisfed.org/series/{symbol}",
                "note": "工作日发布；收益率单位为百分比"}
        if from_treasury:
            meta.update(source="美国财政部 / U.S. Treasury", source_url="https://home.treasury.gov/treasury-daily-interest-rate-xml-feed",
                        symbol={"us10y": "BC_10YEAR", "real10y": "TC_10YEAR", "breakeven10y": "BC_10YEAR - TC_10YEAR"}[key],
                        note="财政部 10Y 名义减实际收益率，仅取共同日期；非直接下载 FRED T10YIE" if key == "breakeven10y"
                        else "美国财政部官方日度 10Y 收益率，单位为百分比")
        series[key] = make_series(meta, points, old.get(key, {}), start, end, error or "FRED 无有效观测")
    print("Fetching ChinaBond sovereign curve...", flush=True)
    try:
        points, error = fetch_china10y(start, end), None
    except Exception as exc:
        points, error = [], f"中债暂不可用（{type(exc).__name__}）"
    series["cn10y"] = make_series({"name": "中国 10Y 国债收益率", "symbol": "ChinaBond 10Y",
        "unit": "%", "frequency": "daily", "source": "中国债券信息网",
        "source_url": "https://yield.chinabond.com.cn/", "note": "中债国债收益率曲线，10 年期限"},
        points, old.get("cn10y", {}), start, end, error or "中债无有效观测")

    # Dollar liquidity PQG. These inputs are independent of the macro panels so
    # a delayed source never overwrites a previously valid liquidity snapshot.
    liquidity_old = previous.get("liquidity_pqg", {}).get("series", {})
    liquidity_series = {}
    print("Fetching dollar liquidity PQG inputs...", flush=True)
    for key, (symbol, name, unit, scale) in LIQUIDITY_FRED.items():
        try:
            points, error = scale_points(fetch_fred_series(symbol, start, end), scale), None
        except Exception as exc:
            points, error = [], f"FRED 暂不可用（{type(exc).__name__}）"
        liquidity_series[key] = make_series({
            "name": name, "symbol": symbol, "unit": unit,
            "frequency": "weekly" if key in {"reserves", "fed_assets"} else "daily",
            "source": "FRED", "source_url": f"https://fred.stlouisfed.org/series/{symbol}",
            "note": "H.4.1 周三周均值" if key == "reserves" else "H.4.1 周三余额" if key == "fed_assets" else "最近有效交易日值",
        }, points, liquidity_old.get(key, {}), start, end, error or "FRED 无有效观测")

    try:
        points, error = fetch_cme_sr3_forward(start, end), None
    except Exception as exc:
        points, error = [], f"CME SR3 远期代理待核验（{type(exc).__name__}）"
    liquidity_series["ois_1y1y"] = make_series({
        "name": "1Y1Y SOFR 远期代理", "symbol": "SR3 forward proxy", "unit": "%", "frequency": "daily",
        "source": "CME Group SR3", "source_url": "https://www.cmegroup.com/markets/interest-rates/stirs/three-month-sofr.html",
        "note": "以 SR3 结算价推导的 1Y1Y SOFR 远期代理；并非交易终端原始 OIS",
    }, points, liquidity_old.get("ois_1y1y", {}), start, end, error or "CME SR3 远期代理无有效观测")

    repo_series = {}
    repo_old = previous.get("liquidity_pqg", {}).get("repo_series", {})
    for key, mnemonic in OFR_REPO.items():
        try:
            points, error = scale_points(fetch_ofr_series(mnemonic, start, end), 1 / 1_000_000_000_000), None
        except Exception as exc:
            points, error = [], f"OFR 暂不可用（{type(exc).__name__}）"
        repo_series[key] = make_series({
            "name": mnemonic, "symbol": mnemonic, "unit": "T", "frequency": "daily",
            "source": "OFR Short-term Funding Monitor", "source_url": "https://www.financialresearch.gov/short-term-funding-monitor/datasets/repo/",
            "note": "OFR 初步口径；缺口代表未交易或保密处理，不补值",
        }, points, repo_old.get(key, {}), start, end, error or "OFR Repo 无有效观测")

    tbill_old = previous.get("liquidity_pqg", {}).get("tbill_events", {})
    try:
        points, error = fetch_tbill_events(start, end), None
    except Exception as exc:
        points, error = [], f"Treasury 暂不可用（{type(exc).__name__}）"
    tbill_events = make_series({
        "name": "T-bill 净发行事件", "symbol": "Treasury bill auctions", "unit": "USD", "frequency": "event",
        "source": "U.S. Treasury Fiscal Data", "source_url": "https://fiscaldata.treasury.gov/datasets/treasury-securities-auctions-data/",
        "note": "发行日加、到期日减的净供给代理；10 个交易日窗口约用 14 个日历日计算",
    }, points, tbill_old, start, end, error or "Treasury T-bill 事件无有效观测")
    liquidity_weeks = build_liquidity_pqg(liquidity_series, repo_series, tbill_events, end - timedelta(days=1))
    good = sum(s["status"] == "ok" for s in series.values())
    if not good:
        raise RuntimeError("All sources failed; original data.json left untouched")
    problems = [key for key, item in series.items() if item["status"] != "ok" or item["stale"]]
    payload = {"schema_version": 2, "history_days": args.days,
               "price_basis": "Yahoo adjusted daily close; no forward fill; current UTC day excluded",
               "series": series,
               "liquidity_pqg": {
                   "as_of": liquidity_weeks[-1]["as_of"] if liquidity_weeks else None,
                   "weeks": liquidity_weeks, "series": liquidity_series, "repo_series": repo_series,
                   "tbill_events": tbill_events,
                   "methodology": {
                       "weekly_selection": "日度优先周五、其次周四；H.4.1 使用周三发布值",
                       "ois": "CME SR3 推导的 1Y1Y SOFR 远期代理；无可验证结算源则待核验",
                       "repo": "定期占比 10 日变化≥2pp 为定期增多；否则清算占比变化绝对值≥5pp 为结构性分化；其余隔夜偏多",
                       "tbill": "近 14 个日历日净发行≥500 亿美元为强虹吸",
                   },
               }}
    changed = write_snapshot(args.output, payload, previous)
    print(f"Snapshot {'updated' if changed else 'unchanged'}: {good}/{len(series)} sources fetched")
    if problems:
        print(f"::warning::Missing, cached or delayed series: {', '.join(problems)}")
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"### Macro data\nFetched: {good}/{len(series)}. Changed: {changed}.\n\n")
            handle.write("|Series|Status|Latest observation|\n|---|---|---|\n")
            for key, item in series.items():
                handle.write(f"|{key}|{item['status']}{' / delayed' if item['stale'] else ''}|{item['last_date'] or '—'}|\n")


if __name__ == "__main__":
    main()
