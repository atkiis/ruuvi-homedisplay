"""
epaper.py – Server-side dashboard renderer for low-resolution e-paper panels.

The Seeed reTerminal E1001 is a 7.5" 800x480 monochrome e-paper device built on
an ESP32-S3. Unlike a Raspberry Pi it cannot run a web browser, so it can't show
the HTML dashboard directly. Instead the device firmware (ESPHome's
``online_image`` component, or an Arduino sketch) periodically downloads a
ready-made image from this server and pushes it straight to the panel.

This module builds that 800x480 image from exactly the same Ruuvi / electricity /
bus data that powers the web dashboard, laid out for a small, slow, 1-bit screen:

* big, high-contrast text (no thin greys that disappear on e-ink)
* no emoji (e-ink fonts can't render them) – short text labels instead
* a compact 24-hour electricity bar chart drawn by hand
* the soonest bus departures only

Endpoints exposed by ``app.py``:
    /epaper.png   – PNG, what most ESPHome setups consume
    /epaper.bmp   – 1-bit BMP, handy for bare Arduino sketches
    /epaper       – HTML preview wrapper for tweaking the layout in a browser
"""

import io
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

import buses
import config
import electricity
import ruuvi_reader

logger = logging.getLogger(__name__)

_HELSINKI = ZoneInfo("Europe/Helsinki")

# Pure black/white plays nicest with 1-bit e-ink. Greys are only used where the
# panel can dither them; on a true mono panel they threshold to black or white.
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GREY = (110, 110, 110)
RED = (200, 0, 0)

# Candidate TrueType fonts. Pillow does not ship a scalable font on every
# platform, so we probe a few common locations and fall back to the bundled
# bitmap default if none are found.
_REGULAR_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/SFNSDisplay.ttf",
    "C:/Windows/Fonts/arial.ttf",
]
_BOLD_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]

_font_cache: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = (size, bold)
    cached = _font_cache.get(key)
    if cached is not None:
        return cached

    candidates = _BOLD_FONTS if bold else _REGULAR_FONTS
    font = None
    for path in candidates:
        try:
            font = ImageFont.truetype(path, size)
            break
        except OSError:
            continue
    if font is None:
        try:
            font = ImageFont.load_default(size)  # Pillow >= 10.1
        except TypeError:
            font = ImageFont.load_default()

    _font_cache[key] = font
    return font


# ---------------------------------------------------------------------------
# Small drawing helpers
# ---------------------------------------------------------------------------

def _text(draw, xy, text, size, *, bold=False, fill=BLACK, anchor="la"):
    draw.text(xy, text, font=_font(size, bold), fill=fill, anchor=anchor)


def _text_width(text, size, bold=False) -> int:
    return int(_font(size, bold).getlength(text))


def _fit(text: str, size: int, max_width: int, bold: bool = False) -> str:
    """Truncate ``text`` with an ellipsis so it fits within ``max_width`` px."""
    if _text_width(text, size, bold) <= max_width:
        return text
    ellipsis = "…"
    while text and _text_width(text + ellipsis, size, bold) > max_width:
        text = text[:-1]
    return (text + ellipsis) if text else ""


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _draw_header(draw, w: int) -> int:
    now = datetime.now(tz=_HELSINKI)
    clock = now.strftime("%H:%M")
    date = now.strftime("%a %-d.%-m.%Y") if hasattr(now, "strftime") else ""
    try:
        date = now.strftime("%a %-d.%-m.%Y")
    except ValueError:  # Windows has no %-d
        date = now.strftime("%a %d.%m.%Y")

    _text(draw, (16, 8), clock, 52, bold=True)
    _text(draw, (190, 30), date, 22, fill=GREY)

    # Current electricity price, right-aligned, as an at-a-glance number.
    prices = electricity.get_prices()
    current = prices.get("current")
    if current:
        price = current["price_with_tax"]
        accent = RED if (config.EPAPER_COLOR and price >= 20) else BLACK
        _text(draw, (w - 16, 8), f"{price:.1f}", 40, bold=True, fill=accent, anchor="ra")
        _text(draw, (w - 16, 52), "c/kWh now", 18, fill=GREY, anchor="ra")

    if config.DEMO_MODE:
        _text(draw, (w - 16, 74), "DEMO", 14, fill=GREY, anchor="ra")

    draw.line([(0, 72), (w, 72)], fill=BLACK, width=2)
    return 72


def _draw_sensors(draw, x: int, y: int, w: int, h: int) -> None:
    _text(draw, (x, y), "SENSORS", 18, bold=True, fill=GREY)
    data = ruuvi_reader.get_latest_data()

    row_y = y + 30
    keys = list(config.RUUVI_TAGS.keys())
    if not keys:
        return
    row_h = min(78, (h - 30) // len(keys))

    for key in keys:
        d = data.get(key, {})
        name = d.get("name", key)
        temp = d.get("temperature")
        hum = d.get("humidity")

        _text(draw, (x, row_y), _fit(name, 22, w - 110, bold=True), 22, bold=True)
        if temp is not None:
            _text(draw, (x + w, row_y - 4), f"{temp:.1f}°", 36, bold=True, anchor="ra")
        else:
            _text(draw, (x + w, row_y - 4), "--", 36, bold=True, fill=GREY, anchor="ra")

        extra = []
        if hum is not None:
            extra.append(f"{hum:.0f}% RH")
        bat = d.get("battery")
        if bat is not None:
            extra.append(f"{bat:.2f}V")
        if extra:
            _text(draw, (x, row_y + 30), "   ".join(extra), 15, fill=GREY)

        row_y += row_h
        if row_y > y + h:
            break


def _draw_electricity(draw, x: int, y: int, w: int, h: int) -> None:
    _text(draw, (x, y), "ELECTRICITY  c/kWh", 18, bold=True, fill=GREY)
    prices = electricity.get_prices()
    hours = prices.get("hours") or []

    chart_top = y + 30
    chart_bottom = y + h - 22
    chart_h = chart_bottom - chart_top

    if not hours:
        _text(draw, (x, chart_top + 10), "No price data", 18, fill=GREY)
        return

    values = [hh["price_with_tax"] for hh in hours]
    vmin = min(values + [0.0])
    vmax = max(values)
    span = (vmax - vmin) or 1.0

    n = len(hours)
    gap = 2
    bar_w = max(4, (w - (n - 1) * gap) // n)
    baseline = chart_bottom

    # Zero line (only meaningful if some prices are negative).
    if vmin < 0:
        zero_y = baseline - int((0 - vmin) / span * chart_h)
        draw.line([(x, zero_y), (x + w, zero_y)], fill=GREY, width=1)
    else:
        zero_y = baseline

    bx = x
    for hh in hours:
        val = hh["price_with_tax"]
        bar_px = int(abs(val - 0) / span * chart_h) if vmin < 0 else int((val - vmin) / span * chart_h)
        bar_px = max(1, bar_px)
        if val >= 0:
            top, bottom = zero_y - bar_px, zero_y
        else:
            top, bottom = zero_y, zero_y + bar_px

        if hh.get("is_current"):
            # Outlined current hour so it stands out on a 1-bit panel.
            draw.rectangle([bx, top, bx + bar_w, bottom], fill=BLACK)
            draw.rectangle([bx - 1, min(top, zero_y) - 4, bx + bar_w + 1, max(bottom, zero_y)],
                           outline=BLACK, width=2)
        elif config.EPAPER_COLOR and val >= 20:
            draw.rectangle([bx, top, bx + bar_w, bottom], fill=RED)
        else:
            # Hollow bars for normal hours keep the chart light and readable.
            draw.rectangle([bx, top, bx + bar_w, bottom], outline=BLACK, width=1)

        bx += bar_w + gap

    # Hour ticks every 6 hours.
    bx = x
    labelled = set()
    for hh in hours:
        hour = hh["hour"]
        if hour % 6 == 0 and hour not in labelled:
            _text(draw, (bx + bar_w / 2, chart_bottom + 2), f"{hour:02d}", 13,
                  fill=GREY, anchor="ma")
            labelled.add(hour)
        bx += bar_w + gap

    _text(draw, (x + w, y), f"min {min(values):.1f} / max {vmax:.1f}", 14,
          fill=GREY, anchor="ra")


def _draw_buses(draw, x: int, y: int, w: int, h: int) -> None:
    _text(draw, (x, y), "NEXT DEPARTURES", 18, bold=True, fill=GREY)
    stops = buses.get_schedules()

    col_gap = 24
    col_w = (w - col_gap) // 2 if len(stops) > 1 else w
    rows = max(1, (h - 34) // 30)

    for ci, stop in enumerate(stops[:2]):
        cx = x + ci * (col_w + col_gap)
        cy = y + 30

        name = stop.get("name", "Stop")
        code = stop.get("code")
        title = f"{name} ({code})" if code else name
        _text(draw, (cx, cy), _fit(title, 17, col_w, bold=True), 17, bold=True)
        cy += 26

        if stop.get("error"):
            _text(draw, (cx, cy), _fit(stop["error"], 15, col_w), 15, fill=GREY)
            continue

        deps = stop.get("departures") or []
        if not deps:
            _text(draw, (cx, cy), "No departures", 15, fill=GREY)
            continue

        for dep in deps[:rows]:
            route = str(dep.get("route", "?"))
            dest = str(dep.get("destination", ""))
            time_s = str(dep.get("time", ""))
            mins = dep.get("minutes_until")

            # Route badge (filled box with white number).
            badge_w = max(34, _text_width(route, 16, bold=True) + 12)
            draw.rectangle([cx, cy, cx + badge_w, cy + 22], fill=BLACK)
            _text(draw, (cx + badge_w / 2, cy + 11), route, 16, bold=True,
                  fill=WHITE, anchor="mm")

            min_txt = f"{mins}'" if isinstance(mins, int) and mins >= 0 else "now"
            _text(draw, (cx + col_w, cy + 2), f"{time_s}  {min_txt}", 16,
                  bold=True, anchor="ra")

            dest_max = col_w - badge_w - 90
            _text(draw, (cx + badge_w + 8, cy + 3),
                  _fit(dest, 15, max(20, dest_max)), 15, fill=BLACK)
            cy += 30
            if cy > y + h:
                break


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render() -> Image.Image:
    """Build and return the full dashboard image as an RGB ``PIL.Image``."""
    w = config.EPAPER_WIDTH
    h = config.EPAPER_HEIGHT

    img = Image.new("RGB", (w, h), WHITE)
    draw = ImageDraw.Draw(img)

    header_bottom = _draw_header(draw, w)

    margin = 14
    body_top = header_bottom + 12

    # Top band: sensors (left) | electricity (right).
    band_h = 196
    mid_x = int(w * 0.42)
    _draw_sensors(draw, margin, body_top, mid_x - margin - 12, band_h)
    draw.line([(mid_x, body_top), (mid_x, body_top + band_h)], fill=GREY, width=1)
    _draw_electricity(draw, mid_x + 16, body_top, w - mid_x - 16 - margin, band_h)

    # Divider + bottom band: buses across the full width.
    bus_top = body_top + band_h + 10
    draw.line([(0, bus_top - 4), (w, bus_top - 4)], fill=BLACK, width=2)
    _draw_buses(draw, margin, bus_top, w - 2 * margin, h - bus_top - 10)

    if config.EPAPER_ROTATE:
        img = img.rotate(config.EPAPER_ROTATE, expand=True)
    return img


def render_png() -> bytes:
    buf = io.BytesIO()
    render().save(buf, format="PNG")
    return buf.getvalue()


def render_bmp(mono: bool = True) -> bytes:
    """Return a BMP. ``mono`` produces a 1-bit image for bare e-ink sketches."""
    img = render()
    if mono:
        # Convert to pure black/white with a 50% threshold (no dithering so text
        # stays crisp on the panel).
        img = img.convert("L").point(lambda p: 255 if p > 128 else 0, mode="1")
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    return buf.getvalue()
