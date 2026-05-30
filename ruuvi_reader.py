"""
ruuvi_reader.py – Background Ruuvi tag reader.

Starts a daemon thread that continuously scans for Ruuvi BLE advertisements
and stores the latest measurement for each configured tag.  Falls back to
simulated demo data when DEMO_MODE is enabled in config.py or when the
ruuvitag-sensor library is not available / no BLE hardware is present.
"""

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


# ---------------------------------------------------------------------------
# BLE reader (real hardware)
# ---------------------------------------------------------------------------

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

    def _handle(found: tuple) -> None:
        mac, data = found
        now = datetime.now(timezone.utc).isoformat()
        with _lock:
            _latest[mac.upper()] = {
                "temperature": data.get("temperature"),
                "humidity": data.get("humidity"),
                "pressure": data.get("pressure"),
                "battery": data.get("battery"),
                "rssi": data.get("rssi"),
                "updated_at": now,
            }
        logger.debug("Updated %s: %.1f°C", mac, data.get("temperature", 0))

    while True:
        try:
            RuuviTagSensor.find_ruuvitags_and_handle_data(_handle)
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
    with _lock:
        for key, tag_cfg in config.RUUVI_TAGS.items():
            mac = tag_cfg["mac"].upper()
            base = _DEMO_BASES.get(mac, {"temperature": 20.0, "humidity": 50.0, "pressure": 1013.0})
            # Slow sinusoidal drift so the values look realistic over time.
            temp = base["temperature"] + 2.0 * math.sin(t / 120.0 + hash(mac) % 10)
            hum = max(0.0, min(100.0, base["humidity"] + 3.0 * math.sin(t / 180.0)))
            _latest[mac] = {
                "temperature": round(temp + random.gauss(0, 0.3), 2),
                "humidity": round(hum + random.gauss(0, 0.5), 1),
                "pressure": round(base["pressure"] + random.gauss(0, 0.2), 2),
                "battery": round(2.85 + random.gauss(0, 0.02), 2),
                "rssi": random.randint(-75, -55),
                "updated_at": now,
            }
