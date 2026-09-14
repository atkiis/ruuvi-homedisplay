"""Persisted iCalendar import and display-event normalization."""

import json
import os
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from icalendar import Calendar
import recurring_ical_events

import config


class CalendarImportError(ValueError):
    """Raised when an uploaded calendar cannot be safely imported."""


def _timezone() -> ZoneInfo:
    return ZoneInfo(getattr(config, "CALENDAR_TIMEZONE", "Europe/Helsinki"))


def _storage_path() -> Path:
    return Path(getattr(config, "CALENDAR_STORAGE_PATH", "instance/calendar.json"))


def _as_text(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "to_ical"):
        value = value.to_ical()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _event_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=_timezone()) if value.tzinfo is None else value.astimezone(_timezone())
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=_timezone())
    raise CalendarImportError("VEVENT has an invalid DTSTART or DTEND")


def _normalize_event(component) -> dict:
    title = _as_text(component.get("SUMMARY")).strip()
    if not title:
        raise CalendarImportError("VEVENT is missing SUMMARY")
    if "DTSTART" not in component:
        raise CalendarImportError(f"Event {title!r} is missing DTSTART")

    start_value = component.decoded("DTSTART")
    all_day = isinstance(start_value, date) and not isinstance(start_value, datetime)
    start = _event_datetime(start_value)
    if "DTEND" in component:
        end = _event_datetime(component.decoded("DTEND"))
    else:
        end = start + (timedelta(days=1) if all_day else timedelta(hours=1))
    if end <= start:
        end = start + (timedelta(days=1) if all_day else timedelta(hours=1))

    category = _as_text(component.get("CATEGORIES")).split(",", 1)[0].lower().strip()
    if category not in getattr(config, "CALENDAR_CATEGORY_COLORS", {}):
        category = "blue"
    return {
        "time": start.isoformat(),
        "end": end.isoformat(),
        "title": title,
        "category": category,
        "all_day": all_day,
    }


def _window() -> tuple[datetime, datetime]:
    now = datetime.now(_timezone())
    start = datetime.combine(now.date(), time.min, tzinfo=_timezone())
    days = max(2, int(getattr(config, "CALENDAR_LOOKAHEAD_DAYS", 7)))
    return start, start + timedelta(days=days)


def parse_ics(raw: bytes) -> list[dict]:
    """Parse an ICS document and return normalized events in the display window."""
    if not isinstance(raw, bytes) or not raw.strip():
        raise CalendarImportError("The uploaded calendar file is empty")
    try:
        calendar = Calendar.from_ical(raw)
    except Exception as exc:
        raise CalendarImportError("The uploaded file is not a valid iCalendar file") from exc
    if calendar.get("VERSION") is None:
        raise CalendarImportError("The uploaded file is missing an iCalendar VERSION")

    start, end = _window()
    try:
        components = recurring_ical_events.of(calendar).between(start, end)
    except Exception as exc:
        raise CalendarImportError("The calendar recurrence rules could not be expanded") from exc

    events = []
    for component in components:
        if component.name != "VEVENT":
            continue
        try:
            event = _normalize_event(component)
        except CalendarImportError:
            continue
        event_start = datetime.fromisoformat(event["time"])
        if event_start < end and datetime.fromisoformat(event["end"]) > start:
            events.append(event)
    events.sort(key=lambda event: (event["time"], event["title"]))
    return events


def load_events() -> list[dict] | None:
    try:
        with _storage_path().open(encoding="utf-8") as stream:
            data = json.load(stream)
        events = data.get("events")
        if not isinstance(events, list):
            return None
        return [event for event in events if _is_normalized_event(event)]
    except (OSError, ValueError, TypeError):
        return None


def _is_normalized_event(event) -> bool:
    return (
        isinstance(event, dict)
        and isinstance(event.get("time"), str)
        and isinstance(event.get("end"), str)
        and isinstance(event.get("title"), str)
        and isinstance(event.get("category"), str)
    )


def save_events(events: list[dict]) -> None:
    path = _storage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="calendar-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(
                {"updated_at": datetime.now(_timezone()).isoformat(), "events": events},
                stream,
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def import_ics(raw: bytes) -> list[dict]:
    events = parse_ics(raw)
    save_events(events)
    return events


def clear() -> None:
    try:
        _storage_path().unlink()
    except FileNotFoundError:
        pass


def status() -> dict:
    path = _storage_path()
    events = load_events()
    updated_at = None
    if events is not None:
        try:
            with path.open(encoding="utf-8") as stream:
                updated_at = json.load(stream).get("updated_at")
        except (OSError, ValueError, TypeError):
            pass
    return {
        "uploaded": events is not None,
        "event_count": len(events or []),
        "updated_at": updated_at,
    }