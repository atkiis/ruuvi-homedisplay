"""
weather.py – Local weather forecast from the free Open-Meteo API.

Open-Meteo (https://open-meteo.com) needs no API key and returns current
conditions plus an hourly/daily forecast for any latitude/longitude. The
location (Partola, Pirkkala by default) is configured in config.py.

Results are cached for 15 minutes so rapid page / e-paper refreshes don't hammer
the upstream service.
"""

import logging
import threading
import time
from zoneinfo import ZoneInfo

import requests

import config

logger = logging.getLogger(__name__)

_HELSINKI = ZoneInfo("Europe/Helsinki")

_cache: dict = {}
_cache_ts: float = 0.0
_lock = threading.Lock()
_refresh_lock = threading.Lock()
_CACHE_TTL = 900  # 15 minutes
_REFRESH_INTERVAL = 900  # background refresh cadence (seconds)

# WMO weather interpretation codes → short, e-ink friendly Finnish text.
_WMO = {
    0: "Selkeää",
    1: "Enimm. selkeää",
    2: "Puolipilvistä",
    3: "Pilvistä",
    45: "Sumua",
    48: "Huurresumua",
    51: "Tihkua",
    53: "Tihkua",
    55: "Sankkaa tihkua",
    56: "Jäätihkua",
    57: "Jäätihkua",
    61: "Heikkoa sadetta",
    63: "Sadetta",
    65: "Rankkasadetta",
    66: "Jäätävää sadetta",
    67: "Jäätävää sadetta",
    71: "Heikkoa lumis.",
    73: "Lumisadetta",
    75: "Sankkaa lumis.",
    77: "Lumijyväsiä",
    80: "Sadekuuroja",
    81: "Sadekuuroja",
    82: "Rankkakuuroja",
    85: "Lumikuuroja",
    86: "Lumikuuroja",
    95: "Ukkosta",
    96: "Ukkosta",
    99: "Ukkosta",
}

# WMO code → simple icon category used by the e-paper renderer's hand-drawn
# weather icons (font-independent so it looks the same on every panel).
_WMO_ICON = {
    0: "sun",
    1: "sun",
    2: "partly",
    3: "cloud",
    45: "fog",
    48: "fog",
    51: "drizzle",
    53: "drizzle",
    55: "drizzle",
    56: "drizzle",
    57: "drizzle",
    61: "rain",
    63: "rain",
    65: "rain",
    66: "rain",
    67: "rain",
    71: "snow",
    73: "snow",
    75: "snow",
    77: "snow",
    80: "rain",
    81: "rain",
    82: "rain",
    85: "snow",
    86: "snow",
    95: "thunder",
    96: "thunder",
    99: "thunder",
}


def code_text(code) -> str:
    """Human-readable description for a WMO weather code."""
    try:
        return _WMO.get(int(code), "—")
    except (TypeError, ValueError):
        return "—"


def code_icon(code) -> str:
    """Icon category ('sun', 'cloud', 'rain', …) for a WMO weather code."""
    try:
        return _WMO_ICON.get(int(code), "cloud")
    except (TypeError, ValueError):
        return "cloud"


def get_weather() -> dict:
    """
    Return a dict with:
        location – display name
        current  – {temperature, humidity, wind, code, text} or None
        hourly   – list of {time, hour, code, text, temperature, precip_prob} for the next hours
        error    – present only if the fetch failed and no cache exists

    Always returns cached data instantly. On a cold cache it performs one
    synchronous fetch; thereafter the background thread keeps it fresh.
    """
    with _lock:
        cache = _cache
    if not cache:
        _refresh()  # cold-start fallback
        with _lock:
            cache = _cache
    if not cache:
        return {"location": config.WEATHER_NAME, "current": None, "hourly": [],
                "error": "Säätiedot eivät saatavilla"}
    return cache


def start() -> None:
    """Start a daemon thread that keeps the weather cache warm in the background."""
    def _loop() -> None:
        while True:
            _refresh()
            time.sleep(_REFRESH_INTERVAL)

    threading.Thread(target=_loop, daemon=True, name="weather-refresh").start()


def _refresh() -> None:
    """Fetch fresh weather and atomically replace the cache. Network runs outside
    the data lock; ``_refresh_lock`` serialises concurrent refreshes."""
    global _cache, _cache_ts
    with _refresh_lock:
        with _lock:
            if _cache and (time.monotonic() - _cache_ts) < _CACHE_TTL:
                return
        params = {
            "latitude": config.WEATHER_LAT,
            "longitude": config.WEATHER_LON,
            "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
            "hourly": "weather_code,temperature_2m,precipitation_probability",
            "timezone": "Europe/Helsinki",
            "forecast_days": 2,
        }
        try:
            resp = requests.get(config.WEATHER_API_URL, params=params, timeout=10)
            resp.raise_for_status()
            parsed = _parse(resp.json())
        except requests.RequestException as exc:
            logger.warning("Failed to fetch weather: %s", exc)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unexpected error fetching weather: %s", exc)
            return
        with _lock:
            _cache = parsed
            _cache_ts = time.monotonic()
        logger.info("Weather refreshed for %s.", config.WEATHER_NAME)


# Number of upcoming hourly forecast entries to expose (current hour + next 8).
_HOURLY_COUNT = 9


def _parse(raw: dict) -> dict:
    cur = raw.get("current") or {}
    code = cur.get("weather_code")
    current = {
        "temperature": cur.get("temperature_2m"),
        "humidity": cur.get("relative_humidity_2m"),
        "wind": cur.get("wind_speed_10m"),
        "code": code,
        "text": code_text(code),
        "icon": code_icon(code),
    }

    from datetime import datetime

    hourly = []
    h = raw.get("hourly") or {}
    times = h.get("time") or []
    codes = h.get("weather_code") or []
    temps = h.get("temperature_2m") or []
    pops = h.get("precipitation_probability") or []

    now = datetime.now(tz=_HELSINKI)
    for i, time_str in enumerate(times):
        try:
            dt = datetime.fromisoformat(time_str).replace(tzinfo=_HELSINKI)
        except ValueError:
            continue
        # Skip hours that have already passed (keep the current hour onward).
        if dt < now.replace(minute=0, second=0, microsecond=0):
            continue
        hourly.append(
            {
                "time": time_str,
                "hour": dt.hour,
                "code": codes[i] if i < len(codes) else None,
                "text": code_text(codes[i]) if i < len(codes) else "—",
                "icon": code_icon(codes[i]) if i < len(codes) else "cloud",
                "temperature": temps[i] if i < len(temps) else None,
                "precip_prob": pops[i] if i < len(pops) else None,
            }
        )
        if len(hourly) >= _HOURLY_COUNT:
            break

    return {"location": config.WEATHER_NAME, "current": current, "hourly": hourly}
