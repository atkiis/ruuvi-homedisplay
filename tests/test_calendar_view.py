import unittest
from unittest.mock import patch

import calendar_view


def _section(title, events):
    return {"title": title, "date": "date", "events": events}


def _event(title):
    return {"start": "10:00", "end": "12:00", "category": "blue", "title": title}


class CalendarViewTests(unittest.TestCase):
    def test_events_are_removed_one_hour_after_their_end(self):
        with patch.object(calendar_view, "_now", return_value=calendar_view.datetime.fromisoformat(
            "2026-09-14T14:00:00+03:00"
        )), patch.object(calendar_view.calendar_backend, "load_events", return_value=[
            {"time": "2026-09-14T09:00:00+03:00", "end": "2026-09-14T10:00:00+03:00",
             "category": "blue", "title": "Expired"},
            {"time": "2026-09-14T11:00:00+03:00", "end": "2026-09-14T13:30:00+03:00",
             "category": "blue", "title": "Within grace"},
        ]):
            displayed = calendar_view.sections()

        self.assertEqual([event["title"] for event in displayed[0]["events"]], ["Within grace"])

    def test_busy_today_uses_one_dense_section(self):
        sections = [_section("Today", [_event(str(index)) for index in range(5)]),
                    _section("Tomorrow", [_event("tomorrow")])]

        with patch.object(calendar_view, "sections", return_value=sections):
            displayed = calendar_view.display_sections()

        self.assertEqual(len(displayed), 1)
        self.assertEqual(displayed[0]["title"], "Today")
        self.assertEqual(displayed[0]["layout"], "dense")

    def test_empty_today_promotes_tomorrow_to_expanded_section(self):
        sections = [_section("Today", []), _section("Tomorrow", [_event("tomorrow")])]

        with patch.object(calendar_view, "sections", return_value=sections):
            displayed = calendar_view.display_sections()

        self.assertEqual(len(displayed), 1)
        self.assertEqual(displayed[0]["title"], "Tomorrow")
        self.assertEqual(displayed[0]["layout"], "expanded")

    def test_normal_schedule_keeps_both_days(self):
        sections = [_section("Today", [_event("today")]), _section("Tomorrow", [_event("tomorrow")])]

        with patch.object(calendar_view, "sections", return_value=sections):
            displayed = calendar_view.display_sections()

        self.assertEqual([section["layout"] for section in displayed], ["normal", "normal"])


if __name__ == "__main__":
    unittest.main()