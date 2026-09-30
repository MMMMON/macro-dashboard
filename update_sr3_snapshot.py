"""Refresh only the SR3-derived 1Y1Y proxy before the slower full update."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import fetch_data as feed


def main():
    path = Path(__file__).resolve().parent / "data.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=int(payload.get("history_days", 1095)))
    old = payload.get("liquidity_pqg", {}).get("series", {}).get("ois_1y1y", {})

    source = "CME Group SR3"
    source_url = "https://www.cmegroup.com/markets/interest-rates/stirs/three-month-sofr.html"
    note = "以经审核的 SR3 结算价推导 1Y1Y SOFR 远期代理；并非交易终端原始 OIS"
    incremental = False
    try:
        points = feed.fetch_cme_sr3_forward(start, end)
    except Exception:
        points = feed.fetch_yahoo_sr3_forward(start, end)
        source = "Yahoo Finance（CME 延迟行情）"
        source_url = "https://finance.yahoo.com/"
        incremental = True
        note = (
            "免费兜底：以 Yahoo Finance 提供的 CME SR3 延迟行情，按未来第 13—24 个月"
            "参考期重叠天数加权推导；属于行情代理，并非 CME 官方结算或原始 OIS。"
        )

    series_end = end + timedelta(days=1) if incremental else end
    if incremental and old.get("source") == source:
        points = feed.clean_points(
            [(p["time"], p["value"]) for p in old.get("data", []) + points], start, series_end)
    updated = feed.make_series({
        "name": "1Y1Y SOFR 远期代理", "symbol": "SR3 forward proxy", "unit": "%",
        "frequency": "daily", "source": source, "source_url": source_url, "proxy": True,
        "note": note,
    }, points, old, start, series_end)
    if updated == old:
        print("SR3 proxy unchanged")
        return
    payload["liquidity_pqg"]["series"]["ois_1y1y"] = updated
    payload["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"SR3 proxy updated: {updated['last_date']} {updated['data'][-1]['value']:.6f}")


if __name__ == "__main__":
    main()
