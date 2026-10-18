import unittest
from unittest.mock import patch

import config
import epaper
import epaper_palette
import i18n


class E1002RenderTests(unittest.TestCase):
    def test_calendar_layout_is_800x480(self):
        img = epaper.render("e1002")
        self.assertEqual(img.size, (800, 480))

    def test_output_uses_only_panel_colours(self):
        img = epaper.render("e1002")
        colours = {rgb for _, rgb in img.getcolors(maxcolors=100000)}
        self.assertLessEqual(colours, set(epaper_palette.PALETTE))

    def test_render_survives_unavailable_price_data(self):
        with patch("calendar_view.electricity.get_summary", return_value={}):
            self.assertEqual(epaper.render("e1002").size, (800, 480))

    def test_png_endpoint_payload_is_png(self):
        epaper.invalidate_render_cache()
        self.assertTrue(epaper.render_png("e1002").startswith(b"\x89PNG"))


class PaletteTests(unittest.TestCase):
    def test_snap_maps_off_palette_colours_to_palette(self):
        from PIL import Image
        img = Image.new("RGB", (4, 4), (200, 10, 10))
        self.assertEqual(epaper_palette.snap(img).getpixel((0, 0)), epaper_palette.RED)


class I18nTests(unittest.TestCase):
    def test_finnish_and_english(self):
        with patch.object(config, "EPAPER_LANGUAGE", "fi"):
            self.assertEqual(i18n.t("today"), "Tänään")
            self.assertEqual(i18n.event_count(1), "1 tapahtuma")
        with patch.object(config, "EPAPER_LANGUAGE", "en"):
            self.assertEqual(i18n.t("today"), "Today")
            self.assertEqual(i18n.event_count(3), "3 events")

    def test_unknown_language_falls_back_to_finnish(self):
        with patch.object(config, "EPAPER_LANGUAGE", "xx"):
            self.assertEqual(i18n.language(), "fi")


if __name__ == "__main__":
    unittest.main()


class HourlyCacheTests(unittest.TestCase):
    def test_cache_does_not_serve_previous_hour(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Helsinki")
        calls = []

        def fake_render():
            from PIL import Image
            calls.append(1)
            return Image.new("RGB", (8, 8), (len(calls), 0, 0))

        epaper.invalidate_render_cache()
        for hour in (13, 13, 14):
            with patch.object(epaper, "datetime") as dt:
                dt.now.return_value = datetime(2026, 10, 18, hour, 59 if hour == 13 else 0, 30, tzinfo=tz)
                epaper._cached_render_png("t", fake_render, invert=False)
        self.assertEqual(len(calls), 2)
