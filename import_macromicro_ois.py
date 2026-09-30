"""Import an authorized MacroMicro OIS chart CSV into the static snapshot."""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import fetch_data as feed


TENOR_SUFFIXES = {
    "ois_1m": ("1month", "1months", "1m"),
    "ois_3m": ("3month", "3months", "3m"),
    "ois_6m": ("6month", "6months", "6m"),
    "ois_1y": ("1year", "1years", "1y"),
    "ois_2y": ("2year", "2years", "2y"),
    "ois_10y": ("10year", "10years", "10y"),
    "ois_30y": ("30year", "30years", "30y"),
}


def normalized_header(value):
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def parse_chart_csv(document):
    reader = csv.DictReader(io.StringIO(document.lstrip("\ufeff")))
    if not reader.fieldnames:
        raise ValueError("CSV 没有表头")
    date_field = next((field for field in reader.fieldnames
                       if normalized_header(field) in {"date", "observationdate", "time"}), None)
    if not date_field:
        raise ValueError("CSV 缺少日期列")
    columns = {}
    for field in reader.fieldnames:
        normalized = normalized_header(field)
        for key, suffixes in TENOR_SUFFIXES.items():
            if any(normalized.endswith(suffix) for suffix in suffixes):
                columns[key] = field
                break
    if len(columns) != len(TENOR_SUFFIXES):
        missing = ", ".join(key.removeprefix("ois_").upper() for key in TENOR_SUFFIXES if key not in columns)
        raise ValueError(f"CSV 缺少 OIS 期限列：{missing}")
    rows = {key: [] for key in TENOR_SUFFIXES}
    for row in reader:
        for key, field in columns.items():
            rows[key].append((row.get(date_field), row.get(field)))
    return rows


def main():
    parser = argparse.ArgumentParser(description="Import an authorized MacroMicro OIS chart CSV")
    parser.add_argument("csv_file", type=Path)
    parser.add_argument("--data-json", type=Path, default=Path(__file__).resolve().parent / "data.json")
    args = parser.parse_args()

    document = args.csv_file.read_text(encoding="utf-8-sig")
    rows = parse_chart_csv(document)
    payload = json.loads(args.data_json.read_text(encoding="utf-8"))
    series = payload["liquidity_pqg"]["series"]
    end = datetime.now(timezone.utc).date() + timedelta(days=1)
    for key, (stat_id, tenor) in feed.MACROMICRO_OIS.items():
        points = feed.clean_points(rows[key], feed.MACROMICRO_OIS_START, end)
        if not points:
            raise ValueError(f"CSV 的 {tenor} 列没有有效数据")
        series[key] = feed.make_series({
            "name": f"美国 OIS {tenor}", "symbol": f"MacroMicro {stat_id}", "unit": "%",
            "frequency": "daily", "source": "MacroMicro（账户授权 CSV）",
            "source_url": f"https://en.macromicro.me/series/{stat_id}",
            "stale_business_days": 2,
            "note": "由账户授权导出的 MacroMicro 历史 CSV 导入；即期起息 OIS，不是 1Y1Y 远期利率。",
        }, points, {}, feed.MACROMICRO_OIS_START, end)
    payload["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    args.data_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    first_dates = [series[key]["data"][0]["time"] for key in feed.MACROMICRO_OIS]
    last_dates = [series[key]["last_date"] for key in feed.MACROMICRO_OIS]
    print(f"Imported MacroMicro OIS history: {min(first_dates)} → {max(last_dates)}")


if __name__ == "__main__":
    main()
