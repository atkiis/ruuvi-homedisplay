import unittest
from unittest.mock import patch

import calendar_view


def _section(title, events):
    return {"title": title, "date": "date", "events": events}


def _event(title):
    return {"start": "10:00", "end": "12:00", "category": "blue", "title": title}


class CalendarViewTests(unittest.TestCase):
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