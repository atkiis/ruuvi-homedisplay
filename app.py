"""
app.py – Main Flask application for ruuvi-homedisplay.

Run with:
    python app.py

Or with a production WSGI server:
    gunicorn -w 1 -b 0.0.0.0:5000 app:app
"""

import logging
import math
import threading
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, Response, request

import buses
import calendar_backend
import calendar_view
import config
import electricity
import epaper
import ruuvi_reader
import weather

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)

app = Flask(__name__)

_device_state_lock = threading.Lock()
_device_state = {
    "battery_level": None,
    "battery_voltage": None,
    "updated_at": None,
    "source_ip": None,
}

# Start the background data threads. Each keeps its in-memory cache warm so
# request handlers (and the e-paper renderer) always serve data instantly
# without blocking on upstream APIs.
ruuvi_reader.start()
electricity.start()
weather.start()
buses.start()


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    return render_template(
        "index.html",
        tags=config.RUUVI_TAGS,
        bus_stops=config.BUS_STOPS,
        refresh_interval=config.REFRESH_INTERVAL,
        ruuvi_stale_minutes=config.RUUVI_STALE_MINUTES,
        demo_mode=config.DEMO_MODE,
    )


# ---------------------------------------------------------------------------
# JSON API endpoints (called by dashboard.js)
# ---------------------------------------------------------------------------


@app.route("/api/ruuvi")
def api_ruuvi():
    return jsonify(ruuvi_reader.get_latest_data())


@app.route("/api/electricity")
def api_electricity():
    return jsonify(electricity.get_prices())


@app.route("/api/buses")
def api_buses():
    return jsonify(buses.get_schedules())


@app.route("/api/device")
def api_device():
    with _device_state_lock:
        return jsonify(dict(_device_state))


@app.route("/api/device/battery", methods=["GET", "POST"])
def api_device_battery():
    payload = request.get_json(silent=True) or {}
    raw_level = request.args.get("level", payload.get("level"))
    raw_voltage = request.args.get("voltage", payload.get("voltage"))

    level = None
    voltage = None
    try:
        if raw_level not in (None, ""):
            v = float(raw_level)
            level = None if math.isnan(v) or math.isinf(v) else v
    except (TypeError, ValueError):
        level = None
    try:
        if raw_voltage not in (None, ""):
            v = float(raw_voltage)
            voltage = None if math.isnan(v) or math.isinf(v) else v
    except (TypeError, ValueError):
        voltage = None

    if level is not None:
        level = max(0.0, min(100.0, level))

    with _device_state_lock:
        if level is not None:
            _device_state["battery_level"] = round(level, 1)
        if voltage is not None:
            _device_state["battery_voltage"] = round(voltage, 3)
        _device_state["updated_at"] = datetime.now(timezone.utc).isoformat()
        _device_state["source_ip"] = request.remote_addr
        return jsonify(dict(_device_state))


# ---------------------------------------------------------------------------
# E-paper endpoints (Seeed reTerminal E1001 and similar e-ink panels)
# ---------------------------------------------------------------------------


def _epaper_png_response(layout: str) -> Response:
    etag = epaper.get_render_etag(layout)
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304)
    data = epaper.render_png(layout)
    resp = Response(data, mimetype="image/png")
    resp.set_etag(etag)
    # Prevent stale intermediary caches from serving an older dashboard image.
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


def _epaper_preview(layout: str, title: str, png_path: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title>"
        f"<meta http-equiv='refresh' content='{config.REFRESH_INTERVAL}'>"
        "<style>body{background:#333;margin:0;display:flex;justify-content:center;"
        "align-items:center;min-height:100vh}"
        "img{image-rendering:pixelated;border:1px solid #000;background:#fff}</style>"
        "</head><body>"
        f"<img src='{png_path}?t=0' width='{config.EPAPER_WIDTH}' "
        f"height='{config.EPAPER_HEIGHT}'>"
        "<script>setInterval(()=>{const i=document.querySelector('img');"
        f"i.src='{png_path}?t='+Date.now();}},"
        f"{config.REFRESH_INTERVAL * 1000});</script>"
        "</body></html>"
    )


@app.route("/epaper.png")
def epaper_png():
    return _epaper_png_response("e1001")


@app.route("/epaper-e1002.png")
def epaper_e1002_png():
    return _epaper_png_response("e1002")


@app.route("/epaper/etag")
def epaper_etag():
    """Lightweight endpoint returning the current image ETag as plain text.

    The e-paper device polls this cheaply to decide whether a full image
    download (and panel refresh) is actually needed.
    """
    return Response(epaper.get_render_etag("e1001"), mimetype="text/plain")


@app.route("/epaper-e1002/etag")
def epaper_e1002_etag():
    return Response(epaper.get_render_etag("e1002"), mimetype="text/plain")


@app.route("/epaper.bmp")
def epaper_bmp():
    return Response(epaper.render_bmp(mono=not config.EPAPER_COLOR, layout="e1001"), mimetype="image/bmp")


@app.route("/epaper-e1002.bmp")
def epaper_e1002_bmp():
    return Response(epaper.render_bmp(mono=False, layout="e1002"), mimetype="image/bmp")


@app.route("/epaper")
def epaper_preview():
    """Simple HTML wrapper to preview the landscape e-paper image in a desktop browser."""
    return _epaper_preview("e1001", "E-paper preview", "/epaper.png")


# ---------------------------------------------------------------------------
# E1002 colour calendar view (7.3" 7-colour panel, 800x480)
# ---------------------------------------------------------------------------


@app.route("/epaper-e1002")
def epaper_e1002_calendar():
    """Self-contained 800x480 HTML/CSS calendar view for the 7.3" colour panel.

    The panel itself consumes the identical layout as a PNG from
    /epaper-e1002.png; this route is the browser preview of it.
    """
    return render_template(
        "epaper_e1002_calendar.html",
        summary=calendar_view.summary(),
        sections=calendar_view.sections(),
        updated=datetime.now().strftime("%H:%M"),
        calendar_status=calendar_backend.status(),
    )


@app.route("/api/calendar", methods=["GET", "DELETE"])
def api_calendar():
    if request.method == "DELETE":
        calendar_backend.clear()
        epaper.invalidate_render_cache()
        return jsonify({"uploaded": False, "event_count": 0})
    return jsonify(calendar_backend.status())


@app.route("/api/calendar/upload", methods=["POST"])
def api_calendar_upload():
    upload = request.files.get("calendar")
    max_bytes = int(getattr(config, "CALENDAR_MAX_UPLOAD_BYTES", 1024 * 1024))
    if upload is None or not upload.filename:
        return jsonify({"error": "Choose an .ics calendar file."}), 400
    if not upload.filename.lower().endswith(".ics"):
        return jsonify({"error": "Upload an iCalendar file with an .ics extension."}), 400
    raw = upload.read(max_bytes + 1)
    if len(raw) > max_bytes:
        return jsonify({"error": "The calendar file is too large."}), 413
    try:
        events = calendar_backend.import_ics(raw)
    except calendar_backend.CalendarImportError as exc:
        return jsonify({"error": str(exc)}), 400
    epaper.invalidate_render_cache()
    return jsonify({"uploaded": True, "event_count": len(events)}), 201



# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
