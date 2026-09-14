# ---------------------------------------------------------------------------
# config.py – Application configuration
#
# Edit the values below to match your setup before running the app.
# ---------------------------------------------------------------------------

# ── Ruuvi tags ──────────────────────────────────────────────────────────────
# Replace MAC addresses with the ones printed on the back of your tags.
# temp_min / temp_max define the expected range shown as a gauge on the UI.

RUUVI_TAGS = {
    "sauna": {
        "mac": "",   # ← replace with actual MAC
        "name": "Sauna",
        "icon": "🔥",
        "temp_min": 20,
        "temp_max": 120,
    },
    "balcony": {
        "mac": "",   # ← replace with actual MAC
        "name": "Balcony",
        "icon": "🌤",
        "temp_min": -50,
        "temp_max": 50,
    },
    "freezer": {
        "mac": "",   # ← replace with actual MAC
        "name": "Freezer",
        "icon": "❄️",
        "temp_min": -40,
        "temp_max": 10,
    },
}

# How often the background Ruuvi scan should attempt to read fresh data (seconds).
RUUVI_SCAN_INTERVAL = 60

# Data older than this many minutes is shown with a staleness label (e.g. "45 min")
# on the e-paper sensor strip instead of the humidity reading, and the temperature
# is greyed out.  Useful for tags that are occasionally out of BLE range.
RUUVI_STALE_MINUTES = 10

# ── Bus stops ───────────────────────────────────────────────────────────────
# Use the Digitransit stop finder to get your stop IDs:
#   https://reittiopas.hsl.fi  (HSL area)
# or query the stops by name:
#   https://api.digitransit.fi/routing/v1/routers/hsl/index/graphql
#
# For cities outside the HSL area (Tampere, Oulu, …) change DIGITRANSIT_API_URL
# to the corresponding router below.

BUS_STOPS = [
    {
        "id": "HSL:1040602",   # ← replace with your nearest stop ID
        "name": "Bus Stop 1",  # ← human-readable label shown on screen
    },
    {
        "id": "HSL:1040603",   # ← replace with your nearest stop ID
        "name": "Bus Stop 2",
    },
]

# Number of next departures to show per stop.
BUS_DEPARTURES_COUNT = 6

# ── Digitransit API ─────────────────────────────────────────────────────────
# HSL (Helsinki metropolitan area):
# DIGITRANSIT_API_URL = "https://api.digitransit.fi/routing/v1/routers/hsl/index/graphql"
DIGITRANSIT_API_URL = "https://api.digitransit.fi/routing/v1/routers/waltti/index/graphql"
# Tampere: "https://api.digitransit.fi/routing/v1/routers/waltti/index/graphql"
# Oulu / national: "https://api.digitransit.fi/routing/v1/routers/finland/index/graphql"

# Optional subscription key – required for production use; see
# https://portal-api.digitransit.fi/  (free registration).
# Leave empty for low-volume development / home use.
DIGITRANSIT_API_KEY = ""

# ── Electricity price API ───────────────────────────────────────────────────
# spot-hinta.fi is a free, open Finnish electricity price API.
ELECTRICITY_API_URL = "https://api.spot-hinta.fi/Today"

# ── Weather API (Open-Meteo) ────────────────────────────────────────────────
# Open-Meteo (https://open-meteo.com) is free and needs no API key. Set the
# coordinates of the location you want the forecast for.
WEATHER_API_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_LAT = 61.4481      # ← your latitude  (default: Partola, Pirkkala)
WEATHER_LON = 23.6450      # ← your longitude
WEATHER_NAME = "Partola"   # Human-readable label shown on screen.

# ── General display settings ────────────────────────────────────────────────
# Page auto-refresh interval in seconds (also controls polling from browser).
REFRESH_INTERVAL = 60

# Set to True to use simulated sensor data (useful when Bluetooth is unavailable).
DEMO_MODE = True

# ── E-paper display (Seeed reTerminal E1001 and similar) ─────────────────────
# The reTerminal E1001 is a 7.5" 800x480 monochrome e-paper device on an
# ESP32-S3. It can't run a browser, so its firmware fetches a pre-rendered
# image from this server instead:
#   /epaper.png  → ESPHome `online_image` (recommended, see esphome/ folder)
#   /epaper.bmp  → 1-bit BMP for bare Arduino sketches
#   /epaper      → HTML preview for tweaking the layout in a desktop browser
EPAPER_WIDTH = 800        # Panel width in pixels.
EPAPER_HEIGHT = 480       # Panel height in pixels.
EPAPER_ROTATE = 0         # Rotate the rendered image (0/90/180/270) if mounted sideways.
# Set True only for the 6-colour reTerminal E1002; adds red highlights for
# expensive electricity hours. Leave False for the monochrome E1001.
EPAPER_COLOR = False
# E1001 only: invert black<->white when the monochrome panel needs opposite
# pixel polarity from the preview.
EPAPER_INVERT = True
# E1002 only: independently invert the colour calendar image when needed.
EPAPER_E1002_INVERT = False
# Layout orientation: "landscape" (800x480, panel horizontal) or
# "portrait" (480x800, panel mounted vertically).  All /epaper endpoints
# will serve the chosen layout automatically.
EPAPER_ORIENTATION = "landscape"
# Default e-paper dashboard layout. Keep "e1001" for the current mono layout;
# set "e1002" for the 7.3" colour calendar view (same design as the HTML
# preview at /epaper-e1002), or "e1002-dashboard" for the older colour
# weather/price dashboard. Can also be passed as ?layout=... in the URL.
EPAPER_LAYOUT = "e1001"
# Force all text to bold weight.  Recommended for most monochrome e-paper
# panels where thin strokes are too faint to read at normal viewing distance.
EPAPER_FORCE_BOLD = True

# Optional calendar feed for the E1002 layout. Add items as:
#   {"time": "2026-07-06T18:30:00+03:00", "title": "Dentist"}
# Optional extra keys used by the 7.3" colour calendar view (/epaper-e1002):
#   "end": "2026-07-06T19:15:00+03:00", "category": "red"|"green"|"blue"|"yellow"
# The renderer shows upcoming entries in the calendar panel; leave empty if
# you do not want a calendar block yet.
CALENDAR_EVENTS = []

# Uploaded E1002 calendar data is stored as normalized events, not raw ICS.
CALENDAR_STORAGE_PATH = "instance/calendar.json"
CALENDAR_TIMEZONE = "Europe/Helsinki"
CALENDAR_MAX_UPLOAD_BYTES = 1024 * 1024
CALENDAR_LOOKAHEAD_DAYS = 7

# Fallback agenda shown on the colour calendar view while CALENDAR_EVENTS is
# empty. "day" is either "today" or "tomorrow".
CALENDAR_DEMO_EVENTS = [
    {"day": "today", "start": "09:00", "end": "10:00", "category": "red",
     "title": "Team Sync & Project Kickoff"},
    {"day": "today", "start": "11:30", "end": "12:30", "category": "green",
     "title": "Lunch with Client (Downtown)"},
    {"day": "today", "start": "14:00", "end": "15:30", "category": "blue",
     "title": "Sprint Review & Demo"},
    {"day": "today", "start": "18:00", "end": "19:00", "category": "yellow",
     "title": "Workout at Gym"},
    {"day": "tomorrow", "start": "08:30", "end": "09:15", "category": "blue",
     "title": "Quarterly Planning Session"},
    {"day": "tomorrow", "start": "19:30", "end": "21:00", "category": "yellow",
     "title": "Family Dinner"},
]

# Category → e-paper palette colour used for the event badge.
CALENDAR_CATEGORY_COLORS = {
    "red": "#c00000",      # priority
    "green": "#008000",    # personal
    "blue": "#0040c0",     # work
    "yellow": "#e0b000",   # health / fitness
}

# ── Sauna view ───────────────────────────────────────────────────────────────
# When the sauna tag temperature rises above SAUNA_TEMP_THRESHOLD the e-paper
# automatically switches to a dedicated sauna view that shows the temperature,
# humidity, and a running count of löyly (steam throws) detected from RH spikes.
SAUNA_TAG_KEY = "sauna"    # Key in RUUVI_TAGS that is the sauna sensor.
SAUNA_TEMP_THRESHOLD = 75  # °C – above this the sauna view is activated.
LOYLY_RH_SPIKE = 5.0       # Minimum RH % increase to count as one löyly.
