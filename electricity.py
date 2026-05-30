"""
electricity.py – Fetch Finnish electricity spot prices from api.spot-hinta.fi.

The free API returns today's 24 hourly prices (Finnish time, VAT included).
We cache the result for 15 minutes to avoid hammering the upstream service.
"""

import logging
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

import config

logger = logging.getLogger(__name__)

_HELSINKI = ZoneInfo("Europe/Helsinki")

_cache: dict = {}
_cache_ts: float = 0.0
_CACHE_TTL = 900  # 15 minutes


def get_prices() -> dict:
    """
    Return a dict with:
        hours   – list of 24 dicts {hour, price_no_tax, price_with_tax, is_current}
        current – the entry for the current hour (or None)
        unit    – "c/kWh"
    """
    global _cache, _cache_ts

    now = time.monotonic()
    if _cache and (now - _cache_ts) < _CACHE_TTL:
        return _patch_current(_cache)

    try:
        resp = requests.get(config.ELECTRICITY_API_URL, timeout=10)
        resp.raise_for_status()
        raw = resp.json()
        _cache = _parse(raw)
        _cache_ts = now
        logger.info("Electricity prices refreshed (%d entries).", len(_cache.get("hours", [])))
    except requests.RequestException as exc:
        logger.warning("Failed to fetch electricity prices: %s", exc)
        if not _cache:
            return {"hours": [], "current": None, "unit": "c/kWh", "error": "Electricity data unavailable"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Unexpected error fetching electricity prices: %s", exc)
        if not _cache:
            return {"hours": [], "current": None, "unit": "c/kWh", "error": "Electricity data unavailable"}

    return _patch_current(_cache)


def _parse(raw: list) -> dict:
    hours = []
    for entry in raw:
        dt_str = entry.get("DateTime", "")
        try:
            dt = datetime.fromisoformat(dt_str)
        except ValueError:
            continue
        hours.append(
            {
                "hour": dt.hour,
                "datetime": dt_str,
                "price_no_tax": round(entry.get("PriceNoTax", 0.0), 3),
                "price_with_tax": round(entry.get("PriceWithTax", 0.0), 3),
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
