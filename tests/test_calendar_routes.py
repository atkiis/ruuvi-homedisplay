import io
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

import calendar_backend
from app import app


class CalendarRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)

    def setUp(self):
        self.client = app.test_client()

    def test_upload_requires_ics_filename(self):
        response = self.client.post(
            "/api/calendar/upload",
            data={"calendar": (io.BytesIO(b"BEGIN:VCALENDAR"), "calendar.txt")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(".ics", response.get_json()["error"])

    @patch("app.epaper.invalidate_render_cache")
    @patch("app.calendar_backend.import_ics", return_value=[{"title": "Meeting"}])
    def test_valid_upload_invalidates_image_cache(self, import_ics, invalidate_cache):
        response = self.client.post(
            "/api/calendar/upload",
            data={
                "calendar": (io.BytesIO(b"trusted test input"), "calendar.ics"),
                "category": "green",
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json(), {"uploaded": True, "event_count": 1})
        import_ics.assert_called_once_with(b"trusted test input", "green")
        invalidate_cache.assert_called_once_with()

    def test_upload_requires_category(self):
        response = self.client.post(
            "/api/calendar/upload",
            data={"calendar": (io.BytesIO(b"trusted test input"), "calendar.ics")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("category", response.get_json()["error"])

    def test_add_category(self):
        with TemporaryDirectory() as directory, patch.object(
            calendar_backend.config,
            "CALENDAR_STORAGE_PATH",
            f"{directory}/calendar.json",
        ):
            response = self.client.post(
                "/api/calendar/categories",
                json={"label": "Family", "palette": "yellow"},
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["label"], "Family")
        self.assertEqual(response.get_json()["palette"], "yellow")

    def test_upload_rejects_missing_file(self):
        response = self.client.post("/api/calendar/upload")

        self.assertEqual(response.status_code, 400)
        self.assertIn("Choose", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()