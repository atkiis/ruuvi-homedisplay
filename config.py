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
        "mac": "AA:BB:CC:DD:EE:01",   # ← replace with actual MAC
        "name": "Sauna",
        "icon": "🔥",
        "temp_min": 20,
        "temp_max": 120,
    },
    "balcony": {
        "mac": "EC:40:38:F3:A9:2E",   # ← replace with actual MAC
        "name": "Balcony",
        "icon": "🌤",
        "temp_min": -50,
        "temp_max": 50,
    },
    "freezer": {
        "mac": "E1:AC:E3:FB:70:91",   # ← replace with actual MAC
        "name": "Freezer",
        "icon": "❄️",
        "temp_min": -40,
        "temp_max": 10,
    },
}

# How often the background Ruuvi scan should attempt to read fresh data (seconds).
RUUVI_SCAN_INTERVAL = 60

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
