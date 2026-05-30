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
import weather

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
# Hand-drawn icons
#
# Real emoji / dingbat glyphs render inconsistently (often as empty "tofu"
# boxes) across the fonts available on a dev Mac vs. the Raspberry Pi that
# drives the panel. Drawing the icons ourselves with simple primitives keeps
# them crisp and identical on every 1-bit display.
# ---------------------------------------------------------------------------

def _draw_sun(draw, cx, cy, r, fill=BLACK):
    import math

    # Rays.
    for k in range(8):
        a = k * math.pi / 4
        x1 = cx + math.cos(a) * (r + 3)
        y1 = cy + math.sin(a) * (r + 3)
        x2 = cx + math.cos(a) * (r + 7)
        y2 = cy + math.sin(a) * (r + 7)
        draw.line([(x1, y1), (x2, y2)], fill=fill, width=2)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=fill, width=2)


def _draw_cloud(draw, cx, cy, s, fill=BLACK, filled=False):
    """A simple cloud centred roughly on (cx, cy); ``s`` ≈ half-width."""
    body = WHITE if not filled else fill
    # Three overlapping lobes + a base, outlined.
    draw.ellipse([cx - s, cy - s * 0.2, cx - s * 0.2, cy + s * 0.6],
                 outline=fill, width=2, fill=body)
    draw.ellipse([cx - s * 0.5, cy - s * 0.8, cx + s * 0.5, cy + s * 0.4],
                 outline=fill, width=2, fill=body)
    draw.ellipse([cx + s * 0.1, cy - s * 0.3, cx + s, cy + s * 0.6],
                 outline=fill, width=2, fill=body)
    draw.rectangle([cx - s * 0.7, cy + s * 0.1, cx + s * 0.7, cy + s * 0.55],
                   fill=body)


def _draw_weather_icon(draw, kind, x, y, size):
    """Draw a weather icon of category ``kind`` in a ``size`` px box at (x, y)."""
    cx = x + size / 2
    cy = y + size / 2
    r = size * 0.22

    if kind == "sun":
        _draw_sun(draw, cx, cy, r)
        return

    if kind == "partly":
        _draw_sun(draw, cx - size * 0.18, cy - size * 0.18, r * 0.75)
        _draw_cloud(draw, cx + size * 0.08, cy + size * 0.12, size * 0.28)
        return

    # Everything else is cloud-based; draw the cloud then add precipitation.
    _draw_cloud(draw, cx, cy - size * 0.12, size * 0.30)
    base_y = cy + size * 0.30

    if kind == "rain" or kind == "drizzle":
        n = 3 if kind == "rain" else 2
        for k in range(n):
            dx = cx - size * 0.22 + k * size * 0.22
            draw.line([(dx, base_y), (dx - size * 0.06, base_y + size * 0.18)],
                      fill=BLACK, width=2)
    elif kind == "snow":
        for k in range(3):
            dx = cx - size * 0.22 + k * size * 0.22
            _text(draw, (dx, base_y + size * 0.04), "*", int(size * 0.32), bold=True)
    elif kind == "thunder":
        draw.line([(cx, base_y - size * 0.02), (cx - size * 0.12, base_y + size * 0.16),
                   (cx + size * 0.04, base_y + size * 0.14),
                   (cx - size * 0.06, base_y + size * 0.30)], fill=BLACK, width=2)
    elif kind == "fog":
        for k in range(3):
            fy = base_y - size * 0.02 + k * size * 0.12
            draw.line([(cx - size * 0.28, fy), (cx + size * 0.28, fy)],
                      fill=GREY, width=2)


def _draw_bus_icon(draw, x, y, size, fill=BLACK):
    """A small bus pictogram for the departures heading."""
    w = size
    h = size * 0.74
    top = y + (size - h) / 2
    draw.rounded_rectangle([x, top, x + w, top + h], radius=size * 0.14,
                           outline=fill, width=2)
    # Windscreen / window band.
    draw.line([(x, top + h * 0.42), (x + w, top + h * 0.42)], fill=fill, width=2)
    # Wheels.
    wr = size * 0.10
    draw.ellipse([x + w * 0.18 - wr, top + h - wr, x + w * 0.18 + wr, top + h + wr],
                 fill=fill)
    draw.ellipse([x + w * 0.82 - wr, top + h - wr, x + w * 0.82 + wr, top + h + wr],
                 fill=fill)


def _draw_bolt_icon(draw, x, y, size, fill=BLACK):
    """A lightning bolt for the electricity heading."""
    draw.line([(x + size * 0.62, y), (x + size * 0.18, y + size * 0.55),
               (x + size * 0.5, y + size * 0.55), (x + size * 0.32, y + size),
               (x + size * 0.85, y + size * 0.38),
               (x + size * 0.5, y + size * 0.38)], fill=fill, width=2, joint="curve")


def _draw_thermo_icon(draw, x, y, size, fill=BLACK):
    """A thermometer for the sensors heading."""
    cw = size * 0.34
    cx = x + size / 2
    bulb_r = size * 0.22
    draw.rounded_rectangle([cx - cw / 2, y, cx + cw / 2, y + size * 0.66],
                           radius=cw / 2, outline=fill, width=2)
    draw.ellipse([cx - bulb_r, y + size * 0.55, cx + bulb_r, y + size * 0.55 + 2 * bulb_r],
                 fill=fill)
    draw.line([(cx, y + size * 0.2), (cx, y + size * 0.6)], fill=fill, width=2)


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

# Finnish weekday abbreviations (Mon..Sun); strftime("%a") would give English.
_FI_WEEKDAYS = ["Ma", "Ti", "Ke", "To", "Pe", "La", "Su"]


def _fi_date(now) -> str:
    """Finnish short date, e.g. 'La 30.5.2026'."""
    wd = _FI_WEEKDAYS[now.weekday()]
    return f"{wd} {now.day}.{now.month}.{now.year}"


def _draw_header(draw, w: int) -> int:
    now = datetime.now(tz=_HELSINKI)
    clock = now.strftime("%H:%M")
    date = _fi_date(now)

    _text(draw, (16, 6), clock, 46, bold=True)
    _text(draw, (172, 24), date, 20, fill=GREY)

    # Current electricity price, right-aligned, as an at-a-glance number.
    prices = electricity.get_prices()
    current = prices.get("current")
    if current:
        price = current["price_with_tax"]
        accent = RED if (config.EPAPER_COLOR and price >= 20) else BLACK
        _text(draw, (w - 16, 4), f"{price:.1f}", 36, bold=True, fill=accent, anchor="ra")
        _text(draw, (w - 16, 44), "c/kWh nyt", 16, fill=GREY, anchor="ra")

    if config.DEMO_MODE:
        _text(draw, (380, 44), "DEMO", 14, fill=GREY)

    # Sensor strip across the full width, just under the clock row.
    strip_top = 62
    draw.line([(0, strip_top), (w, strip_top)], fill=GREY, width=1)
    _draw_sensor_strip(draw, 14, strip_top + 4, w - 28, 46)

    header_bottom = strip_top + 4 + 46 + 6
    draw.line([(0, header_bottom), (w, header_bottom)], fill=BLACK, width=2)
    return header_bottom


def _draw_sensor_strip(draw, x: int, y: int, w: int, h: int) -> None:
    """Compact horizontal row of Ruuvi sensor readings for the top bar."""
    keys = list(config.RUUVI_TAGS.keys())
    if not keys:
        return
    data = ruuvi_reader.get_latest_data()

    col_gap = 14
    col_w = (w - (len(keys) - 1) * col_gap) // len(keys)

    for i, key in enumerate(keys):
        cx = x + i * (col_w + col_gap)
        d = data.get(key, {})
        name = d.get("name", key)
        temp = d.get("temperature")
        hum = d.get("humidity")

        # Vertical divider between cells.
        if i > 0:
            draw.line([(cx - col_gap // 2, y), (cx - col_gap // 2, y + h)],
                      fill=GREY, width=1)

        _text(draw, (cx, y), _fit(name, 16, col_w - 70, bold=True), 16, bold=True)
        sub = f"{hum:.0f}% RH" if hum is not None else ""
        if sub:
            _text(draw, (cx, y + 22), sub, 14, fill=GREY)

        if temp is not None:
            _text(draw, (cx + col_w, y + 2), f"{temp:.1f}°", 30, bold=True, anchor="ra")
        else:
            _text(draw, (cx + col_w, y + 2), "--", 30, bold=True, fill=GREY, anchor="ra")


def _draw_weather(draw, x: int, y: int, w: int, h: int) -> None:
    wx = weather.get_weather()
    _draw_thermo_icon(draw, x, y - 1, 18)
    _text(draw, (x + 24, y), f"SÄÄ  {wx.get('location', '')}".upper(), 18,
          bold=True, fill=GREY)

    if wx.get("error"):
        _text(draw, (x, y + 30), wx["error"], 16, fill=GREY)
        return

    cur = wx.get("current") or {}
    temp = cur.get("temperature")
    cy = y + 30
    if temp is not None:
        # Current-conditions icon to the right of the panel heading row.
        _draw_weather_icon(draw, cur.get("icon", "cloud"), x + w - 46, y + 24, 44)
        _text(draw, (x, cy - 6), f"{temp:.0f}°", 48, bold=True)
        tx = x + _text_width(f"{temp:.0f}°", 48, bold=True) + 14
        _text(draw, (tx, cy), _fit(cur.get("text", ""), 20, x + w - tx - 50, bold=True),
              20, bold=True)
        extras = []
        if cur.get("humidity") is not None:
            extras.append(f"{cur['humidity']:.0f}% RH")
        if cur.get("wind") is not None:
            extras.append(f"{cur['wind']:.0f} m/s")
        if extras:
            _text(draw, (tx, cy + 26), "   ".join(extras), 15, fill=GREY)

    # Hourly forecast for the next 8 hours, laid out in two columns of four so
    # they all fit in the left band.
    hourly = wx.get("hourly") or []
    # Skip the current hour (already covered by "current" above) when possible.
    upcoming = hourly[1:] if len(hourly) > 1 else hourly
    upcoming = upcoming[:8]

    grid_top = y + 90
    row_h = 22
    per_col = 4
    col_gap = 18
    col_w = (w - col_gap) // 2

    for i, hr in enumerate(upcoming):
        col = i // per_col
        row = i % per_col
        cx = x + col * (col_w + col_gap)
        ry = grid_top + row * row_h
        if ry > y + h:
            continue

        hh = hr.get("hour")
        text = hr.get("text", "")
        temp = hr.get("temperature")
        pop = hr.get("precip_prob")
        icon = hr.get("icon", "cloud")

        _text(draw, (cx, ry), f"{hh:02d}" if hh is not None else "--", 15, bold=True)
        # Small per-hour weather icon next to the hour.
        _draw_weather_icon(draw, icon, cx + 26, ry, 18)
        # Rain probability sits just left of the temperature; only show it when
        # there's a meaningful chance so the row stays uncluttered.
        rain_txt = f"{pop:.0f}%" if isinstance(pop, (int, float)) and pop >= 10 else ""
        if rain_txt:
            _text(draw, (cx + col_w - 42, ry + 1), rain_txt, 13, fill=GREY, anchor="ra")
        _text(draw, (cx + 50, ry + 1), _fit(text, 13, col_w - 50 - 86), 13, fill=BLACK)
        t = f"{temp:.0f}°" if temp is not None else "--"
        _text(draw, (cx + col_w, ry), t, 15, bold=True, anchor="ra")


def _draw_electricity(draw, x: int, y: int, w: int, h: int) -> None:
    _draw_bolt_icon(draw, x, y - 1, 18)
    _text(draw, (x + 24, y), "SÄHKÖ  c/kWh", 18, bold=True, fill=GREY)
    prices = electricity.get_prices()
    hours = prices.get("hours") or []

    chart_top = y + 30
    chart_bottom = y + h - 22
    chart_h = chart_bottom - chart_top

    if not hours:
        _text(draw, (x, chart_top + 10), "Ei hintatietoja", 18, fill=GREY)
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
    _draw_bus_icon(draw, x, y - 1, 20)
    _text(draw, (x + 28, y), "SEURAAVAT LÄHDÖT", 18, bold=True, fill=GREY)
    stops = buses.get_schedules()

    col_gap = 24
    col_w = (w - col_gap) // 2 if len(stops) > 1 else w
    rows = max(1, (h - 34) // 30)

    for ci, stop in enumerate(stops[:2]):
        cx = x + ci * (col_w + col_gap)
        cy = y + 30

        name = stop.get("name", "Pysäkki")
        code = stop.get("code")
        title = f"{name} ({code})" if code else name
        _text(draw, (cx, cy), _fit(title, 17, col_w, bold=True), 17, bold=True)
        cy += 26

        if stop.get("error"):
            _text(draw, (cx, cy), _fit(stop["error"], 15, col_w), 15, fill=GREY)
            continue

        deps = stop.get("departures") or []
        if not deps:
            _text(draw, (cx, cy), "Ei lähtöjä", 15, fill=GREY)
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

            min_txt = f"{mins}'" if isinstance(mins, int) and mins >= 0 else "nyt"
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
    body_top = header_bottom + 10

    # Top band: weather (left) | electricity (right). Sensors now live in the
    # top bar, so the left column shows the local forecast instead.
    band_h = 168
    mid_x = int(w * 0.44)
    _draw_weather(draw, margin, body_top, mid_x - margin - 12, band_h)
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
