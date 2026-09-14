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

import hashlib
import io
import logging
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

import buses
import calendar_view
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

# Electricity price thresholds for tiered bar fills on monochrome e-ink (c/kWh,
# tax-included).  Bars below CHEAP are hollow; CHEAP–EXPENSIVE are hatched;
# above EXPENSIVE are solid.  Adjust to match your local price expectations.
ELEC_CHEAP_THRESHOLD = 8.0
ELEC_EXPENSIVE_THRESHOLD = 18.0

# Candidate TrueType fonts. Pillow does not ship a scalable font on every
# platform, so we probe a few common locations and fall back to the bundled
# bitmap default if none are found.
# To get clean modern fonts on Raspberry Pi run:
#   sudo apt-get install -y fonts-roboto fonts-noto
_REGULAR_FONTS = [
    # Roboto (modern, clean – best choice on Raspberry Pi)
    "/usr/share/fonts/truetype/roboto/unhinted/RobotoTTF/Roboto-Regular.ttf",
    "/usr/share/fonts/truetype/roboto/Roboto-Regular.ttf",
    "/usr/share/fonts/truetype/roboto/hinted/Roboto-Regular.ttf",
    # Noto Sans (excellent Unicode fallback)
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoSans-Regular.ttf",
    # Liberation Sans (pre-installed on most Debian/Ubuntu systems)
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    # DejaVu fallback
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    # macOS / Windows
    "/Library/Fonts/SF-Pro-Text-Regular.otf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/SFNSDisplay.ttf",
    "C:/Windows/Fonts/arial.ttf",
]
_BOLD_FONTS = [
    "/usr/share/fonts/truetype/roboto/unhinted/RobotoTTF/Roboto-Bold.ttf",
    "/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf",
    "/usr/share/fonts/truetype/roboto/hinted/Roboto-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/opentype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]

_font_cache: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}


def _resolve_layout(layout: str | None = None) -> str:
    if layout:
        return layout.lower()
    return getattr(config, "EPAPER_LAYOUT", "e1001").lower()


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
    if getattr(config, "EPAPER_FORCE_BOLD", False):
        bold = True
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


def _draw_steam(draw, cx, cy_bottom, width, height, fill=BLACK):
    """Three wavy steam lines rising upward, decorating the sauna löyly count."""
    import math
    n = 3
    spacing = width / (n + 1)
    for i in range(n):
        sx = cx - width / 2 + spacing * (i + 1)
        pts = []
        steps = 18
        for j in range(steps + 1):
            frac = j / steps
            y = cy_bottom - frac * height
            x = sx + (width / (n * 2.5)) * math.sin(frac * 3 * math.pi)
            pts.append((x, y))
        for j in range(len(pts) - 1):
            draw.line([pts[j], pts[j + 1]], fill=fill, width=3)


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


def _data_age_label(updated_at: str | None) -> str | None:
    """Return a short staleness label (e.g. '45 min') when data is older than
    RUUVI_STALE_MINUTES, or None when data is fresh / never received."""
    if not updated_at:
        return "ei yht."  # no contact at all
    try:
        from datetime import timezone as _tz
        ts = datetime.fromisoformat(updated_at)
        age_s = (datetime.now(_tz.utc) - ts).total_seconds()
        threshold = getattr(config, "RUUVI_STALE_MINUTES", 10) * 60
        if age_s < threshold:
            return None  # fresh enough
        age_min = int(age_s // 60)
        if age_min < 60:
            return f"{age_min} min"
        return f"{age_min // 60}h {age_min % 60:02d}m"
    except Exception:
        return None


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
        age_label = _data_age_label(d.get("updated_at"))

        # Vertical divider between cells.
        if i > 0:
            draw.line([(cx - col_gap // 2, y), (cx - col_gap // 2, y + h)],
                      fill=GREY, width=1)

        _text(draw, (cx, y), _fit(name, 16, col_w - 70, bold=True), 16, bold=True)
        if age_label:
            # Show staleness instead of humidity when data is old.
            _text(draw, (cx, y + 22), age_label, 14, fill=GREY)
        elif hum is not None:
            _text(draw, (cx, y + 22), f"{hum:.0f}% RH", 14, fill=GREY)

        if temp is not None:
            fill = GREY if age_label else BLACK
            _text(draw, (cx + col_w, y + 2), f"{temp:.1f}°", 30, bold=True,
                  fill=fill, anchor="ra")
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

    # Horizontal guide lines at price tier boundaries so the chart reads
    # clearly on a monochrome panel without any colour cues.
    for ref_price in [ELEC_CHEAP_THRESHOLD, ELEC_EXPENSIVE_THRESHOLD]:
        if vmin < ref_price < vmax:
            ref_y = baseline - int((ref_price - vmin) / span * chart_h)
            if chart_top <= ref_y <= chart_bottom:
                draw.line([(x, ref_y), (x + w, ref_y)], fill=GREY, width=1)

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
            # Solid fill + thick border – unmistakeable on a 1-bit panel.
            draw.rectangle([bx, top, bx + bar_w, bottom], fill=BLACK)
            draw.rectangle([bx - 1, min(top, zero_y) - 4, bx + bar_w + 1, max(bottom, zero_y)],
                           outline=BLACK, width=2)
        elif val >= ELEC_EXPENSIVE_THRESHOLD:
            # Expensive hour: solid fill (replaces RED on colour panels).
            draw.rectangle([bx, top, bx + bar_w, bottom],
                           fill=RED if config.EPAPER_COLOR else BLACK)
        elif val >= ELEC_CHEAP_THRESHOLD:
            # Moderate hour: hatched fill – outline plus horizontal stripes.
            draw.rectangle([bx, top, bx + bar_w, bottom], outline=BLACK, width=1)
            for hy in range(top + 2, bottom - 1, 4):
                draw.line([(bx + 1, hy), (bx + bar_w - 1, hy)], fill=BLACK, width=1)
        else:
            # Cheap hour: hollow outline only.
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


# ---------------------------------------------------------------------------
# Sauna view helpers
# ---------------------------------------------------------------------------

def _session_duration(session_start: str | None) -> str:
    """Return a Finnish session duration string, e.g. 'Saunattu: 1h 23min'."""
    if not session_start:
        return ""
    try:
        from datetime import timezone as _tz
        ts = datetime.fromisoformat(session_start)
        secs = int((datetime.now(_tz.utc) - ts).total_seconds())
        h, rem = divmod(secs, 3600)
        m = rem // 60
        if h:
            return f"Saunattu: {h}h {m:02d}min"
        return f"Saunattu: {m} min"
    except Exception:
        return ""


def _draw_sauna_header(draw, w: int) -> int:
    """Compact header for sauna view: clock left, date right."""
    now = datetime.now(tz=_HELSINKI)
    _text(draw, (16, 8), now.strftime("%H:%M"), 40, bold=True)
    _text(draw, (w - 16, 8), _fi_date(now), 20, fill=GREY, anchor="ra")
    header_bottom = 56
    draw.line([(0, header_bottom), (w, header_bottom)], fill=BLACK, width=2)
    return header_bottom


def _render_sauna_landscape(sauna: dict) -> Image.Image:
    """800×480 sauna view: big temperature left, löyly count right."""
    w = config.EPAPER_WIDTH
    h = config.EPAPER_HEIGHT
    img = Image.new("RGB", (w, h), WHITE)
    draw = ImageDraw.Draw(img)

    header_bottom = _draw_sauna_header(draw, w)
    body_top = header_bottom + 10

    # ── Left column: temperature & session info ──────────────────────────────
    left_cx = int(w * 0.28)      # horizontal centre of left column
    divider_x = int(w * 0.54)   # vertical divider position

    _text(draw, (16, body_top), "SAUNA", 24, bold=True)

    temp = sauna.get("temperature")
    hum = sauna.get("humidity")
    temp_str = f"{temp:.0f}°" if temp is not None else "--"
    _text(draw, (left_cx, body_top + 150), temp_str, 120, bold=True, anchor="mm")

    if hum is not None:
        _text(draw, (left_cx, body_top + 240), f"{hum:.0f}% RH", 34, bold=True, anchor="mm")

    dur = _session_duration(sauna.get("session_start"))
    if dur:
        _text(draw, (left_cx, body_top + 300), dur, 20, fill=GREY, anchor="mm")

    # ── Vertical divider ─────────────────────────────────────────────────────
    draw.line([(divider_x, body_top), (divider_x, h - 10)], fill=GREY, width=1)

    # ── Right column: löyly count ────────────────────────────────────────────
    right_cx = divider_x + (w - divider_x) // 2
    count = sauna.get("loyly_count", 0)

    _text(draw, (right_cx, body_top + 20), "LÖYLYÄ", 28, bold=True, anchor="mm")
    # Steam icon sitting above the count number.
    _draw_steam(draw, right_cx, body_top + 130, 110, 80)
    _text(draw, (right_cx, body_top + 280), str(count), 110, bold=True, anchor="mm")
    _text(draw, (right_cx, body_top + 355), "kertaa", 20, fill=GREY, anchor="mm")

    if config.EPAPER_ROTATE:
        img = img.rotate(config.EPAPER_ROTATE, expand=True)
    return img


def _render_sauna_portrait(sauna: dict) -> Image.Image:
    """480×800 portrait sauna view: temperature top half, löyly count bottom half."""
    w = _PORTRAIT_W
    h = _PORTRAIT_H
    img = Image.new("RGB", (w, h), WHITE)
    draw = ImageDraw.Draw(img)

    header_bottom = _draw_sauna_header(draw, w)
    body_top = header_bottom + 10
    cx = w // 2

    # ── Top section: temperature & session info ───────────────────────────────
    _text(draw, (cx, body_top + 10), "SAUNA", 24, bold=True, anchor="mm")

    temp = sauna.get("temperature")
    hum = sauna.get("humidity")
    temp_str = f"{temp:.0f}°" if temp is not None else "--"
    _text(draw, (cx, body_top + 160), temp_str, 130, bold=True, anchor="mm")

    if hum is not None:
        _text(draw, (cx, body_top + 262), f"{hum:.0f}% RH", 34, bold=True, anchor="mm")

    dur = _session_duration(sauna.get("session_start"))
    if dur:
        _text(draw, (cx, body_top + 316), dur, 20, fill=GREY, anchor="mm")

    # ── Divider ───────────────────────────────────────────────────────────────
    div_y = body_top + 348
    draw.line([(0, div_y), (w, div_y)], fill=BLACK, width=2)

    # ── Bottom section: löyly count ──────────────────────────────────────────
    count = sauna.get("loyly_count", 0)
    sec_top = div_y + 14

    _text(draw, (cx, sec_top + 20), "LÖYLYÄ", 28, bold=True, anchor="mm")
    _draw_steam(draw, cx, sec_top + 120, 120, 80)
    _text(draw, (cx, sec_top + 270), str(count), 120, bold=True, anchor="mm")
    _text(draw, (cx, sec_top + 345), "kertaa", 22, fill=GREY, anchor="mm")

    return img

_NIGHT_START = 22  # inclusive (hour in Helsinki time)
_NIGHT_END = 6     # exclusive (resume at this hour)


def is_night_mode() -> bool:
    """Return True between 22:00 and 06:00 Helsinki time."""
    hour = datetime.now(tz=_HELSINKI).hour
    return hour >= _NIGHT_START or hour < _NIGHT_END


def render(layout: str | None = None) -> Image.Image:
    """Build and return the dashboard image in the orientation set by config.

    Delegates to ``render_portrait()`` when ``config.EPAPER_ORIENTATION`` is
    ``"portrait"``, otherwise renders the standard landscape layout.
    """
    resolved_layout = _resolve_layout(layout)
    if resolved_layout == "e1002":
        return _render_e1002_calendar()
    if resolved_layout == "e1002-dashboard":
        return _render_e1002()
    if getattr(config, "EPAPER_ORIENTATION", "landscape") == "portrait":
        return render_portrait()
    return _render_landscape()


def _render_landscape() -> Image.Image:
    """Build and return the 800×480 landscape dashboard as an RGB PIL.Image."""
    # Sauna mode takes priority over everything including night mode.
    sauna = ruuvi_reader.get_sauna_state()
    if sauna:
        return _render_sauna_landscape(sauna)

    w = config.EPAPER_WIDTH
    h = config.EPAPER_HEIGHT

    if is_night_mode():
        # Show a minimal clock so the display is not completely blank at night.
        img = Image.new("RGB", (w, h), WHITE)
        draw = ImageDraw.Draw(img)
        now = datetime.now(tz=_HELSINKI)
        _text(draw, (w // 2, h // 2 - 24), now.strftime("%H:%M"), 80, bold=True, anchor="mm")
        _text(draw, (w // 2, h // 2 + 44), _fi_date(now), 22, fill=GREY, anchor="mm")
        return img

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


def _invert(img: Image.Image) -> Image.Image:
    """Invert black<->white when ``config.EPAPER_INVERT`` is set.

    Some 1-bit panels treat a set bit as white and others as black, so a PNG
    that looks correct on screen can appear with inverted colours on the panel.
    Enable EPAPER_INVERT to flip the output to match the hardware.
    """
    if getattr(config, "EPAPER_INVERT", False):
        from PIL import ImageOps
        return ImageOps.invert(img.convert("RGB"))
    return img


# ---------------------------------------------------------------------------
# Render cache – avoids re-rendering on every device poll within a refresh
# window. The cache entry is invalidated after _RENDER_CACHE_TTL seconds so
# that stale data is never served for more than one interval. A potential
# double-render race is intentionally accepted over adding another lock.
# ---------------------------------------------------------------------------
_render_cache: dict[str, tuple[str, bytes, float]] = {}  # key → (etag, png_bytes, monotonic_ts)
_render_cache_lock = threading.Lock()
_RENDER_CACHE_TTL = 55  # slightly under the 60 s data refresh interval


def invalidate_render_cache() -> None:
    """Discard cached images after a data source changes."""
    with _render_cache_lock:
        _render_cache.clear()


def _cached_render_png(key: str, render_fn) -> tuple[str, bytes]:
    """Return *(etag, png_bytes)*, pulling from cache when still fresh."""
    with _render_cache_lock:
        entry = _render_cache.get(key)
        if entry and (time.monotonic() - entry[2]) < _RENDER_CACHE_TTL:
            return entry[0], entry[1]
    # Render outside the lock so long renders don't block concurrent readers.
    buf = io.BytesIO()
    _invert(render_fn()).save(buf, format="PNG")
    data = buf.getvalue()
    etag = hashlib.md5(data).hexdigest()
    with _render_cache_lock:
        _render_cache[key] = (etag, data, time.monotonic())
    return etag, data


def get_render_etag(layout: str | None = None) -> str:
    """Return the ETag of the image that would be served by render_png().

    Uses the render cache, so calling this is nearly free after the first
    render. The e-paper device can poll */epaper/etag* and only fetch the
    full image when the value changes – avoiding unnecessary panel refreshes.
    """
    resolved_layout = _resolve_layout(layout)
    orientation = "portrait" if getattr(config, "EPAPER_ORIENTATION", "landscape") == "portrait" else "landscape"
    key = f"{resolved_layout}:{orientation}"
    etag, _ = _cached_render_png(key, lambda: render(resolved_layout))
    return etag


def render_png(layout: str | None = None) -> bytes:
    resolved_layout = _resolve_layout(layout)
    orientation = "portrait" if getattr(config, "EPAPER_ORIENTATION", "landscape") == "portrait" else "landscape"
    key = f"{resolved_layout}:{orientation}"
    _, data = _cached_render_png(key, lambda: render(resolved_layout))
    return data


def render_bmp(mono: bool = True, layout: str | None = None) -> bytes:
    """Return a BMP. ``mono`` produces a 1-bit image for bare e-ink sketches."""
    img = _invert(render(layout))
    if mono:
        # Floyd-Steinberg dithering preserves the tonal hierarchy of grey labels
        # and icons (they dither to a lighter pattern instead of collapsing to
        # solid black) while keeping bold text crisp at 800×480 resolution.
        img = img.convert("1")
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Portrait / vertical layout  (480 × 800 – same panel mounted on its side)
# ---------------------------------------------------------------------------
# Sections are stacked top-to-bottom:
#   header (clock + sensor strip)  ~118 px
#   weather                         220 px
#   ── divider ──
#   electricity chart               155 px
#   ── divider ──
#   bus departures                  remainder
# ---------------------------------------------------------------------------

_PORTRAIT_W = 480
_PORTRAIT_H = 800


def render_portrait() -> Image.Image:
    """Build and return the 480×800 portrait dashboard as an RGB PIL.Image."""
    # Sauna mode takes priority over everything including night mode.
    sauna = ruuvi_reader.get_sauna_state()
    if sauna:
        return _render_sauna_portrait(sauna)

    w = _PORTRAIT_W
    h = _PORTRAIT_H

    if is_night_mode():
        img = Image.new("RGB", (w, h), WHITE)
        draw = ImageDraw.Draw(img)
        now = datetime.now(tz=_HELSINKI)
        _text(draw, (w // 2, h // 2 - 24), now.strftime("%H:%M"), 80, bold=True, anchor="mm")
        _text(draw, (w // 2, h // 2 + 44), _fi_date(now), 22, fill=GREY, anchor="mm")
        return img

    img = Image.new("RGB", (w, h), WHITE)
    draw = ImageDraw.Draw(img)

    # The existing header renderer works at any width – it anchors clock left
    # and price right, so it adapts cleanly to 480 px.
    header_bottom = _draw_header(draw, w)

    margin = 14
    body_top = header_bottom + 10
    remaining = h - body_top - margin

    # Fixed heights leave the rest for buses, which benefits most from space.
    weather_h = 220
    elec_h = 155
    divider_space = 20  # 10 px gap either side of each bold divider line
    bus_h = remaining - weather_h - elec_h - divider_space * 2

    _draw_weather(draw, margin, body_top, w - 2 * margin, weather_h)

    elec_top = body_top + weather_h + divider_space
    draw.line([(0, elec_top - 4), (w, elec_top - 4)], fill=BLACK, width=2)
    _draw_electricity(draw, margin, elec_top, w - 2 * margin, elec_h)

    bus_top = elec_top + elec_h + divider_space
    draw.line([(0, bus_top - 4), (w, bus_top - 4)], fill=BLACK, width=2)
    _draw_buses(draw, margin, bus_top, w - 2 * margin, bus_h)

    return img


def render_portrait_png() -> bytes:
    _, data = _cached_render_png("portrait", render_portrait)
    return data


def render_portrait_bmp(mono: bool = True) -> bytes:
    """1-bit BMP version of the portrait layout."""
    img = _invert(render_portrait())
    if mono:
        img = img.convert("1")
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    return buf.getvalue()


def _parse_dt(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _format_clock(dt) -> str:
    return dt.strftime("%H:%M") if dt else "--:--"


def _format_date_label(dt) -> str:
    return dt.strftime("%a %d.%m") if dt else "--.--"


def _status_colors(price: float | None) -> tuple[str, tuple[int, int, int], tuple[int, int, int]]:
    if price is None:
        return "NO DATA", (220, 220, 220), BLACK
    if price >= ELEC_EXPENSIVE_THRESHOLD:
        return "EXPENSIVE", (255, 225, 225), RED
    if price >= ELEC_CHEAP_THRESHOLD:
        return "NORMAL", (255, 242, 210), (150, 100, 0)
    return "CHEAP", (225, 245, 225), (0, 120, 0)


def _draw_pill(draw, x, y, w, h, fill, outline, label, text_fill=BLACK, size=15):
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=fill, outline=outline, width=1)
    _text(draw, (x + w / 2, y + h / 2 + 1), label, size, bold=True, fill=text_fill, anchor="mm")


def _draw_sparkline(draw, values, x, y, w, h, *, fill=BLACK, outline=GREY):
    values = [v for v in values if isinstance(v, (int, float))]
    draw.rounded_rectangle([x, y, x + w, y + h], radius=6, outline=outline, width=1)
    if len(values) < 2:
        _text(draw, (x + 10, y + h / 2), "--", 14, fill=GREY, anchor="lm")
        return
    vmin = min(values)
    vmax = max(values)
    span = (vmax - vmin) or 1.0
    pts = []
    for i, value in enumerate(values):
        px = x + 6 + (w - 12) * i / (len(values) - 1)
        py = y + h - 6 - (h - 12) * (value - vmin) / span
        pts.append((px, py))
    for i in range(len(pts) - 1):
        draw.line([pts[i], pts[i + 1]], fill=fill, width=2)
    for px, py in pts:
        draw.ellipse([px - 2, py - 2, px + 2, py + 2], fill=fill)


def _draw_horizontal_bars(draw, values, labels, x, y, w, h, *, fill=BLACK):
    values = list(values)
    if not values:
        _text(draw, (x + 8, y + h / 2), "--", 14, fill=GREY, anchor="lm")
        return
    vmax = max(values) or 1.0
    row_h = max(10, min(24, (h - 2) // len(values)))
    for i, value in enumerate(values):
        row_y = y + i * row_h
        label = labels[i] if i < len(labels) else ""
        _text(draw, (x, row_y + row_h / 2 + 1), label, 12, fill=GREY, anchor="lm")
        bar_x = x + 46
        bar_w = int((w - 52) * max(0.0, float(value)) / vmax)
        draw.rectangle([bar_x, row_y + 4, bar_x + max(1, bar_w), row_y + row_h - 4], fill=fill)


def _aggregate_ruuvi() -> dict:
    data = ruuvi_reader.get_latest_data()
    temps = [d.get("temperature") for d in data.values() if isinstance(d.get("temperature"), (int, float))]
    hums = [d.get("humidity") for d in data.values() if isinstance(d.get("humidity"), (int, float))]
    pressures = [d.get("pressure") for d in data.values() if isinstance(d.get("pressure"), (int, float))]
    return {
        "temperature": sum(temps) / len(temps) if temps else None,
        "humidity": sum(hums) / len(hums) if hums else None,
        "pressure": sum(pressures) / len(pressures) if pressures else None,
        "count": len(data),
        "freshest": max((d.get("updated_at") for d in data.values() if d.get("updated_at")), default=None),
    }


def _calendar_items() -> list[dict]:
    items = []
    for entry in getattr(config, "CALENDAR_EVENTS", []) or []:
        if not isinstance(entry, dict):
            continue
        dt = _parse_dt(entry.get("time"))
        title = str(entry.get("title", "")).strip()
        if not dt or not title:
            continue
        items.append({"time": dt, "title": title})
    items.sort(key=lambda item: item["time"])
    return items


def _estimate_price_window(hours: list[dict]) -> tuple[str, str]:
    if len(hours) < 3:
        return "--", "--"
    best_start = 0
    best_total = float("inf")
    for index in range(len(hours) - 2):
        window_total = sum(float(hours[index + offset].get("price_with_tax", 0.0)) for offset in range(3))
        if window_total < best_total:
            best_total = window_total
            best_start = index
    start_hour = hours[best_start].get("hour")
    end_hour = hours[best_start + 2].get("hour")
    return f"{int(start_hour):02d}:00", f"{(int(end_hour) + 1) % 24:02d}:00"


# ─────────────────────────────────────────────────────────────────────────────
# E1002 colour dashboard – flat design, Finnish labels, no border boxes
# ─────────────────────────────────────────────────────────────────────────────

# Colour palette
_E2_BG    = (233, 241, 251)   # page background
_E2_W     = (255, 255, 255)   # panel background
_E2_SEP   = (208, 218, 234)   # thin separator lines
_E2_DARK  = (18,  22,  36)    # primary text
_E2_MID   = (86,  98,  120)   # secondary text
_E2_LIGHT = (150, 162, 184)   # label / unit text

_C_SAA    = (22,  108, 192)   # SÄÄ – blue
_C_KAL    = (124, 34,  164)   # KALENTERI – purple
_C_EHINTA = (208, 116, 0)     # ENERGIAN HINTA – amber
_C_AQI    = (0,   148, 118)   # ILMANLAATU – teal
_C_TODAY  = (188, 126, 0)     # TÄNÄÄN – amber-gold
_C_VAROIT = (192, 50,  42)    # VAROITUKSET – red
_C_TREND  = (0,   140, 132)   # TRENDIT – teal
_C_GREEN  = (10,  164, 74)    # cheap / good
_C_AMBER  = (226, 148, 0)     # moderate
_C_RED_E  = (200, 46,  38)    # expensive / bad


def _e2_txt(draw, xy, text, size, *, bold=False, fill=None, anchor="la"):
    """E1002 text – bypasses EPAPER_FORCE_BOLD for clean mixed-weight typography."""
    draw.text(xy, text, font=_font(size, bold),
              fill=fill if fill is not None else _E2_DARK, anchor=anchor)


def _e2_price_status(price):
    if price is None:
        return "Ei tietoa", _E2_LIGHT, _E2_MID
    if price >= ELEC_EXPENSIVE_THRESHOLD:
        return "Kallis \u2191", (250, 222, 220), _C_RED_E
    if price >= ELEC_CHEAP_THRESHOLD:
        return "Normaali \u2192", (255, 242, 208), _C_AMBER
    return "Halpa \u2193", (218, 244, 228), _C_GREEN


def _e2_price_color(price):
    if price is None:
        return _E2_LIGHT
    if price >= ELEC_EXPENSIVE_THRESHOLD:
        return _C_RED_E
    if price >= ELEC_CHEAP_THRESHOLD:
        return _C_AMBER
    return _C_GREEN


def _e2_bar_color(price, is_current=False):
    if is_current:
        return (28, 34, 54)
    if price >= ELEC_EXPENSIVE_THRESHOLD:
        return (205, 56, 48)
    if price >= ELEC_CHEAP_THRESHOLD:
        return (238, 166, 36)
    return (100, 196, 124)


def _e2_sparkline(draw, values, x, y, w, h, line_fill):
    """Borderless sparkline – None values skipped."""
    clean = [(i, float(v)) for i, v in enumerate(values) if isinstance(v, (int, float))]
    if len(clean) < 2:
        return
    n    = len(values)
    vmin = min(v for _, v in clean)
    vmax = max(v for _, v in clean)
    span = (vmax - vmin) or 1.0
    pts  = []
    for i, v in clean:
        px = x + (w - 1) * i / max(n - 1, 1)
        py = y + (h - 1) * (1.0 - (v - vmin) / span)
        pts.append((px, py))
    for j in range(len(pts) - 1):
        draw.line([pts[j], pts[j + 1]], fill=line_fill, width=2)


def _e2_temp_chart(draw, x, y, w, h, hourly):
    """Temperature line chart with Y labels and X hour ticks."""
    temps = [(i, float(hr["temperature"])) for i, hr in enumerate(hourly)
             if isinstance(hr.get("temperature"), (int, float))]
    if not temps:
        return
    n    = len(hourly)
    vmin = min(v for _, v in temps)
    vmax = max(v for _, v in temps)
    span = (vmax - vmin) or 1.0
    ax, ay = x + 22, y
    aw, ah = w - 24, h - 14
    draw.line([(ax, ay + ah), (ax + aw, ay + ah)], fill=_E2_SEP, width=1)
    _e2_txt(draw, (ax - 2, ay),          f"{vmax:.0f}", 9, fill=_E2_LIGHT, anchor="ra")
    _e2_txt(draw, (ax - 2, ay + ah - 9), f"{vmin:.0f}", 9, fill=_E2_LIGHT, anchor="ra")
    labelled: set = set()
    for i, hr in enumerate(hourly):
        hr_n = hr.get("hour")
        if isinstance(hr_n, int) and hr_n % 6 == 0 and hr_n not in labelled:
            px = ax + aw * i / max(n - 1, 1)
            _e2_txt(draw, (px, ay + ah + 2), f"{hr_n:02d}", 9, fill=_E2_LIGHT, anchor="ma")
            labelled.add(hr_n)
    pts = []
    for i, t in temps:
        px = ax + aw * i / max(n - 1, 1)
        py = ay + ah * (1.0 - (t - vmin) / span)
        pts.append((px, py))
    for j in range(len(pts) - 1):
        draw.line([pts[j], pts[j + 1]], fill=(44, 104, 196), width=2)


def _e2_rain_chart(draw, x, y, w, h, hourly):
    """Precipitation probability bar chart."""
    n    = len(hourly)
    pops = [float(hr.get("precip_prob") or 0) for hr in hourly]
    vmax = max(pops) if pops else 100.0
    if vmax < 1:
        vmax = 100.0
    ah = h - 14
    draw.line([(x, y + ah), (x + w - 2, y + ah)], fill=_E2_SEP, width=1)
    bw    = max(2, (w - 2 - max(n - 1, 0)) // max(n, 1))
    seen: set = set()
    for i, (pop, hr) in enumerate(zip(pops, hourly)):
        bh = max(0, int(ah * pop / vmax))
        bx = x + i * (bw + 1)
        if bh > 0:
            draw.rectangle([bx, y + ah - bh, bx + bw, y + ah], fill=(58, 136, 216))
        hr_n = hr.get("hour")
        if isinstance(hr_n, int) and hr_n % 6 == 0 and hr_n not in seen:
            _e2_txt(draw, (bx + bw // 2, y + ah + 2), f"{hr_n:02d}", 9, fill=_E2_LIGHT, anchor="ma")
            seen.add(hr_n)


def _e2_moon(draw, cx, cy, r, fill):
    """Crescent moon: filled circle minus offset circle."""
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)
    draw.ellipse([cx - r + r, cy - r, cx + r + r, cy + r], fill=_E2_W)


def _e2_section_title(draw, x, y, title, color):
    """Colored section title with thin left accent bar."""
    draw.rounded_rectangle([x + 10, y + 5, x + 14, y + 18], radius=2, fill=color)
    _e2_txt(draw, (x + 20, y + 4), title, 14, bold=True, fill=color)


# ── Section renderers ─────────────────────────────────────────────────────────

def _e2_draw_saa(draw, x, y, w, h, wx, ruuvi):
    _e2_section_title(draw, x, y, f"SÄÄ  {wx.get('location', '').upper()}", _C_SAA)
    if wx.get("error"):
        _e2_txt(draw, (x + 14, y + 22), wx["error"], 13, fill=_E2_LIGHT)
        return
    cur   = wx.get("current") or {}
    daily = wx.get("daily") or {}
    feels   = cur.get("feels_like")
    hum     = cur.get("humidity")
    wind    = cur.get("wind")
    cond    = cur.get("text", "")

    # Condition + details (icon & temp are now in the top header bar)
    _e2_txt(draw, (x + 14, y + 22), cond, 15, fill=_E2_MID)
    parts = []
    if feels is not None:
        parts.append(f"Tuntuu {feels:.0f}\u00b0")
    if hum is not None:
        parts.append(f"{hum:.0f}% RH")
    if wind is not None:
        parts.append(f"{wind:.0f} m/s")
    _e2_txt(draw, (x + 14, y + 38), "  \u2022  ".join(parts), 13, fill=_E2_LIGHT)

    sunrise_str = _format_clock(_parse_dt(daily.get("sunrise")))
    sunset_str  = _format_clock(_parse_dt(daily.get("sunset")))
    _draw_sun(draw, x + 18, y + 54, 5, fill=(220, 155, 0))
    _e2_txt(draw, (x + 34, y + 48), sunrise_str, 13, fill=_E2_MID)
    _e2_moon(draw, x + 94, y + 54, 6, fill=_E2_MID)
    _e2_txt(draw, (x + 106, y + 48), sunset_str, 13, fill=_E2_MID)

    hourly   = wx.get("hourly") or []
    upcoming = hourly[:8]
    strip_y  = y + 70
    if upcoming:
        col_w = (w - 12) // len(upcoming)
        for i, hr in enumerate(upcoming):
            cx      = x + 6 + i * col_w + col_w // 2
            hr_n    = hr.get("hour")
            t_hr    = hr.get("temperature")
            _e2_txt(draw, (cx, strip_y),
                    f"{hr_n:02d}" if isinstance(hr_n, int) else "--",
                    12, bold=True, fill=_E2_MID, anchor="ma")
            _draw_weather_icon(draw, hr.get("icon", "cloud"), cx - 10, strip_y + 12, 20)
            _e2_txt(draw, (cx, strip_y + 35),
                    f"{t_hr:.0f}\u00b0" if t_hr is not None else "--",
                    13, bold=True, fill=_E2_DARK, anchor="ma")

    chart_y    = y + 122
    chart_h_lo = 90   # fixed height – charts stay compact
    half_w     = (w - 12) // 2
    _e2_txt(draw, (x + 14,            chart_y - 12), "L\u00e4mp\u00f6tila (\u00b0C)",  10, fill=_E2_LIGHT)
    _e2_temp_chart(draw, x + 8,            chart_y, half_w, chart_h_lo, upcoming)
    _e2_txt(draw, (x + 20 + half_w,   chart_y - 12), "Sadem\u00e4\u00e4r\u00e4 (mm)", 10, fill=_E2_LIGHT)
    _e2_rain_chart(draw, x + 16 + half_w, chart_y, half_w, chart_h_lo, upcoming)


def _e2_draw_kalenteri(draw, x, y, w, h, cal):
    _e2_section_title(draw, x, y, "KALENTERI", _C_KAL)
    now = datetime.now(tz=_HELSINKI)
    if not cal:
        _e2_txt(draw, (x + 14, y + 40), "Ei kalenteritapahtumia", 13, fill=_E2_LIGHT)
        return
    next_item = None
    cy = y + 32
    for item in cal[:5]:
        dt    = item["time"]
        delta = int((dt - now).total_seconds() // 60)
        if delta < 0:
            continue
        if next_item is None:
            next_item = (item, delta)
        draw.rounded_rectangle([x + 10, cy, x + 56, cy + 20],
                               radius=10, fill=(240, 234, 250), outline=(210, 192, 234), width=1)
        _e2_txt(draw, (x + 33, cy + 10), _format_clock(dt), 11, bold=True, fill=_C_KAL, anchor="mm")
        _e2_txt(draw, (x + 62, cy + 2),  _fit(item["title"], 14, w - 74), 14)
        cy += 28
        if cy > y + 190:
            break
    if next_item:
        item, delta = next_item
        sep_y = max(cy + 4, y + 186)
        draw.line([(x + 10, sep_y), (x + w - 10, sep_y)], fill=_E2_SEP, width=1)
        _e2_txt(draw, (x + 14, sep_y + 8),  "Seuraava:", 12, fill=_E2_LIGHT)
        cd_str = str(delta)
        _e2_txt(draw, (x + 14, sep_y + 24), cd_str, 52, bold=True, fill=_C_KAL)
        cd_w = int(_font(52, True).getlength(cd_str))
        _e2_txt(draw, (x + 18 + cd_w, sep_y + 48), " min", 18, bold=True, fill=_C_KAL)
        _e2_txt(draw, (x + 14, sep_y + 90), _fit(item["title"], 14, w - 26), 14, fill=_E2_MID)


def _e2_draw_ehinta(draw, x, y, w, h, prices, hours):
    _e2_section_title(draw, x, y, "ENERGIAN HINTA", _C_EHINTA)
    current    = prices.get("current") or {}
    curr_price = current.get("price_with_tax")
    price_clr  = _e2_price_color(curr_price)
    stat_lbl, _, _ = _e2_price_status(curr_price)

    price_str = f"{curr_price:.1f}" if curr_price is not None else "--"
    _e2_txt(draw, (x + 14, y + 26), price_str, 44, bold=True, fill=price_clr)
    pw = int(_font(44, True).getlength(price_str))
    _e2_txt(draw, (x + 20 + pw, y + 40), "c/kWh",  16, fill=_E2_MID)
    _e2_txt(draw, (x + 20 + pw, y + 60), stat_lbl, 14, bold=True, fill=price_clr)

    _e2_txt(draw, (x + 14, y + 88), "T\u00e4n\u00e4\u00e4n", 12, bold=True, fill=_E2_MID)

    if hours:
        cxl, cyl  = x + 10, y + 104
        cw_l, cbh = w - 20, 90
        vals  = [float(hr.get("price_with_tax", 0.0)) for hr in hours]
        vmin  = min(vals + [0.0])
        vmax  = max(vals) or 1.0
        span  = (vmax - vmin) or 1.0
        n_h   = len(hours)
        bw    = max(3, (cw_l - (n_h - 1)) // n_h)
        base  = cyl + cbh
        for idx, hr in enumerate(hours):
            val = float(hr.get("price_with_tax", 0.0))
            bh  = max(1, int((val - vmin) / span * (cbh - 10)))
            bx  = cxl + idx * (bw + 1)
            draw.rounded_rectangle([bx, base - bh, bx + bw, base], radius=2,
                                   fill=_e2_bar_color(val, hr.get("is_current", False)))
        seen: set = set()
        for idx, hr in enumerate(hours):
            hr_n = hr.get("hour")
            if isinstance(hr_n, int) and hr_n % 6 == 0 and hr_n not in seen:
                bx = cxl + idx * (bw + 1) + bw // 2
                _e2_txt(draw, (bx, base + 2), f"{hr_n:02d}", 10, fill=_E2_LIGHT, anchor="ma")
                seen.add(hr_n)

    cheapest_start, cheapest_end = _estimate_price_window(hours)
    by = y + 200  # bar chart now 90 px tall, ends at y+194
    draw.rounded_rectangle([x + 10, by, x + w - 10, by + 44],
                           radius=10, fill=(220, 244, 230), outline=(168, 216, 186), width=1)
    _e2_txt(draw, (x + 18, by + 6),  "Edullisin aika t\u00e4n\u00e4\u00e4n", 11, fill=_C_AQI)
    _e2_moon(draw, x + w - 22, by + 22, 7, fill=_E2_MID)
    _e2_txt(draw, (x + 18, by + 22), f"{cheapest_start} \u2013 {cheapest_end}", 18, bold=True)

    # Päivän min / max + huomenna alin are shown in the top bar.


def _e2_draw_ilmanlaatu(draw, x, y, w, h, ruuvi):
    _e2_section_title(draw, x, y, "ILMANLAATU", _C_AQI)
    temp = ruuvi.get("temperature")
    hum  = ruuvi.get("humidity")
    ry   = y + 22
    for label, value in [
        ("PM2.5",      "--"),
        ("CO\u2082",   "--"),
        ("Kosteus",    f"{hum:.0f}%"   if hum  is not None else "--"),
        ("L\u00e4mp\u00f6tila", f"{temp:.0f}\u00b0C" if temp is not None else "--"),
    ]:
        _e2_txt(draw, (x + 12, ry),    label, 13, fill=_E2_LIGHT)
        _e2_txt(draw, (x + w - 8, ry), value, 17, bold=True, anchor="ra")
        ry += 20


def _e2_draw_tanaan(draw, x, y, w, h, wx, prices):
    _e2_section_title(draw, x, y, "T\u00c4N\u00c4\u00c4N", _C_TODAY)
    daily   = wx.get("daily") or {}
    current = prices.get("current") or {}
    temp    = (wx.get("current") or {}).get("temperature")
    rows = [
        ("Sunrise",  _format_clock(_parse_dt(daily.get("sunrise"))),    (220, 155,   0)),
        ("Sunset",   _format_clock(_parse_dt(daily.get("sunset"))),     _E2_MID),
        ("Rain",
         f"{daily.get('precip_sum'):.0f} mm" if daily.get("precip_sum") is not None else "--",
         (58, 136, 216)),
        ("Energia",
         f"{current.get('price_with_tax'):.1f} c" if current.get("price_with_tax") is not None else "--",
         _C_EHINTA),
        ("Ulkona",   f"{temp:.0f}\u00b0C" if temp is not None else "--", _C_SAA),
    ]
    ry = y + 20
    for label, value, dot_fill in rows:
        draw.ellipse([x + 13, ry + 2, x + 21, ry + 10], fill=dot_fill)
        _e2_txt(draw, (x + 28, ry),    label, 13, fill=_E2_LIGHT)
        _e2_txt(draw, (x + w - 8, ry), value, 16, bold=True, anchor="ra")
        ry += 18


def _e2_draw_varoitukset(draw, x, y, w, h, wx, prices, cal, ruuvi):
    _e2_section_title(draw, x, y, "VAROITUKSET", _C_VAROIT)
    warn_list = []
    if wx.get("error"):
        warn_list.append(wx["error"])
    if prices.get("error"):
        warn_list.append("Ei hintadataa")
    if not warn_list:
        cx_w = x + w // 2
        cy_w = y + h // 2 + 6
        r_w  = 14
        draw.ellipse([cx_w - r_w, cy_w - r_w, cx_w + r_w, cy_w + r_w],
                     fill=(218, 246, 228), outline=(148, 208, 168), width=1)
        draw.line([(cx_w - 7, cy_w + 1), (cx_w - 1, cy_w + 7)], fill=(18, 154, 66), width=2)
        draw.line([(cx_w - 1, cy_w + 7), (cx_w + 9, cy_w - 6)], fill=(18, 154, 66), width=2)
        _e2_txt(draw, (cx_w, cy_w + r_w + 4),  "Ei aktiivisia", 12, fill=_E2_MID, anchor="ma")
        _e2_txt(draw, (cx_w, cy_w + r_w + 18), "varoituksia",   12, fill=_E2_MID, anchor="ma")
    else:
        wy = y + 28
        for wt in warn_list[:3]:
            _e2_txt(draw, (x + 12, wy), _fit(wt, 13, w - 24), 13, fill=_C_VAROIT)
            wy += 18


def _e2_draw_trendit(draw, x, y, w, h, wx, prices, ruuvi):
    _e2_section_title(draw, x, y, "TRENDIT (24H)", _C_TREND)
    hourly      = wx.get("hourly") or []
    price_hours = prices.get("hours") or []
    temp_vals   = [hr.get("temperature") for hr in hourly[:24]]
    press_v     = ruuvi.get("pressure")
    press_vals  = ([press_v] * len(temp_vals)) if press_v is not None else []
    price_vals  = [float(h.get("price_with_tax", 0.0)) for h in price_hours[:24]]
    pop_vals    = [float(hr.get("precip_prob") or 0) for hr in hourly[:24]]

    charts = [
        ("L\u00e4mp\u00f6tila",    "\u00b0C",    temp_vals,  (44,  104, 196)),
        ("Ilmanpaine",              "hPa",        press_vals, (138, 78,  176)),
        ("S\u00e4hk\u00f6n hinta", "c/kWh",      price_vals, (214, 128, 0)),
        ("S\u00e4hk\u00f6n kulutus","kWh",        [],         (48,  158, 78)),
        ("PM2.5",                   "\u03bcg/m\u00b3", [],    (198, 68,  48)),
        ("Kosteus",                 "%",          pop_vals,   (0,   138, 118)),
    ]
    n_ch = len(charts)
    ch_w = (w - 8) // n_ch
    ct   = y + 22
    ca_h = h - 28

    for i, (title, unit, vals, line_fill) in enumerate(charts):
        cx_t = x + 4 + i * ch_w
        _e2_txt(draw, (cx_t + ch_w // 2, ct - 2), _fit(title, 11, ch_w - 2), 11,
                fill=_E2_MID, anchor="ma")
        clean_v = [v for v in vals if isinstance(v, (int, float))]
        if clean_v:
            vmin_t, vmax_t = min(clean_v), max(clean_v)
            _e2_txt(draw, (cx_t,             ct + 6),         f"{vmax_t:.0f}", 10, fill=_E2_LIGHT)
            _e2_txt(draw, (cx_t,             ct + ca_h - 16), f"{vmin_t:.0f}", 10, fill=_E2_LIGHT)
            _e2_txt(draw, (cx_t + ch_w // 2, ct + ca_h - 6), unit, 9, fill=_E2_LIGHT, anchor="ma")
        _e2_sparkline(draw, vals, cx_t + 8, ct + 4, ch_w - 16, ca_h - 18, line_fill)


def _render_e1002() -> Image.Image:
    W, H = 800, 480
    img  = Image.new("RGB", (W, H), _E2_BG)
    draw = ImageDraw.Draw(img)

    HEADER_H = 52
    MAIN_Y   = HEADER_H + 1
    MAIN_H   = 286    # larger bottom bar to remove dead space and improve readability
    BOT_Y    = MAIN_Y + MAIN_H + 1
    BOT_H    = H - BOT_Y

    # White panel backgrounds
    draw.rectangle([0, 0,      W, HEADER_H],         fill=_E2_W)
    draw.rectangle([0, MAIN_Y, W, MAIN_Y + MAIN_H],  fill=_E2_W)
    draw.rectangle([0, BOT_Y,  W, H],                 fill=_E2_W)

    # ── Header ───────────────────────────────────────────────────────────────
    now        = datetime.now(tz=_HELSINKI)
    prices     = electricity.get_prices()
    curr_price = (prices.get("current") or {}).get("price_with_tax")
    price_clr  = _e2_price_color(curr_price)
    stat_lbl, _, _ = _e2_price_status(curr_price)
    fi_wd      = _FI_WEEKDAYS[now.weekday()]
    wx         = weather.get_weather()   # fetched early for the header widget

    # Calendar icon
    draw.rounded_rectangle([14, 14, 26, 24], radius=2, outline=_E2_DARK, width=1)
    draw.line([(18, 12), (18, 16)], fill=_E2_DARK, width=1)
    draw.line([(22, 12), (22, 16)], fill=_E2_DARK, width=1)
    draw.line([(14, 18), (26, 18)], fill=_E2_DARK, width=1)
    _e2_txt(draw, (32, 12), f"{fi_wd} {now.day}.{now.month}.{now.year}", 24, bold=True)
    _e2_txt(draw, (32, 38), f"Päivitetty {now.strftime('%H:%M')}", 12, fill=_E2_LIGHT)

    # Current weather widget – between date and price
    _hdr_cur  = wx.get("current") or {}
    _hdr_temp = _hdr_cur.get("temperature")
    _hdr_icon = _hdr_cur.get("icon", "cloud")
    _hdr_cond = _hdr_cur.get("text", "")
    _hdr_loc  = wx.get("location", "")
    _draw_weather_icon(draw, _hdr_icon, 296, 2, 26)
    _e2_txt(draw, (326, 6),  f"{_hdr_temp:.0f}\u00b0" if _hdr_temp is not None else "--", 22, bold=True)
    _e2_txt(draw, (326, 30), f"{_hdr_cond}  \u2022  {_hdr_loc}", 12, fill=_E2_MID)

    # Päivän min / max between weather and price
    _hdr_hrs  = prices.get("hours") or []
    if _hdr_hrs:
        _hv       = [float(h.get("price_with_tax", 0.0)) for h in _hdr_hrs]
        _hdr_minp = min(_hv)
        _hdr_maxp = max(_hv)
        _hdr_mhr  = next((h for h in _hdr_hrs if abs(float(h.get("price_with_tax", 99)) - _hdr_minp) < 0.01), None)
        _hdr_xhr  = next((h for h in _hdr_hrs if abs(float(h.get("price_with_tax", 99)) - _hdr_maxp) < 0.01), None)
        _e2_txt(draw, (492, 6),  "Min", 10, fill=_E2_LIGHT)
        _e2_txt(draw, (492, 18), f"{_hdr_minp:.1f} c", 16, bold=True, fill=_C_GREEN)
        if _hdr_mhr:
            h_n = _hdr_mhr.get("hour", 0)
            _e2_txt(draw, (492, 34), f"{h_n:02d}\u2013{(h_n+1)%24:02d}h", 9, fill=_E2_LIGHT)
        _e2_txt(draw, (558, 6),  "Max", 10, fill=_E2_LIGHT)
        _e2_txt(draw, (558, 18), f"{_hdr_maxp:.1f} c", 16, bold=True, fill=_C_RED_E)
        if _hdr_xhr:
            h_n = _hdr_xhr.get("hour", 0)
            _e2_txt(draw, (558, 34), f"{h_n:02d}\u2013{(h_n+1)%24:02d}h", 9, fill=_E2_LIGHT)

    price_str = f"{curr_price:.1f} c/kWh" if curr_price is not None else "-- c/kWh"
    _e2_txt(draw, (W - 14, 10), price_str,  28, bold=True, fill=price_clr, anchor="ra")
    _e2_txt(draw, (W - 14, 36), stat_lbl,   15, bold=True, fill=price_clr, anchor="ra")
    draw.line([(0, HEADER_H), (W, HEADER_H)], fill=_E2_SEP, width=1)

    # ── Data ─────────────────────────────────────────────────────────────────
    ruuvi = _aggregate_ruuvi()
    cal   = _calendar_items()
    hours = prices.get("hours") or []

    # ── Main panels ───────────────────────────────────────────────────────────
    SAA_X, SAA_W = 0,   358
    KAL_X, KAL_W = 360, 178
    EL_X,  EL_W  = 540, 260

    _e2_draw_saa      (draw, SAA_X + 8, MAIN_Y + 6, SAA_W - 16, MAIN_H - 12, wx, ruuvi)
    draw.line([(SAA_X + SAA_W, MAIN_Y + 14), (SAA_X + SAA_W, MAIN_Y + MAIN_H - 14)],
              fill=_E2_SEP, width=1)
    _e2_draw_kalenteri(draw, KAL_X + 8, MAIN_Y + 6, KAL_W - 16, MAIN_H - 12, cal)
    draw.line([(EL_X, MAIN_Y + 14), (EL_X, MAIN_Y + MAIN_H - 14)], fill=_E2_SEP, width=1)
    _e2_draw_ehinta   (draw, EL_X + 8,  MAIN_Y + 6, EL_W - 16,  MAIN_H - 12, prices, hours)

    # ── Bottom row ────────────────────────────────────────────────────────────
    draw.line([(0, BOT_Y), (W, BOT_Y)], fill=_E2_SEP, width=1)

    AQ_X, AQ_W = 0,   170
    TD_X, TD_W = 172, 170
    TR_X, TR_W = 344, 456

    _e2_draw_ilmanlaatu (draw, AQ_X + 8, BOT_Y + 2, AQ_W - 14, BOT_H - 3,  ruuvi)
    draw.line([(AQ_X + AQ_W, BOT_Y + 10), (AQ_X + AQ_W, H - 10)], fill=_E2_SEP, width=1)
    _e2_draw_tanaan     (draw, TD_X + 8, BOT_Y + 2, TD_W - 14, BOT_H - 3,  wx, prices)
    draw.line([(TD_X + TD_W, BOT_Y + 10), (TD_X + TD_W, H - 10)], fill=_E2_SEP, width=1)
    _e2_draw_trendit    (draw, TR_X + 4, BOT_Y + 2, TR_W - 8,  BOT_H - 3,  wx, prices, ruuvi)

    if config.EPAPER_ROTATE:
        img = img.rotate(config.EPAPER_ROTATE, expand=True)
    return img


# ─────────────────────────────────────────────────────────────────────────────
# E1002 colour calendar – pixel twin of templates/epaper_e1002_calendar.html
#
# The 7.3" panel cannot run a browser, so this redraws the same design with
# Pillow. Keep the constants below in sync with the stylesheet.
# ─────────────────────────────────────────────────────────────────────────────

_CAL_SLATE = (169, 178, 189)
_CAL_PAPER = (255, 255, 255)
_CAL_INK = (0, 0, 0)
_CAL_MUTED = (106, 116, 130)

_CAL_ACCENT = {
    "red": (208, 0, 0),
    "green": (0, 144, 40),
    "blue": (0, 48, 168),
    "yellow": (240, 192, 0),
}
_CAL_TINT = {
    "red": (251, 233, 231),
    "green": (230, 244, 234),
    "blue": (232, 237, 250),
    "yellow": (252, 243, 218),
}

_CAL_TOP_H = 62
_CAL_FOOT_H = 34
_CAL_ROW_H = 42
_CAL_ROW_GAP = 5
_CAL_HEAD_H = 26
_CAL_SHEET_GAP = 8


def _cal_text(draw, xy, text, size, *, bold=False, fill=_CAL_INK, anchor="la"):
    """Plain text helper – bypasses EPAPER_FORCE_BOLD to keep mixed weights."""
    draw.text(xy, text, font=_font(size, bold), fill=fill, anchor=anchor)


def _cal_tracked(draw, xy, text, size, spacing, *, bold=True, fill=_CAL_INK):
    """Draw letter-spaced uppercase text; Pillow has no tracking of its own."""
    x, y = xy
    font = _font(size, bold)
    for char in text:
        draw.text((x, y), char, font=font, fill=fill)
        x += font.getlength(char) + spacing
    return x


def _cal_draw_topbar(draw, w, summary):
    draw.rectangle([0, 0, w, _CAL_TOP_H], fill=_CAL_INK)
    col_w = w / 3
    unit = summary.get("unit", "c/kWh")
    peak = summary.get("peak") or {}
    cheap = summary.get("cheapest") or {}
    columns = [
        ("AVERAGE", f"{summary.get('average')}", f" {unit}", _CAL_PAPER, "", None),
        ("PEAK", f"{peak.get('price')}", " c",
         _CAL_ACCENT["yellow"], f"{peak.get('start')}–{peak.get('end')}", _CAL_ACCENT["red"]),
        ("CHEAPEST", f"{cheap.get('price')}", " c",
         _CAL_ACCENT["green"], f"{cheap.get('start')}–{cheap.get('end')}", _CAL_ACCENT["green"]),
    ]

    for i, (label, value, value_unit, value_fill, range_text, chip) in enumerate(columns):
        x = int(i * col_w) + 22
        if i:
            sep = int(i * col_w)
            draw.line([(sep, 14), (sep, _CAL_TOP_H - 14)], fill=_CAL_PAPER, width=1)
        _cal_tracked(draw, (x, 12), label, 11, 2.5, fill=_CAL_PAPER)
        baseline = 48
        value_x = x + _font(27, True).getlength(value)
        _cal_text(draw, (x, baseline), value, 27, bold=True, fill=value_fill, anchor="ls")
        _cal_text(draw, (value_x, baseline), value_unit, 14, bold=True, fill=value_fill, anchor="ls")
        if range_text:
            unit_w = _font(14, True).getlength(value_unit)
            _cal_text(draw, (value_x + unit_w + 10, baseline), range_text, 13,
                      bold=False, fill=_CAL_PAPER, anchor="ls")
        if chip:
            cx = int((i + 1) * col_w) - 18
            draw.rounded_rectangle([cx, 12, cx + 10, 22], radius=2, fill=chip)


def _cal_draw_section(draw, x, y, w, h, section):
    draw.rounded_rectangle([x, y, x + w, y + h], radius=10, fill=_CAL_PAPER)

    inner_x = x + 14
    inner_w = w - 28
    head_baseline = y + 8
    end_x = _cal_tracked(draw, (inner_x, head_baseline), section["title"].upper(), 16, 4)
    _cal_text(draw, (end_x + 10, head_baseline + 3), section["date"], 13,
              bold=True, fill=_CAL_ACCENT["blue"])
    count = f"{len(section['events'])} EVENTS"
    _cal_tracked(draw, (x + w - 14 - _text_width(count, 11, True) - 1.5 * len(count),
                        head_baseline + 4), count, 11, 1.5, fill=_CAL_MUTED)
    rule_y = y + _CAL_HEAD_H
    draw.rectangle([inner_x, rule_y, inner_x + inner_w, rule_y + 1], fill=_CAL_INK)

    row_y = rule_y + 7
    if not section["events"]:
        _cal_text(draw, (inner_x, row_y + 6), "Nothing scheduled", 16,
                  bold=True, fill=_CAL_MUTED)
        return

    for event in section["events"]:
        if row_y + _CAL_ROW_H > y + h:
            break
        category = event.get("category", "blue")
        draw.rounded_rectangle([inner_x, row_y, inner_x + inner_w, row_y + _CAL_ROW_H],
                               radius=6, fill=_CAL_TINT.get(category, _CAL_TINT["blue"]))
        # Colour bar flush with the rounded card edge.
        draw.rectangle([inner_x + 3, row_y, inner_x + 10, row_y + _CAL_ROW_H],
                       fill=_CAL_ACCENT.get(category, _CAL_ACCENT["blue"]))
        draw.rounded_rectangle([inner_x, row_y, inner_x + 8, row_y + _CAL_ROW_H],
                               radius=6, fill=_CAL_ACCENT.get(category, _CAL_ACCENT["blue"]))

        mid_y = row_y + _CAL_ROW_H / 2 + 1
        text_x = inner_x + 22
        start = str(event.get("start", ""))
        _cal_text(draw, (text_x, mid_y), start, 18, bold=True, anchor="lm")
        _cal_text(draw, (text_x + _text_width(start, 18, True) + 6, mid_y),
                  f"– {event.get('end', '')}", 18, fill=_CAL_MUTED, anchor="lm")
        title_x = text_x + 130
        _cal_text(draw, (title_x, mid_y),
                  _fit(str(event.get("title", "")), 20, inner_x + inner_w - 12 - title_x),
                  20, bold=True, anchor="lm")
        row_y += _CAL_ROW_H + _CAL_ROW_GAP


def _cal_draw_legend(draw, w, h, updated):
    top = h - _CAL_FOOT_H
    draw.rectangle([0, top, w, h], fill=_CAL_ACCENT["blue"])
    mid_y = top + _CAL_FOOT_H / 2
    x = 26
    for category, label in (("red", "PRIORITY"), ("green", "PERSONAL"),
                            ("blue", "WORK"), ("yellow", "HEALTH")):
        chip = [x, mid_y - 6, x + 12, mid_y + 6]
        # The blue chip would vanish on the blue bar, so every chip gets a keyline.
        draw.rounded_rectangle(chip, radius=3, fill=_CAL_ACCENT[category],
                               outline=_CAL_PAPER, width=1)
        x += 20
        x = _cal_tracked(draw, (x, mid_y - 7), label, 12, 1.5, fill=_CAL_PAPER) + 24

    stamp = f"UPDATED {updated}"
    stamp_w = _text_width(stamp, 12, True) + 1.5 * len(stamp)
    _cal_tracked(draw, (w - 26 - stamp_w, mid_y - 7), stamp, 12, 1.5, fill=_CAL_PAPER)


def _render_e1002_calendar() -> Image.Image:
    w, h = 800, 480
    img = Image.new("RGB", (w, h), _CAL_SLATE)
    draw = ImageDraw.Draw(img)

    _cal_draw_topbar(draw, w, calendar_view.summary())

    sections = calendar_view.sections()
    body_top = _CAL_TOP_H + 8
    body_bottom = h - _CAL_FOOT_H - 8
    sheet_x = 20
    sheet_w = w - 40

    today, tomorrow = sections[0], sections[1]
    today_h = _cal_sheet_height(today)
    tomorrow_top = body_top + today_h + _CAL_SHEET_GAP

    _cal_draw_section(draw, sheet_x, body_top, sheet_w, today_h, today)
    _cal_draw_section(draw, sheet_x, tomorrow_top, sheet_w,
                      max(_cal_sheet_height(tomorrow), body_bottom - tomorrow_top), tomorrow)

    _cal_draw_legend(draw, w, h, datetime.now(tz=_HELSINKI).strftime("%H:%M"))

    if config.EPAPER_ROTATE:
        img = img.rotate(config.EPAPER_ROTATE, expand=True)
    return img


def _cal_sheet_height(section: dict) -> int:
    rows = len(section["events"])
    body = (rows * (_CAL_ROW_H + _CAL_ROW_GAP) - _CAL_ROW_GAP) if rows else 34
    return _CAL_HEAD_H + 7 + body + 6
