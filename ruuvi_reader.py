"""
ruuvi_reader.py – Background Ruuvi tag reader.

Starts a daemon thread that continuously scans for Ruuvi BLE advertisements
and stores the latest measurement for each configured tag.  Falls back to
simulated demo data when DEMO_MODE is enabled in config.py or when the
ruuvitag-sensor library is not available / no BLE hardware is present.
"""

import asyncio
import logging
import math
import random
import threading
import time
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)

# Shared storage: { mac_address: {temperature, humidity, pressure, battery, rssi, updated_at} }
_latest: dict[str, dict] = {}
_lock = threading.Lock()

# Per-MAC löyly (steam-throw) tracking – updated inside _lock by _check_loyly().
_loyly_state: dict[str, dict] = {}
# structure per MAC: count, session_start, prev_rh, cooldown, session_active

# Demo-mode löyly simulation state (not used in real BLE mode).
_DEMO_LOYLY_INTERVAL = 150   # seconds between simulated löyly events
_demo_loyly_boost: dict[str, float] = {}   # MAC → current extra RH
_demo_loyly_next: dict[str, float] = {}    # MAC → monotonic time of next event


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start() -> None:
    """Kick off the background reader thread (call once at app start-up)."""
    if config.DEMO_MODE:
        logger.info("Ruuvi reader: DEMO MODE enabled – using simulated data.")
        t = threading.Thread(target=_demo_loop, daemon=True, name="ruuvi-demo")
    else:
        logger.info("Ruuvi reader: starting BLE scan thread.")
        t = threading.Thread(target=_ble_loop, daemon=True, name="ruuvi-ble")
    t.start()


def get_latest_data() -> dict:
    """Return the latest measurements indexed by tag key (sauna / balcony / freezer)."""
    result = {}
    with _lock:
        for key, tag_cfg in config.RUUVI_TAGS.items():
            mac = tag_cfg["mac"].upper()
            raw = _latest.get(mac)
            if raw:
                result[key] = {
                    "name": tag_cfg["name"],
                    "icon": tag_cfg["icon"],
                    "temp_min": tag_cfg["temp_min"],
                    "temp_max": tag_cfg["temp_max"],
                    **raw,
                }
            else:
                result[key] = {
                    "name": tag_cfg["name"],
                    "icon": tag_cfg["icon"],
                    "temp_min": tag_cfg["temp_min"],
                    "temp_max": tag_cfg["temp_max"],
                    "temperature": None,
                    "humidity": None,
                    "pressure": None,
                    "battery": None,
                    "rssi": None,
                    "updated_at": None,
                }
    return result


def get_sauna_state() -> dict | None:
    """Return sauna tag data + löyly count if the sauna is hot, else None.

    Returns a dict with keys: name, temperature, humidity, loyly_count,
    session_start, updated_at.  Returns None when the sauna tag is not
    configured or the temperature is below SAUNA_TEMP_THRESHOLD.
    """
    sauna_key = getattr(config, "SAUNA_TAG_KEY", "sauna")
    threshold = getattr(config, "SAUNA_TEMP_THRESHOLD", 75)
    tag_cfg = config.RUUVI_TAGS.get(sauna_key)
    if not tag_cfg:
        return None
    mac = tag_cfg["mac"].upper()
    with _lock:
        raw = _latest.get(mac)
        if not raw:
            return None
        temp = raw.get("temperature")
        if temp is None or temp < threshold:
            return None
        state = _loyly_state.get(mac, {})
        return {
            "name": tag_cfg["name"],
            "temperature": temp,
            "humidity": raw.get("humidity"),
            "loyly_count": state.get("count", 0),
            "session_start": state.get("session_start"),
            "updated_at": raw.get("updated_at"),
        }


# ---------------------------------------------------------------------------
# BLE reader (real hardware)
# ---------------------------------------------------------------------------


def _check_loyly(mac: str, temp: float | None, humidity: float | None) -> None:
    """Detect a löyly event from an RH spike.  Must be called while _lock is held."""
    threshold = getattr(config, "SAUNA_TEMP_THRESHOLD", 75)
    spike = getattr(config, "LOYLY_RH_SPIKE", 5.0)
    state = _loyly_state.setdefault(mac, {
        "count": 0,
        "session_start": None,
        "prev_rh": None,
        "cooldown": False,
        "session_active": False,
    })
    if temp is None or humidity is None:
        return
    sauna_active = temp >= threshold
    if sauna_active and not state["session_active"]:
        # Temperature just crossed the threshold – start a new session.
        state["count"] = 0
        state["session_start"] = datetime.now(timezone.utc).isoformat()
        state["prev_rh"] = humidity
        state["cooldown"] = False
        state["session_active"] = True
        return
    if not sauna_active:
        state["session_active"] = False
        state["prev_rh"] = humidity
        return
    # Session is active – detect RH spike as one löyly.
    prev_rh = state["prev_rh"]
    if prev_rh is not None:
        rh_delta = humidity - prev_rh
        if rh_delta >= spike and not state["cooldown"]:
            state["count"] += 1
            state["cooldown"] = True
        elif rh_delta <= -1.0:
            # RH is falling again – ready to detect the next throw.
            state["cooldown"] = False
    state["prev_rh"] = humidity


def _ble_loop() -> None:
    """Continuously read from real Ruuvi tags via BLE."""
    try:
        from ruuvitag_sensor.ruuvi import RuuviTagSensor  # type: ignore
    except ImportError:
        logger.error(
            "ruuvitag-sensor is not installed or BLE is unavailable. "
            "Set DEMO_MODE = True in config.py to use simulated data."
        )
        return

    macs = [cfg["mac"].upper() for cfg in config.RUUVI_TAGS.values()]
    logger.info("Scanning for Ruuvi tags: %s", macs)

    def _store(mac: str, data: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        temp = data.get("temperature")
        hum = data.get("humidity")
        with _lock:
            _latest[mac.upper()] = {
                "temperature": temp,
                "humidity": hum,
                "pressure": data.get("pressure"),
                "battery": data.get("battery"),
                "rssi": data.get("rssi"),
                "updated_at": now,
            }
            _check_loyly(mac.upper(), temp, hum)
        logger.debug("Updated %s: %.1f°C", mac, data.get("temperature", 0))

    async def _scan() -> None:
        # get_data_async() is an async generator yielding (mac, data) tuples for
        # every advertisement. It works with the default bleak backend, unlike
        # the synchronous get_data() which needs a sync (BlueZ) adapter and
        # raises "sync BLE adapter required" when bleak is installed.
        async for mac, data in RuuviTagSensor.get_data_async(macs):
            _store(mac, data)

    while True:
        try:
            asyncio.run(_scan())
        except Exception as exc:  # noqa: BLE001
            logger.warning("BLE scan error: %s – retrying in 10 s.", exc)
            time.sleep(10)


# ---------------------------------------------------------------------------
# Demo / simulation loop
# ---------------------------------------------------------------------------

# Base values for each tag (slightly randomised each cycle to look "live").
_DEMO_BASES = {
    "AA:BB:CC:DD:EE:01": {"temperature": 80.0, "humidity": 15.0, "pressure": 1013.0},
    "AA:BB:CC:DD:EE:02": {"temperature": 18.0, "humidity": 65.0, "pressure": 1015.0},
    "AA:BB:CC:DD:EE:03": {"temperature": -18.0, "humidity": 85.0, "pressure": 1013.0},
}


def _demo_loop() -> None:
    """Update _latest with slowly drifting simulated values every 30 seconds."""
    # Initialise immediately so the first page load has data.
    _demo_tick()
    while True:
        time.sleep(30)
        _demo_tick()


def _demo_tick() -> None:
    now = datetime.now(timezone.utc).isoformat()
    t = time.monotonic()
    sauna_key = getattr(config, "SAUNA_TAG_KEY", "sauna")
    sauna_threshold = getattr(config, "SAUNA_TEMP_THRESHOLD", 75)
    with _lock:
        for key, tag_cfg in config.RUUVI_TAGS.items():
            mac = tag_cfg["mac"].upper()
            base = _DEMO_BASES.get(mac, {"temperature": 20.0, "humidity": 50.0, "pressure": 1013.0})
            # Slow sinusoidal drift so the values look realistic over time.
            temp = base["temperature"] + 2.0 * math.sin(t / 120.0 + hash(mac) % 10)
            hum = max(0.0, min(100.0, base["humidity"] + 3.0 * math.sin(t / 180.0)))

            # Simulate periodic löyly (steam throws) for the sauna tag.
            if key == sauna_key and temp >= sauna_threshold:
                boost = _demo_loyly_boost.get(mac, 0.0) * 0.55  # decay ~45% per 30 s tick
                if t >= _demo_loyly_next.get(mac, 0.0):
                    boost += 18.0  # sudden RH spike mimicking water on stones
                    _demo_loyly_next[mac] = (
                        t + _DEMO_LOYLY_INTERVAL + random.uniform(-20, 20)
                    )
                _demo_loyly_boost[mac] = max(0.0, boost)
                hum = min(100.0, hum + boost)

            final_temp = round(temp + random.gauss(0, 0.3), 2)
            final_hum = round(hum + random.gauss(0, 0.5), 1)
            _latest[mac] = {
                "temperature": final_temp,
                "humidity": final_hum,
                "pressure": round(base["pressure"] + random.gauss(0, 0.2), 2),
                "battery": round(2.85 + random.gauss(0, 0.02), 2),
                "rssi": random.randint(-75, -55),
                "updated_at": now,
            }
            _check_loyly(mac, final_temp, final_hum)
