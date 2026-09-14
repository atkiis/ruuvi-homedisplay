# ruuvi-homedisplay

A full-screen home-display dashboard that shows:

- **Ruuvi tag sensor data** (temperature, humidity, pressure, battery) – sauna, balcony, and freezer
- **Finnish electricity spot prices** (hourly chart + current price, from [api.spot-hinta.fi](https://api.spot-hinta.fi))
- **Bus stop schedules** for two nearby stops (via the [Digitransit routing API](https://digitransit.fi/en/developers/); preconfigured for Tampere / Nysse via the Waltti feed)

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
| `DIGITRANSIT_API_URL` | Digitransit v2 endpoint (Waltti/Tampere by default; also HSL / Finland) |
| `DIGITRANSIT_API_KEY` | **Required** API key (free, see [Digitransit portal](https://portal-api.digitransit.fi/)) |
| `REFRESH_INTERVAL` | Page polling interval in seconds (default 60) |
| `DEMO_MODE` | `True` → use simulated data (no Bluetooth required) |

### Finding your Ruuvi tag MAC addresses

Run the Ruuvi scanner once:
```bash
sudo python -c "from ruuvitag_sensor.ruuvi import RuuviTagSensor; print(RuuviTagSensor.find_ruuvitags())"
```

> **Digitransit now requires a (free) API key.** Register at the
> [Digitransit portal](https://portal-api.digitransit.fi/), then set
> `DIGITRANSIT_API_KEY` in `config.py`. Requests without a key return HTTP 401.

### Finding your bus stop IDs

- **Tampere (Nysse)**: this app is preconfigured for the Waltti feed
  (`routing/v2/waltti/gtfs/v1`). Find stop IDs on
  [reittiopas.tampere.fi](https://reittiopas.tampere.fi) or with the
  [GraphiQL explorer](https://digitransit.fi/en/developers/apis/1-routing-api/1-graphiql/).
  Waltti/Tampere stop IDs use the `tampere:` prefix:
  ```graphql
  { stops(name: "Keskustori") { gtfsId name code } }
  ```
- **Other regions**: change `DIGITRANSIT_API_URL` to the matching v2 endpoint —
  HSL `routing/v2/hsl/gtfs/v1` or Finland-wide `routing/v2/finland/gtfs/v1`.

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

## Running on a Seeed reTerminal E1001 e-paper display

The [reTerminal E1001](https://wiki.seeedstudio.com/) is a 7.5" **800×480
monochrome e-paper** device built on an ESP32-S3. It is **not** a computer with a
browser, so it can't load the HTML dashboard directly. Instead the board fetches
a pre-rendered image from this server and draws it on the panel.

The server exposes three extra endpoints (no extra services to run):

| Endpoint | Use |
|---|---|
| `/epaper.png` | 800×480 image for ESPHome's `online_image` (recommended) |
| `/epaper-e1002.png` | 800×480 colour image for the E1002 layout |
| `/epaper.bmp` | 1-bit BMP for bare Arduino sketches |
| `/epaper-e1002.bmp` | BMP for the E1002 layout |
| `/epaper` | HTML preview to tweak the layout in a desktop browser |
| `/epaper-e1002` | HTML preview for the E1002 layout |

The image is rendered for a small, slow, 1-bit screen: big high-contrast text,
no emoji, a hand-drawn 24-hour electricity chart, and only the soonest bus
departures. Adjust the panel in `config.py`:

```python
EPAPER_WIDTH  = 800     # panel width
EPAPER_HEIGHT = 480     # panel height
EPAPER_ROTATE = 0       # 90/180/270 if mounted sideways
EPAPER_COLOR  = False   # True only for the 6-colour reTerminal E1002
```

Preview the default layout locally before flashing anything:

```bash
python app.py
# → open http://localhost:5001/epaper
```

### Flashing the device (ESPHome)

A starter config lives in [esphome/ruuvi-homedisplay-e1001.yaml](esphome/ruuvi-homedisplay-e1001.yaml).
It refreshes the monochrome E1001 layout on the existing `/epaper.png` route.

For the colour panel, use [esphome/ruuvi-homedisplay-e1002.yaml](esphome/ruuvi-homedisplay-e1002.yaml).
It wakes once an hour, downloads `/epaper-e1002.png`, and refreshes the colour
layout. Edit the `substitutions:` block with your WiFi and the server URL, add
Seeed's official board/display definition where indicated, then:

```bash
esphome run esphome/ruuvi-homedisplay-e1001.yaml
esphome run esphome/ruuvi-homedisplay-e1002.yaml
```

> Because e-paper refreshes are slow and wear the panel, keep the update
> interval generous (5–15 min). The sensor, electricity, and bus data all change
> slowly enough that this is plenty.

### Updating the E1002 calendar

Open `http://localhost:5001/epaper-e1002` in a browser on the trusted home
network and use the calendar upload control to select an `.ics` file. The
server parses the file, keeps only the event fields needed by the display, and
stores the last valid normalized calendar in `instance/calendar.json`.

The calendar service uses `Europe/Helsinki` by default and supports timed
events, all-day events, categories, and bounded recurring events. A malformed
or oversized upload is rejected without replacing the current calendar. The
management endpoints are:

| Endpoint | Use |
|---|---|
| `POST /api/calendar/upload` | Upload an `.ics` file in the `calendar` multipart field with `category` set to `red`, `green`, `blue`, or `yellow` |
| `GET /api/calendar` | Return upload status, event count, and update time |
| `DELETE /api/calendar` | Remove the uploaded calendar and return to configured/demo events |

The E1002 continues to download the unchanged `/epaper-e1002.png` endpoint.
The server invalidates its image cache after a successful upload; the physical
panel updates on its next ESPHome wake cycle, which is hourly in the example
configuration.

Image polarity is configured independently in `config.py`: `EPAPER_INVERT`
controls E1001 and `EPAPER_E1002_INVERT` controls E1002. The defaults keep
E1001 inverted and E1002 in its normal color presentation.

The selected upload category is applied to every imported event in that file,
so one calendar feed can be assigned to Priority, Personal, Work, or Health
without editing the source calendar. Embedded ICS categories are used only
when importing through the backend directly without an upload override.

Additional E1002 categories can be added from the calendar preview. Each new
category is assigned one of the panel's existing red, green, blue, or yellow
pigments and is stored in `instance/calendar_categories.json`. This keeps the
color e-paper output predictable while allowing named categories such as
Family, Travel, or School. These settings apply only to E1002 calendar events.

The E1002 calendar layout adapts to the agenda: five or more events today use
the full display in a denser layout, while an empty today promotes tomorrow's
events into a larger single-day layout. Normal agendas show both days. Event
start and end times are rendered as separate high-contrast values so the end
time remains easy to read on the e-paper panel.

Events remain visible for one hour after their scheduled end, then are omitted
from both the browser preview and the next E1002 image render. Adjust
`CALENDAR_EVENT_RETENTION_HOURS` in `config.py` to change this grace period.

The upload endpoint is intended for a trusted home network and has no built-in
authentication. Do not expose it directly to the public internet.

## Architecture

```
app.py                Flask application & routing
config.py             All user-facing settings
ruuvi_reader.py       Background BLE scan thread (or demo mode)
electricity.py         Fetches/caches spot-hinta.fi prices
buses.py               Fetches/caches Digitransit departures
weather.py             Fetches/caches Open-Meteo forecast
calendar_backend.py    Parses and persists uploaded ICS calendars
calendar_view.py       Normalizes calendar events for HTML and e-paper
epaper.py              Renders the 800x480 e-paper dashboard image
templates/index.html   Dashboard HTML
static/css/style.css   Dark-theme CSS
static/js/dashboard.js  Auto-refresh & Chart.js rendering
esphome/               ESPHome configs for the reTerminal E1001 and E1002
```
