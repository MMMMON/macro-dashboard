"""Refresh OIS inputs and the SR3-derived 1Y1Y proxy before the slower full update."""
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
    liquidity_series = payload.get("liquidity_pqg", {}).get("series", {})
    changed = False
    for series_key, (stat_id, tenor) in feed.MACROMICRO_OIS.items():
        old_ois = liquidity_series.get(series_key, {})
        try:
            points = feed.fetch_macromicro_series(stat_id, start, end)
        except Exception as exc:
            print(f"MacroMicro OIS {tenor} unchanged ({type(exc).__name__})")
            continue
        updated_ois = feed.make_series({
            "name": f"美国 OIS {tenor}", "symbol": f"MacroMicro {stat_id}", "unit": "%",
            "frequency": "daily", "source": "MacroMicro",
            "source_url": f"https://en.macromicro.me/series/{stat_id}",
            "stale_business_days": 2,
            "note": "即期起息 OIS 固定端利率；MacroMicro 授权接口。它不是 1Y1Y 远期利率。",
        }, points, old_ois, start, end)
        if updated_ois != old_ois:
            liquidity_series[series_key] = updated_ois
            changed = True
            print(f"MacroMicro OIS {tenor} updated: {updated_ois['last_date']}")

    old = liquidity_series.get("ois_1y1y", {})

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
    else:
        liquidity_series["ois_1y1y"] = updated
        changed = True
        print(f"SR3 proxy updated: {updated['last_date']} {updated['data'][-1]['value']:.6f}")
    if not changed:
        return
    payload["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
