#!/usr/bin/env python3
"""Build the weekly Forex Factory calendar snapshot used by the calendar page."""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
OUTPUT = Path(__file__).with_name("economic_calendar.json")
BERLIN = ZoneInfo("Europe/Berlin")
ALLOWED = {"USD", "EUR", "GBP", "AUD", "JPY", "CHF", "NZD", "CNY"}
IMPORTANT = re.compile(
    r"pmi|cpi|inflation|policy rate|rate statement|rate decision|employment|"
    r"unemployment|gdp|retail sales|industrial production|trade balance|"
    r"business climate|consumer confidence|durable goods|pce|non-farm|"
    r"payroll|jolts|ism|adp|jobless|claims|monetary policy|minutes|"
    r"press conference|money supply|factory orders|housing starts|"
    r"manufacturing index|current account|lending|mortgage approvals",
    re.IGNORECASE,
)
LOW_PRIORITY = re.compile(r"pmi|cpi|inflation|price index", re.IGNORECASE)


def should_keep(title: str, impact: str) -> bool:
    return bool(
        impact == "High"
        or (impact == "Medium" and IMPORTANT.search(title))
        or (impact == "Low" and LOW_PRIORITY.search(title))
    )


def target_week(now: datetime) -> tuple[datetime, datetime]:
    local = now.astimezone(BERLIN)
    days_to_monday = 1 if local.weekday() == 6 else -local.weekday()
    monday = (local + timedelta(days=days_to_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday, monday + timedelta(days=6)


def fetch_feed() -> list[dict]:
    request = urllib.request.Request(
        FEED_URL,
        headers={"User-Agent": "Mozilla/5.0 WeeklyEconomicCalendar/1.0"},
    )
    error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except Exception as exc:  # pragma: no cover - network retry path
            error = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Forex Factory feed unavailable: {error}")


def normalize(rows: list[dict], now: datetime) -> dict:
    week_start, week_end = target_week(now)
    events: list[dict] = []
    for row in rows:
        country = str(row.get("country", ""))
        title = str(row.get("title", ""))
        impact = str(row.get("impact", "Low"))
        if country not in ALLOWED:
            continue
        try:
            instant = datetime.fromisoformat(str(row.get("date", ""))).astimezone(BERLIN)
        except ValueError:
            continue
        if not (week_start.date() <= instant.date() <= week_end.date()):
            continue
        if not should_keep(title, impact):
            continue
        tags = [country]
        if impact == "High":
            tags.append("high")
        if re.search(r"pmi", title, re.IGNORECASE):
            tags.append("pmi")
        if re.search(r"cpi|inflation|price index", title, re.IGNORECASE):
            tags.append("inflation")
        events.append(
            {
                "title": title,
                "country": country,
                "impact": impact,
                "forecast": str(row.get("forecast") or "—"),
                "previous": str(row.get("previous") or "—"),
                "url": "",
                "timestamp": instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "allDay": False,
                "tags": tags,
            }
        )
    events.sort(key=lambda event: event["timestamp"])
    if not events:
        raise RuntimeError("New weekly feed contained no matching events; keeping the previous file")
    return {
        "weekStart": week_start.date().isoformat(),
        "weekEnd": week_end.date().isoformat(),
        "generatedAt": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "timezone": "Europe/Berlin",
        "source": FEED_URL,
        "events": events,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scheduled", action="store_true")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    local = now.astimezone(BERLIN)
    if args.scheduled and not (local.weekday() == 6 and local.hour in {0, 1}):
        print(f"Outside Sunday 00:00/01:00 Berlin window: {local.isoformat()}")
        return
    try:
        data = normalize(fetch_feed(), now)
    except RuntimeError:
        week_start, _ = target_week(now)
        if not OUTPUT.exists():
            raise
        data = json.loads(OUTPUT.read_text(encoding="utf-8"))
        if data.get("weekStart") != week_start.date().isoformat():
            raise
        data["events"] = [
            event for event in data.get("events", [])
            if should_keep(str(event.get("title", "")), str(event.get("impact", "Low")))
        ]
        if not data["events"]:
            raise RuntimeError("Cached weekly calendar contained no matching events")
        print("Feed rate-limited; validated and reused the current-week snapshot")
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(data['events'])} events for {data['weekStart']} through {data['weekEnd']}")


if __name__ == "__main__":
    main()
