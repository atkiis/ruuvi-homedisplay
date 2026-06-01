"""
app.py – Main Flask application for ruuvi-homedisplay.

Run with:
    python app.py

Or with a production WSGI server:
    gunicorn -w 1 -b 0.0.0.0:5000 app:app
"""

import logging

from flask import Flask, jsonify, render_template, Response

import buses
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


# ---------------------------------------------------------------------------
# E-paper endpoints (Seeed reTerminal E1001 and similar e-ink panels)
# ---------------------------------------------------------------------------


@app.route("/epaper.png")
def epaper_png():
    return Response(epaper.render_png(), mimetype="image/png")


@app.route("/epaper.bmp")
def epaper_bmp():
    return Response(epaper.render_bmp(mono=not config.EPAPER_COLOR), mimetype="image/bmp")


@app.route("/epaper")
def epaper_preview():
    """Simple HTML wrapper to preview the landscape e-paper image in a desktop browser."""
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>E-paper preview</title>"
        f"<meta http-equiv='refresh' content='{config.REFRESH_INTERVAL}'>"
        "<style>body{background:#333;margin:0;display:flex;justify-content:center;"
        "align-items:center;min-height:100vh}"
        "img{image-rendering:pixelated;border:1px solid #000;background:#fff}</style>"
        "</head><body>"
        f"<img src='/epaper.png?t={{}}' width='{config.EPAPER_WIDTH}' "
        f"height='{config.EPAPER_HEIGHT}'>"
        "<script>setInterval(()=>{const i=document.querySelector('img');"
        "i.src='/epaper.png?t='+Date.now();},"
        f"{config.REFRESH_INTERVAL * 1000});</script>"
        "</body></html>"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
