"""
electricity.py – Fetch Finnish electricity spot prices from api.spot-hinta.fi.

The free API returns today's 24 hourly prices (Finnish time, VAT included).
We cache the result for 15 minutes to avoid hammering the upstream service.
"""

import logging
import threading
import time
from datetime import datetime, timezone
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


def start() -> None:
    """Start a daemon thread that keeps the price cache warm in the background."""
    def _loop() -> None:
        while True:
            _refresh()
            time.sleep(_REFRESH_INTERVAL)

    threading.Thread(target=_loop, daemon=True, name="electricity-refresh").start()


def get_prices() -> dict:
    """
    Return a dict with:
        hours   – list of 24 dicts {hour, price_no_tax, price_with_tax, is_current}
        current – the entry for the current hour (or None)
        unit    – "c/kWh"

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
        return {"hours": [], "current": None, "unit": "c/kWh",
                "error": "Electricity data unavailable"}
    return _patch_current(cache)


def _refresh() -> None:
    """Fetch fresh prices and atomically replace the cache. Network runs outside
    the data lock; ``_refresh_lock`` serialises concurrent refreshes so a burst
    of cold requests triggers only one upstream call."""
    global _cache, _cache_ts
    with _refresh_lock:
        # Another thread may have refreshed while we waited for the lock.
        with _lock:
            if _cache and (time.monotonic() - _cache_ts) < _CACHE_TTL:
                return
        try:
            resp = requests.get(config.ELECTRICITY_API_URL, timeout=10)
            resp.raise_for_status()
            parsed = _parse(resp.json())
        except requests.RequestException as exc:
            logger.warning("Failed to fetch electricity prices: %s", exc)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unexpected error fetching electricity prices: %s", exc)
            return
        with _lock:
            _cache = parsed
            _cache_ts = time.monotonic()
        logger.info("Electricity prices refreshed (%d entries).", len(parsed.get("hours", [])))


def _parse(raw: list) -> dict:
    # The API now reports prices at 15-minute resolution (96 entries/day) in
    # €/kWh. The dashboard is built around 24 hourly bars, so average the
    # quarter-hour values into hourly buckets and convert €/kWh → c/kWh (so the
    # colour thresholds of 5 / 12 / 20 / 30 c/kWh stay meaningful).
    buckets: dict[int, dict] = {}
    for entry in raw:
        dt_str = entry.get("DateTime", "")
        try:
            dt = datetime.fromisoformat(dt_str)
        except ValueError:
            continue
        b = buckets.setdefault(
            dt.hour,
            {"datetime": dt_str, "no_tax": [], "with_tax": []},
        )
        b["no_tax"].append(entry.get("PriceNoTax", 0.0) * 100)
        b["with_tax"].append(entry.get("PriceWithTax", 0.0) * 100)

    hours = []
    for hour, b in buckets.items():
        n = len(b["with_tax"]) or 1
        hours.append(
            {
                "hour": hour,
                "datetime": b["datetime"],
                "price_no_tax": round(sum(b["no_tax"]) / n, 2),
                "price_with_tax": round(sum(b["with_tax"]) / n, 2),
                "is_current": False,
            }
        )
    # Sort by hour ascending.
    hours.sort(key=lambda h: h["hour"])
    return {"hours": hours, "current": None, "unit": "c/kWh"}


def _patch_current(data: dict) -> dict:
    """Mark the entry matching the current Finnish local hour."""
    import copy

    result = copy.deepcopy(data)
    # Use Finnish timezone so the current-hour highlight is always correct regardless
    # of which timezone the server is configured to use.
    current_hour = datetime.now(tz=_HELSINKI).hour
    current = None
    for h in result["hours"]:
        h["is_current"] = h["hour"] == current_hour
        if h["is_current"]:
            current = h
    result["current"] = current
    return result
