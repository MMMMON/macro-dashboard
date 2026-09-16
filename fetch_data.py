"""Daily static snapshot. No credentials or upstream requests are exposed to browsers."""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent
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
    last_date = points[-1]["time"] if points else None
    stale = bool(last_date and (end - date.fromisoformat(last_date)).days > meta.get("stale_days", 7))
    return {**meta, "status": status, "stale": stale, "last_date": last_date,
            "message": error if status != "ok" else None, "data": points}


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
    parser.add_argument("--days", type=int, default=1095)
    args = parser.parse_args()
    if args.days < 30:
        parser.error("--days must be at least 30")
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=args.days)
    previous = json.loads(args.output.read_text(encoding="utf-8")) if args.output.exists() else {}
    series = {}
    old = previous.get("series", {})
    try:
        yahoo = fetch_yahoo(start, end)
    except Exception as exc:
        print(f"::warning::Yahoo batch failed ({type(exc).__name__})")
        yahoo = {}
    for key, (symbol, name, unit) in YAHOO.items():
        meta = {"name": name, "symbol": symbol, "unit": unit, "frequency": "daily",
                "source": "Yahoo Finance / yfinance", "stale_days": 3 if key in {"btc", "eth"} else 7,
                "source_url": f"https://finance.yahoo.com/quote/{symbol}/",
                "note": "日线复权收盘；期货为连续近月合约，换月可能跳变" if key in {"oil", "gold"} else "日线复权收盘"}
        series[key] = make_series(meta, yahoo.get(key, []), old.get(key, {}), start, end,
                                  "Yahoo 暂不可用")
    for key, (symbol, name) in FRED.items():
        error, points = None, []
        try:
            points = fetch_fred_series(symbol, start, end)
        except Exception as exc:
            error = f"FRED 暂不可用（{type(exc).__name__}）"
        meta = {"name": name, "symbol": symbol, "unit": "%", "frequency": "daily",
                "source": "FRED", "source_url": f"https://fred.stlouisfed.org/series/{symbol}",
                "note": "工作日发布；收益率单位为百分比"}
        series[key] = make_series(meta, points, old.get(key, {}), start, end, error or "FRED 无有效观测")
    try:
        points, error = fetch_china10y(start, end), None
    except Exception as exc:
        points, error = [], f"中债暂不可用（{type(exc).__name__}）"
    series["cn10y"] = make_series({"name": "中国 10Y 国债收益率", "symbol": "ChinaBond 10Y",
        "unit": "%", "frequency": "daily", "source": "中国债券信息网",
        "source_url": "https://yield.chinabond.com.cn/", "note": "中债国债收益率曲线，10 年期限"},
        points, old.get("cn10y", {}), start, end, error or "中债无有效观测")
    good = sum(s["status"] == "ok" for s in series.values())
    if not good:
        raise RuntimeError("All sources failed; original data.json left untouched")
    problems = [key for key, item in series.items() if item["status"] != "ok" or item["stale"]]
    payload = {"schema_version": 1, "history_days": args.days,
               "price_basis": "Yahoo adjusted daily close; no forward fill; current UTC day excluded",
               "series": series}
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
