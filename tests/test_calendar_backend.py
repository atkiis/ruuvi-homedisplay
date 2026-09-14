import json
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import calendar_backend


ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Ruuvi Home Display//EN
BEGIN:VEVENT
UID:one@example.test
DTSTART;TZID=Europe/Helsinki:20260912T093000
DTEND;TZID=Europe/Helsinki:20260912T103000
SUMMARY:Appointment
CATEGORIES:green
END:VEVENT
BEGIN:VEVENT
UID:all-day@example.test
DTSTART;VALUE=DATE:20260913
SUMMARY:Holiday
END:VEVENT
END:VCALENDAR
"""

RECURRING_ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Ruuvi Home Display//EN
BEGIN:VEVENT
UID:recurring@example.test
DTSTART;TZID=Europe/Helsinki:20260912T080000
DTEND;TZID=Europe/Helsinki:20260912T083000
RRULE:FREQ=DAILY;COUNT=3
SUMMARY:Daily check
END:VEVENT
END:VCALENDAR
"""


class CalendarBackendTests(unittest.TestCase):
    def test_parse_normalizes_timed_and_all_day_events(self):
        with patch.object(calendar_backend, "_window", return_value=(
            datetime.fromisoformat("2026-09-12T00:00:00+03:00"),
            datetime.fromisoformat("2026-09-14T00:00:00+03:00"),
        )):
            events = calendar_backend.parse_ics(ICS)

        self.assertEqual([event["title"] for event in events], ["Appointment", "Holiday"])
        self.assertEqual(events[0]["category"], "green")
        self.assertEqual(events[0]["time"], "2026-09-12T09:30:00+03:00")
        self.assertEqual(events[1]["time"], "2026-09-13T00:00:00+03:00")
        self.assertTrue(events[1]["all_day"])

    def test_invalid_input_does_not_replace_previous_calendar(self):
        with TemporaryDirectory() as directory, patch.object(
            calendar_backend.config,
            "CALENDAR_STORAGE_PATH",
            str(Path(directory) / "calendar.json"),
        ):
            calendar_backend.import_ics(ICS)
            previous = json.loads(Path(directory, "calendar.json").read_text())

            with self.assertRaises(calendar_backend.CalendarImportError):
                calendar_backend.import_ics(b"not an ics file")

            self.assertEqual(
                json.loads(Path(directory, "calendar.json").read_text()),
                previous,
            )

    def test_persistence_and_status(self):
        with TemporaryDirectory() as directory, patch.object(
            calendar_backend.config,
            "CALENDAR_STORAGE_PATH",
            str(Path(directory) / "calendar.json"),
        ), patch.object(calendar_backend, "_window", return_value=(
            datetime.fromisoformat("2026-09-12T00:00:00+03:00"),
            datetime.fromisoformat("2026-09-14T00:00:00+03:00"),
        )):
            calendar_backend.import_ics(ICS)
            status = calendar_backend.status()
            self.assertTrue(status["uploaded"])
            self.assertEqual(status["event_count"], 2)
            self.assertIsNotNone(status["updated_at"])
            self.assertEqual(len(calendar_backend.load_events()), 2)

    def test_empty_input_is_rejected(self):
        with self.assertRaises(calendar_backend.CalendarImportError):
            calendar_backend.parse_ics(b" ")

    def test_upload_category_overrides_ics_categories(self):
        with patch.object(calendar_backend, "_window", return_value=(
            datetime.fromisoformat("2026-09-12T00:00:00+03:00"),
            datetime.fromisoformat("2026-09-14T00:00:00+03:00"),
        )):
            events = calendar_backend.parse_ics(ICS, "yellow")

        self.assertEqual({event["category"] for event in events}, {"yellow"})

    def test_invalid_upload_category_is_rejected(self):
        with self.assertRaises(calendar_backend.CalendarImportError):
            calendar_backend.parse_ics(ICS, "purple")

    def test_daily_recurrence_is_bounded_to_the_display_window(self):
        with patch.object(calendar_backend, "_window", return_value=(
            datetime.fromisoformat("2026-09-12T00:00:00+03:00"),
            datetime.fromisoformat("2026-09-14T00:00:00+03:00"),
        )):
            events = calendar_backend.parse_ics(RECURRING_ICS)

        self.assertEqual(len(events), 2)
        self.assertEqual(
            [event["time"] for event in events],
            ["2026-09-12T08:00:00+03:00", "2026-09-13T08:00:00+03:00"],
        )


if __name__ == "__main__":
    unittest.main()