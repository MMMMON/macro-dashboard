#!/usr/bin/env python3
"""Build the weekly Forex Factory calendar snapshot used by the calendar page."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
OUTPUT = Path(__file__).with_name("economic_calendar.json")
BERLIN = ZoneInfo("Europe/Berlin")
SHANGHAI = ZoneInfo("Asia/Shanghai")
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
COUNTRY_NAMES = {
    "USD": "美国", "EUR": "欧元区", "GBP": "英国", "AUD": "澳大利亚",
    "JPY": "日本", "CHF": "瑞士", "NZD": "新西兰", "CNY": "中国",
}
WEEKDAYS = "一二三四五六日"


def category(title: str) -> str:
    if re.search(r"cash rate|policy rate|rate statement|rate decision|interest rate|monetary policy", title, re.I):
        return "policy"
    if re.search(r"cpi|pce|inflation|price index", title, re.I):
        return "inflation"
    if re.search(r"non-farm|employment|unemployment|hourly earnings|jobless|claims|adp|jolts|payroll", title, re.I):
        return "labor"
    if re.search(r"pmi|gdp|ism|industrial production|retail sales|business climate|factory orders", title, re.I):
        return "growth"
    return "other"


def chinese_title(title: str) -> str:
    replacements = [
        (r"Non-Farm Employment Change", "非农就业人数"),
        (r"Core PCE Price Index m/m", "核心 PCE 物价指数"),
        (r"Trimmed Mean CPI m/m", "截尾均值 CPI"),
        (r"Average Hourly Earnings m/m", "平均时薪"),
        (r"Unemployment Rate", "失业率"),
        (r"Cash Rate", "现金利率决议"),
        (r"Rate Statement", "利率声明"),
        (r"Manufacturing PMI", "制造业 PMI"),
        (r"Services PMI", "服务业 PMI"),
        (r"CPI m/m", "CPI 月率"),
        (r"CPI y/y", "CPI 年率"),
        (r"GDP q/q", "GDP 季率"),
    ]
    result = title
    for pattern, replacement in replacements:
        result = re.sub(pattern, replacement, result, flags=re.I)
    return result


def event_time_label(event: dict) -> str:
    instant = datetime.fromisoformat(str(event["timestamp"]).replace("Z", "+00:00")).astimezone(BERLIN)
    return f"周{WEEKDAYS[instant.weekday()]} {instant:%H:%M}"


def value_note(event: dict) -> str:
    forecast = str(event.get("forecast") or "—")
    previous = str(event.get("previous") or "—")
    if forecast != "—" and previous != "—":
        return f"预期 {forecast}，前值 {previous}"
    if forecast != "—":
        return f"预期 {forecast}"
    return "关注声明与市场预期差"


def event_priority(event: dict) -> int:
    title = str(event.get("title", ""))
    priorities = [
        (r"non-farm|payroll", 100), (r"core pce", 95), (r"cpi", 90),
        (r"gdp", 80), (r"unemployment rate", 75), (r"pmi|ism", 65),
    ]
    score = next((weight for pattern, weight in priorities if re.search(pattern, title, re.I)), 10)
    return score + {"High": 30, "Medium": 20, "Low": 10}.get(str(event.get("impact")), 0)


def build_focus(events: list[dict]) -> dict:
    weights = {"High": 3, "Medium": 2, "Low": 1}
    stats = {name: {"max": 0, "High": 0, "Medium": 0, "Low": 0} for name in ("policy", "inflation", "labor", "growth")}
    for event in events:
        group = category(str(event["title"]))
        if group in stats:
            impact = str(event.get("impact"))
            weight = weights.get(impact, 1)
            stats[group]["max"] = max(stats[group]["max"], weight)
            stats[group][impact] += 1
    scores = {
        name: values["max"] * 10 + values["High"] * 3 + values["Medium"] * 2 + min(values["Low"], 3)
        for name, values in stats.items()
    }
    ranked = sorted(scores, key=scores.get, reverse=True)
    pair = set(ranked[:2])
    if pair == {"inflation", "labor"}:
        main_title = "通胀与就业双重定价"
    elif pair == {"inflation", "growth"}:
        main_title = "增长—通胀再平衡"
    elif ranked[0] == "policy":
        main_title = "央行政策周"
    elif ranked[0] == "growth":
        main_title = "增长动能集中验证"
    elif ranked[0] == "labor":
        main_title = "就业市场压力测试"
    else:
        main_title = "通胀路径再定价"
    main_events = []
    for group in ranked[:2]:
        choices = [event for event in events if category(str(event["title"])) == group]
        if choices:
            main_events.append(max(choices, key=event_priority))
    main_detail = "；".join(
        f"{event_time_label(event)} {COUNTRY_NAMES.get(str(event['country']), event['country'])}{chinese_title(str(event['title']))}"
        for event in main_events
    ) or "关注高影响数据与预期差"

    policy_events = [event for event in events if category(str(event["title"])) == "policy"]
    policy_events.sort(key=lambda event: (weights.get(str(event.get("impact")), 1), "rate" in str(event["title"]).lower()), reverse=True)
    if policy_events:
        chosen = policy_events[0]
        policy_title = f"{COUNTRY_NAMES.get(str(chosen['country']), chosen['country'])}{chinese_title(str(chosen['title']))} · {event_time_label(chosen)}"
        policy_detail = value_note(chosen)
    else:
        policy_title = "本周无主要央行利率决议"
        policy_detail = "重点转向数据对政策预期的影响"

    candidates = [event for event in events if event.get("impact") == "High" and category(str(event["title"])) != "policy"]
    chosen = max(candidates or events, key=event_priority)
    special_title = f"{COUNTRY_NAMES.get(str(chosen['country']), chosen['country'])}{chinese_title(str(chosen['title']))} · {event_time_label(chosen)}"
    special_detail = value_note(chosen)
    return {
        "mainLine": {"title": main_title, "detail": main_detail},
        "policyFocus": {"title": policy_title, "detail": policy_detail},
        "specialAttention": {"title": special_title, "detail": special_detail},
    }


def should_keep(title: str, impact: str) -> bool:
    return bool(
        impact == "High"
        or (impact == "Medium" and IMPORTANT.search(title))
        or (impact == "Low" and LOW_PRIORITY.search(title))
    )


def target_week(now: datetime) -> tuple[datetime, datetime]:
    local = now.astimezone(BERLIN)
    china = now.astimezone(SHANGHAI)
    switch_to_next_week = local.weekday() == 6 or china.weekday() == 6
    days_to_monday = 7 - local.weekday() if switch_to_next_week else -local.weekday()
    monday = (local + timedelta(days=days_to_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday, monday + timedelta(days=6)


class FeedUnavailable(RuntimeError):
    pass


class FeedFormatError(RuntimeError):
    pass


class NoMatchingEvents(RuntimeError):
    pass


def dated_rows(rows: object) -> list[datetime]:
    if not isinstance(rows, list) or not rows:
        raise FeedFormatError("Feed must be a nonempty list of events")
    dates = []
    for row in rows:
        if not isinstance(row, dict):
            raise FeedFormatError("Feed event must be an object")
        try:
            instant = datetime.fromisoformat(str(row.get("date", "")).replace("Z", "+00:00"))
            if instant.tzinfo is None:
                raise ValueError("Missing timezone")
            if not isinstance(row.get("country"), str) or not row["country"] or not isinstance(row.get("title"), str) or not row["title"].strip() or row.get("impact") not in {"High", "Medium", "Low", "Holiday"}:
                raise ValueError("Invalid event fields")
            dates.append(instant.astimezone(BERLIN))
        except ValueError as exc:
            raise FeedFormatError(f"Invalid event date or fields: {exc}") from exc
    return dates


def fetch_feed() -> list[dict]:
    request = urllib.request.Request(
        FEED_URL,
        headers={"User-Agent": "Mozilla/5.0 WeeklyEconomicCalendar/1.0"},
    )
    error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                try:
                    rows = json.load(response)
                except (ValueError, UnicodeError) as exc:
                    raise FeedFormatError("Feed is not valid JSON") from exc
                dated_rows(rows)
                return rows
        except FeedFormatError:
            raise
        except Exception as exc:  # pragma: no cover - network retry path
            error = exc
            time.sleep(2 ** attempt)
    raise FeedUnavailable(f"Forex Factory feed unavailable: {error}")


def normalize(rows: list[dict], now: datetime) -> dict:
    dated_rows(rows)
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
        raise NoMatchingEvents("Feed contained no matching target-week events; keeping the previous file")
    return {
        "weekStart": week_start.date().isoformat(),
        "weekEnd": week_end.date().isoformat(),
        "generatedAt": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "timezone": "Europe/Berlin",
        "source": FEED_URL,
        "events": events,
        "focus": build_focus(events),
    }


def report(status: str, detail: str) -> None:
    message = f"{status}: {detail}"
    print(message)
    if status in {"WAITING", "FAILED"}:
        print(f"::{'warning' if status == 'WAITING' else 'error'}::{message}")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"### Economic calendar: {status}\n\n{detail}\n\n")


def valid_cache(now: datetime) -> dict | None:
    try:
        data = json.loads(OUTPUT.read_text(encoding="utf-8"))
        start, end = target_week(now)
        if data.get("weekStart") != start.date().isoformat() or data.get("weekEnd") != end.date().isoformat():
            return None
        events = data["events"]
        normalized = normalize([dict(e, date=e["timestamp"]) for e in events], now)
        if len(normalized["events"]) != len(events):
            return None
        return data
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
        return None


def update(now: datetime) -> None:
    try:
        rows = fetch_feed()
        try:
            data = normalize(rows, now)
        except NoMatchingEvents:
            start, _ = target_week(now)
            dates = dated_rows(rows)
            previous_only = all(start.date() - timedelta(days=7) <= d.date() < start.date() for d in dates)
            deadline = datetime.combine(start.date(), datetime.min.time(), SHANGHAI) + timedelta(hours=8)
            if previous_only and now < deadline:
                report("WAITING", f"Upstream still serves the previous week; target {start.date()}. Existing file preserved. Retry until {deadline.isoformat()}.")
                return
            raise
        if OUTPUT.exists():
            try:
                old = json.loads(OUTPUT.read_text(encoding="utf-8"))
            except (ValueError, UnicodeError):
                old = None
            if isinstance(old, dict) and {k: v for k, v in old.items() if k != "generatedAt"} == {k: v for k, v in data.items() if k != "generatedAt"}:
                report("UNCHANGED", f"Validated {len(data['events'])} events for {data['weekStart']}; original timestamp preserved.")
                return
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=OUTPUT.parent, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, OUTPUT)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        report("UPDATED", f"Wrote {len(data['events'])} events for {data['weekStart']} through {data['weekEnd']}.")
    except (RuntimeError, OSError) as exc:
        cache = valid_cache(now)
        detail = "Validated target-week cache retained with original timestamp." if cache else "Existing file preserved; no valid target-week cache."
        report("FAILED", f"{type(exc).__name__}: {exc}. {detail}")
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scheduled", action="store_true", help="Compatibility flag; delayed executions also run")
    parser.parse_args()
    update(datetime.now(timezone.utc))


if __name__ == "__main__":
    main()
