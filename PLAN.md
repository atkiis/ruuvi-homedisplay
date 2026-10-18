# Plan: more sophisticated, nicer e-paper displays

Status as of 2026-10-18, branch `20260914-epaper`. Target hardware: reTerminal E1002 (7.3" Spectra 6, 800×480,
6 pigments: black, white, red, green, blue, yellow; ~15–30 s full refresh) and E1001 (7.5" mono, 800×480).

## Findings (repo evidence)

- `epaper.py` is a 1750-line single module. E1002 dashboard uses 10 ad-hoc RGB colours (`_C_SAA`, purple `_C_KAL`,
  amber, teal…) that **are not panel pigments**. The ESPHome `online_image` (RGB565) is quantised on-device, so
  purple/teal/amber turn into unpredictable dither patterns. The calendar view already does it more right
  (red/green/blue/yellow categories).
- Text is anti-aliased RGB; on a 6-colour panel the AA edge pixels become coloured speckle around glyphs.
- Layout constants are hard-coded pixels, duplicated between E1001 and E1002 (`_draw_*` vs `_e2_draw_*`).
- Refresh is hourly on E1002; the server already has a render cache + `/etag` endpoints, but the ESPHome config doesn't use them to skip refreshes.
- Tests only cover calendar. No renderer tests, no pytest in requirements, no golden images.
- Unreleased working-tree change: `CALENDAR_EVENT_RETENTION_HOURS` in `config.py` (needs commit with its feature).

## Progress (2026-10-18)

Decisions: E1002 is the focus (E1001 left as is); Finnish default, English via `EPAPER_LANGUAGE`.
Note: the production E1002 image (`/epaper-e1002.png`, layout `e1002`) is the **calendar + price summary**; the
older card dashboard (`e1002-dashboard`, `_render_e1002`) is unused and has overlapping text, so redesign work
should start from the calendar view or replace the dashboard.

- [x] Phase 0: pytest in `requirements-dev.txt`, renderer/palette/i18n tests, `tools/preview.py`.
- [x] Phase 1 (first cut): `epaper_palette.py` snap-to-6-colours, `EPAPER_E1002_SNAP`; calendar view redrawn
  pigment-native (blue page, white outlined cards, black footer) and HTML preview matched.
- [x] Finnish/English strings via `i18n.py` (calendar view + HTML preview).
- [x] Hour-aligned refresh: ESPHome E1002 wakes at ~HH:00:25 (`sleep_to_next_hour`, SNTP), server render cache keyed by hour.
- [x] Two-column E1002 view: calendar left; weather (12 h curve + rain) and 96-bar price chart with now-marker, cheapest/peak right; top bar date/temp/current price.
- [ ] HTML preview `/epaper-e1002` does not yet show the weather/price cards (image is the source of truth). Port or replace with an `<img>` preview.
- [ ] Skip identical redraws via ETag on device (needs state kept across deep sleep; low value at 1 refresh/hour).
- [ ] Still to verify on hardware: snapped colours and outlined cards on the real panel.
- [ ] Next: fix or retire `e1002-dashboard`, add weather + price chart to the production view, bundled fonts, package split.

## Principles

1. **Design for the panel, not the screen**: only the 6 pigments + white, flat fills, no gradients, no AA colour fringing.
2. Show the *preview exactly as the panel will look* (server-side quantised) so design iteration doesn't need flashing.
3. Few things, big, high hierarchy: one hero number per card; colour means something (red = alert/expensive, green = cheap/good, blue = cold/water, yellow = highlight).
4. Hourly cadence: design for "glanceable state now + next few hours", not live data.

## Phases

### Phase 0: Foundations (1–2 sessions)
- Add `requirements-dev.txt` (pytest); verify the existing 3 test files pass.
- Renderer smoke tests: each layout renders at 800×480 with mocked sources, and with every source raising/returning `{}`.
- Script `tools/preview.py` that renders all layouts to a scratchpad dir and builds a contact sheet, for before/after comparison.
- Commit pending `config.py` change together with the calendar retention work.

### Phase 1: Spectra 6 pipeline (biggest visual win)
- New `epaper_palette.py`: the six measured/nominal pigment RGB values (make them configurable; real panels are muted, e.g. red ≈ (178,19,24), yellow ≈ (239,222,68), green ≈ (18,95,32), blue ≈ (33,87,186)).
- Final-stage `quantize_spectra6(img)` using Pillow `quantize(palette=…, dither=NONE)` for the UI plus optional Floyd–Steinberg only for photographic/area-shaded regions (e.g. price heat bars, weather-icon tints). Serve it as a palette PNG (smaller download, deterministic, no on-device RGB565 rounding).
- Render text without colour fringing: draw at 1× with `ImageDraw.fontmode = "1"` for small coloured text, or supersample 2–3× and quantise with threshold for large text; pick per size by eyeballing.
- Replace the `_C_*` colours with named palette roles (`INK`, `ACCENT_RED`, `OK_GREEN`, `COLD_BLUE`, `HILITE_YELLOW`); keep E1001 path untouched (it already dithers/thresholds).
- Browser preview `/epaper-e1002` shows the quantised result, plus a toggle for "ideal RGB" vs "panel simulation".
- Update ESPHome E1002 config for palette PNG if the `online_image` type supports it; otherwise keep RGB565 but with exact-pigment colours (no rounding error).
- Acceptance: no off-palette pixels in output (test counts unique colours ≤ 6 after quantise); visual check of contact sheet.

### Phase 2: Layout system and visual redesign
- Split `epaper.py` into a package: `epaper/{core,fonts,palette,icons,e1001,e1002,calendar}.py`; keep `epaper.render*` public API and routes unchanged.
- Small layout helpers: grid/card (`Card(x,y,w,h)`, `section_title`, `kpi(value, unit, label)`), replacing magic pixel numbers.
- Bundle fonts in `static/fonts/` (e.g. Inter/Roboto Condensed + a numeric/tabular face, OFL) so output is identical on Mac and Pi; keep system lookup as fallback.
- Redesigned E1002 dashboard cards:
  - **Now** hero: big outdoor temp + weather icon, feels-like, wind, "next rain in Xh".
  - **Electricity**: 24 h bars coloured green/yellow/red by thresholds, now-marker, cheapest 3 h window highlighted, tomorrow's prices when published (~14:00).
  - **Weather**: 12 h temp line + precipitation bars, sunrise/sunset, UV/air-quality if available from Open-Meteo.
  - **Indoors/sauna/freezer**: tiles with min/max trend arrows and red alert if out of range (freezer > −15 °C, etc.).
  - **Buses**: next 2–3 departures per stop, minutes-to-leave large.
  - **Calendar strip**: next event(s) with category colour.
- Better icon set: consistent 2 px stroke hand-drawn icons at 3 sizes (weather ×8 conditions, bus, bolt, thermometer, droplet, snowflake, flame) in one `icons.py`.
- Adaptive layouts (already begun for calendar): alert mode (red banner when freezer warm, price spike, battery low), night mode (existing `is_night_mode`) as a calm clock-plus-weather screen.

### Phase 3: Device behaviour and robustness
- ESPHome: poll `/epaper-e1002/etag` first, only download/refresh when changed (saves battery + panel wear); report battery to `/api/device/battery` (route exists).
- Server-side "stale data" badge when any source older than N minutes (some of this exists in `_data_age_label`).
- Smarter refresh cadence: shorter at mornings/commute (e.g. 15 min 6–9), hourly otherwise, via `Refresh`/sleep-duration hint endpoint the ESPHome config reads.
- Optional auth token for upload/calendar endpoints (header token in `config.py`), still LAN-only by default.

### Phase 4: Nice-to-haves
- Photo/artwork or quote mode on E1002 using Floyd–Steinberg to 6 colours when nothing urgent to show.
- Multiple calendars with a legend, week view on Sundays, birthday/holiday highlights.
- Golden-image regression tests (hash or perceptual diff against `tests/golden/` with tolerance) for the main layouts.
- Split E1001 and E1002 templates into config-driven card lists (`EPAPER_E1002_CARDS = [...]`).

## Suggested order and sizing

| # | Work | Size | Risk |
|---|---|---|---|
| 0 | Tests + preview contact sheet | S | low |
| 1 | Spectra 6 palette + quantised output + preview | M | med: needs on-device check of real pigment colours |
| 2a | Package split (no behaviour change) | M | low with Phase 0 tests |
| 2b | Fonts + icon set + card helpers | M | low |
| 2c | Dashboard redesign card by card | L | needs iteration on hardware photos |
| 3 | ETag polling, adaptive refresh, auth | M | med: ESPHome flashing |
| 4 | Optional features | S each | low |

## Open questions for the owner

- Which panel is the primary target going forward, E1002 only or both? (Affects whether to invest in E1001 redesign.)
- Language: stay Finnish-only on the display?
- Is a photo of the physical panel available to calibrate the pigment RGB values?
- Any must-have data not shown today (e.g. garbage collection, calendar of family, car heater)?
