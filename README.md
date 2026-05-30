# ruuvi-homedisplay

A full-screen home-display dashboard that shows:

- **Ruuvi tag sensor data** (temperature, humidity, pressure, battery) – sauna, balcony, and freezer
- **Finnish electricity spot prices** (hourly chart + current price, from [api.spot-hinta.fi](https://api.spot-hinta.fi))
- **Bus stop schedules** for two nearby stops (via [Digitransit HSL API](https://digitransit.fi/en/developers/))

## Screenshot layout

```
┌──────────────────────────────────────────────────────────┐
│  14:35   30.5.2026                         [DEMO MODE]   │
├────────────────────────┬─────────────────────────────────┤
│  Sensors               │  Electricity Price (Finland)    │
│  🔥 Sauna  🌤 Balcony  │  Now: 8.45 c/kWh               │
│  84.3 °C   18.1 °C     │  [24-hour bar chart]            │
│  ❄️ Freezer            │                                 │
│  -18.2 °C              │                                 │
├────────────────────────┴─────────────────────────────────┤
│  Bus Schedules                                           │
│  Bus Stop 1         │  Bus Stop 2                        │
│  15  City   14:40 5 │  23  Center  14:38  3              │
└──────────────────────────────────────────────────────────┘
```

## Requirements

- Python 3.11+
- Raspberry Pi (or any Linux host with Bluetooth LE) for real Ruuvi readings
- Network access to reach the Digitransit and spot-hinta.fi APIs

## Quick start

```bash
# 1. Clone and enter the directory
git clone https://github.com/atkiis/ruuvi-homedisplay.git
cd ruuvi-homedisplay

# 2. Create a virtual environment and install dependencies
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Configure your tags, bus stops, etc.
nano config.py   # see comments inside the file

# 4. Run the app
python app.py
# → Open http://localhost:5000 in a browser
```

## Configuration

All settings live in **`config.py`**:

| Setting | Description |
|---|---|
| `RUUVI_TAGS` | Tag names, MAC addresses, and expected temperature ranges |
| `BUS_STOPS` | Digitransit stop IDs and display labels |
| `DIGITRANSIT_API_URL` | Router URL (HSL / Waltti / Finland) |
| `DIGITRANSIT_API_KEY` | Optional API key (see [Digitransit portal](https://portal-api.digitransit.fi/)) |
| `REFRESH_INTERVAL` | Page polling interval in seconds (default 60) |
| `DEMO_MODE` | `True` → use simulated data (no Bluetooth required) |

### Finding your Ruuvi tag MAC addresses

Run the Ruuvi scanner once:
```bash
sudo python -c "from ruuvitag_sensor.ruuvi import RuuviTagSensor; print(RuuviTagSensor.find_ruuvitags())"
```

### Finding your bus stop IDs

- **HSL (Helsinki region)**: search on [reittiopas.hsl.fi](https://reittiopas.hsl.fi) or use the Digitransit GraphQL API:
  ```graphql
  { stops(name: "Rautatientori") { gtfsId name code } }
  ```
- **Other cities**: change `DIGITRANSIT_API_URL` to the correct router (see comments in `config.py`).

## Running on a display at boot (systemd)

```ini
# /etc/systemd/system/homedisplay.service
[Unit]
Description=ruuvi-homedisplay
After=network.target bluetooth.target

[Service]
WorkingDirectory=/home/pi/ruuvi-homedisplay
ExecStart=/home/pi/ruuvi-homedisplay/.venv/bin/python app.py
Restart=always
User=pi

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now homedisplay
```

Then open a Chromium kiosk window:
```bash
chromium-browser --kiosk http://localhost:5000
```

## Architecture

```
app.py                Flask application & routing
config.py             All user-facing settings
ruuvi_reader.py       Background BLE scan thread (or demo mode)
electricity.py        Fetches/caches spot-hinta.fi prices
buses.py              Fetches/caches Digitransit departures
templates/index.html  Dashboard HTML
static/css/style.css  Dark-theme CSS
static/js/dashboard.js  Auto-refresh & Chart.js rendering
```
