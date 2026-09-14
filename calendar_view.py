"""
calendar_view.py – Data for the 7.3" colour calendar view.

Shared by the HTML preview (``/epaper-e1002``) and the Pillow renderer that
produces ``/epaper-e1002.png`` for the panel, so both always show the same
agenda and the same electricity summary.
"""

from datetime import datetime, timedelta

import config
import calendar_backend
import electricity

# Shown when the spot-price API has not produced usable data yet.
FALLBACK_SUMMARY = {
    "unit": "c/kWh",
    "average": 7.8,
    "peak": {"price": 18.5, "start": "17:00", "end": "19:00"},
    "cheapest": {"price": 2.1, "start": "01:00", "end": "05:00"},
}


def summary() -> dict:
    data = electricity.get_summary()
    if data.get("average") is None or not data.get("peak") or not data.get("cheapest"):
        return FALLBACK_SUMMARY
    return data


def _category(raw) -> str:
    category = str(raw or "").lower()
    return category if category in config.CALENDAR_CATEGORY_COLORS else "blue"


def sections() -> list[dict]:
    """Group configured events into TODAY / TOMORROW rows."""
    today = datetime.now().date()
    day_of = {today: "today", today + timedelta(days=1): "tomorrow"}
    grouped: dict[str, list[dict]] = {"today": [], "tomorrow": []}

    uploaded_events = calendar_backend.load_events()
    configured_events = uploaded_events if uploaded_events is not None else getattr(config, "CALENDAR_EVENTS", [])
    for entry in configured_events or []:
        if not isinstance(entry, dict):
            continue
        try:
            start = datetime.fromisoformat(str(entry.get("time")))
        except ValueError:
            continue
        day = day_of.get(start.date())
        title = str(entry.get("title", "")).strip()
        if not day or not title:
            continue
        try:
            end = datetime.fromisoformat(str(entry.get("end")))
        except ValueError:
            end = start + timedelta(hours=1)
        grouped[day].append(
            {
                "start": start.strftime("%H:%M"),
                "end": end.strftime("%H:%M"),
                "category": _category(entry.get("category")),
                "title": title,
            }
        )

    if not grouped["today"] and not grouped["tomorrow"]:
        for entry in getattr(config, "CALENDAR_DEMO_EVENTS", []) or []:
            day = str(entry.get("day", "")).lower()
            if day in grouped:
                grouped[day].append(
                    {
                        "start": entry.get("start", ""),
                        "end": entry.get("end", ""),
                        "category": _category(entry.get("category")),
                        "title": str(entry.get("title", "")),
                    }
                )

    for events in grouped.values():
        events.sort(key=lambda e: e["start"])

    return [
        {
            "title": "Today",
            "date": today.strftime("%a %-d %b"),
            "events": grouped["today"],
        },
        {
            "title": "Tomorrow",
            "date": (today + timedelta(days=1)).strftime("%a %-d %b"),
            "events": grouped["tomorrow"],
        },
    ]
