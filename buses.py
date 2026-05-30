"""
buses.py – Fetch next departures from the Digitransit / HSL GraphQL API.

The Digitransit routing API is documented at:
    https://digitransit.fi/en/developers/apis/1-routing-api/

Results are cached for 60 seconds so rapid page refreshes don't hammer the API.
"""

import logging
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

import config

logger = logging.getLogger(__name__)

_HELSINKI = ZoneInfo("Europe/Helsinki")

# Simple in-memory cache: { stop_id: (timestamp, data) }
_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 60  # seconds

_STOP_QUERY = """
query StopDepartures($stopId: String!, $count: Int!) {
  stop(id: $stopId) {
    name
    code
    stoptimesWithoutPatterns(numberOfDepartures: $count, omitCanceled: false) {
      scheduledDeparture
      realtimeDeparture
      departureDelay
      realtime
      realtimeState
      serviceDay
      trip {
        route {
          shortName
          longName
          mode
        }
      }
      headsign
      pickupType
    }
  }
}
"""


def get_schedules() -> list[dict]:
    """Return a list of stop dicts, each containing next departures."""
    results = []
    for stop_cfg in config.BUS_STOPS:
        results.append(_get_stop(stop_cfg))
    return results


def _get_stop(stop_cfg: dict) -> dict:
    stop_id = stop_cfg["id"]
    now = time.monotonic()

    cached_ts, cached_data = _cache.get(stop_id, (0.0, None))
    if cached_data and (now - cached_ts) < _CACHE_TTL:
        return cached_data

    data = _fetch_stop(stop_id, stop_cfg["name"])
    _cache[stop_id] = (now, data)
    return data


def _fetch_stop(stop_id: str, label: str) -> dict:
    headers = {"Content-Type": "application/json"}
    if config.DIGITRANSIT_API_KEY:
        headers["digitransit-subscription-key"] = config.DIGITRANSIT_API_KEY

    payload = {
        "query": _STOP_QUERY,
        "variables": {"stopId": stop_id, "count": config.BUS_DEPARTURES_COUNT},
    }

    try:
        resp = requests.post(config.DIGITRANSIT_API_URL, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        body = resp.json()
    except requests.RequestException as exc:
        logger.warning("Failed to fetch stop %s: %s", stop_id, exc)
        return {"id": stop_id, "name": label, "departures": [], "error": "Transit data unavailable"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Unexpected error fetching stop %s: %s", stop_id, exc)
        return {"id": stop_id, "name": label, "departures": [], "error": "Transit data unavailable"}

    stop_data = (body.get("data") or {}).get("stop")
    if not stop_data:
        return {"id": stop_id, "name": label, "departures": [], "error": "Stop not found"}

    departures = []
    for st in stop_data.get("stoptimesWithoutPatterns") or []:
        service_day = st.get("serviceDay", 0)
        scheduled = st.get("scheduledDeparture", 0)
        realtime_dep = st.get("realtimeDeparture", scheduled)
        is_realtime = st.get("realtime", False)

        # Epoch seconds → human-readable time in Finnish timezone.
        epoch = service_day + realtime_dep
        dt = datetime.fromtimestamp(epoch, tz=_HELSINKI)
        # Minutes until departure (may be negative for already-departed vehicles).
        minutes_until = int((epoch - time.time()) / 60)

        route = (st.get("trip") or {}).get("route") or {}
        departures.append(
            {
                "route": route.get("shortName", "?"),
                "destination": st.get("headsign", ""),
                "time": dt.strftime("%H:%M"),
                "minutes_until": minutes_until,
                "is_realtime": is_realtime,
                "mode": route.get("mode", "BUS"),
            }
        )

    return {
        "id": stop_id,
        "name": stop_data.get("name", label),
        "code": stop_data.get("code", ""),
        "label": label,
        "departures": departures,
    }
