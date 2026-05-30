"""
app.py – Main Flask application for ruuvi-homedisplay.

Run with:
    python app.py

Or with a production WSGI server:
    gunicorn -w 1 -b 0.0.0.0:5000 app:app
"""

import logging

from flask import Flask, jsonify, render_template

import buses
import config
import electricity
import ruuvi_reader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)

app = Flask(__name__)

# Start the background Ruuvi BLE / demo thread.
ruuvi_reader.start()


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
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
