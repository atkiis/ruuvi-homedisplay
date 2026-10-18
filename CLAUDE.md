# CLAUDE.md

Flask home dashboard (Ruuvi sensors, Finnish spot prices, Nysse buses, weather, ICS calendar) with two outputs:
a browser dashboard (`/`) and server-rendered 800×480 images for Seeed reTerminal **E1001** (mono) and **E1002**
(Spectra 6 colour) e-paper displays driven by ESPHome. See [README.md](README.md) for user docs and
[PLAN.md](PLAN.md) for the e-paper roadmap.

## Commands

```bash
source .venv/bin/activate && pip install -r requirements.txt   # Python 3.11+
python app.py                                  # http://localhost:5001 ; previews at /epaper and /epaper-e1002
python -m pytest tests/                        # pytest is NOT in requirements.txt; install it (or add requirements-dev.txt)
curl -s localhost:5001/epaper-e1002.png -o /tmp/e.png   # inspect rendered output (use scratchpad dir, not /tmp, in Claude sessions)
esphome run esphome/ruuvi-homedisplay-e1002.yaml
```

`DEMO_MODE = True` in `config.py` gives simulated Ruuvi data (no Bluetooth). Always check rendered PNGs visually
after e-paper changes; passing tests do not prove the image looks right.

## Layout

| File | Role |
|---|---|
| `app.py` | Routes: dashboard, `/api/*`, `/epaper*.png|.bmp|/etag`, calendar upload/categories |
| `config.py` | All user settings (tags, stops, API keys, e-paper flags). Keep new settings here, with comments |
| `epaper.py` | Pillow renderer (~1750 lines): E1001 landscape/portrait/sauna, E1002 dashboard (`_render_e1002`), E1002 calendar (`_render_e1002_calendar`), render cache + ETag |
| `ruuvi_reader.py`, `electricity.py`, `buses.py`, `weather.py` | Data sources with caching; background thread for BLE |
| `calendar_backend.py` / `calendar_view.py` | ICS parsing + persistence in `instance/` / normalising events for HTML + e-paper |
| `templates/`, `static/` | Browser dashboard and the E1002 calendar preview/upload UI |
| `esphome/` | Device configs (deep sleep, download PNG, draw). `secrets.yaml` is local, never commit real secrets |
| `.github/agents/*.agent.md` | Existing Copilot role agents (researcher → project-lead → backend/frontend/testing); mirrored below |

## Conventions

- Match surrounding code: small module-level helpers, `_prefixed` private functions, section comment banners in `epaper.py`.
- Every data source must degrade gracefully (network, BLE, API-key failures are normal): show stale/`--`/"ei dataa", never crash a render. The e-paper must always return a valid image.
- Preserve public routes, config names and response shapes unless the task says otherwise.
- UI text is Finnish on the e-paper (Sää, Kalenteri, Päivitetty, weekday abbreviations `Ma Ti Ke…`), timezone `Europe/Helsinki`.
- E-paper constraints: no emoji (fonts can't render them), hand-drawn icons via ImageDraw, high contrast, readable at distance, one refresh per hour on E1002 (slow, wears the panel; don't add polling).
- E1001 = 1-bit, `EPAPER_INVERT`; E1002 = colour, `EPAPER_E1002_INVERT`. Polarity is independent per panel. Don't change defaults casually; they were tuned on hardware (see git log "invert e1002").
- Fonts are looked up from system paths (Roboto/Noto/Liberation/DejaVu). Anything depending on a specific font must fall back cleanly.
- Calendar upload endpoints are unauthenticated by design (trusted LAN). Don't expose them or add features that make that worse; validate/size-limit all uploads.
- No new dependencies without a reason; mention it in the summary.
- Don't commit `instance/`, `esphome/secrets.yaml`, `.venv`. Commit only when asked.

## Working modes (from the Copilot agents)

The repo defines a pipeline; use it as a mental checklist even without subagents:

1. **Research**: inspect the owning code, check external docs only if repo evidence is insufficient; separate evidence from guesses. Output: findings, smallest viable approach, files affected, risks, acceptance checks.
2. **Lead**: break into narrow tasks (files, inputs, outputs, verification), no unrelated cleanup, require executable validation.
3. **Backend**: Flask routes, data sources, caching, Pillow rendering, ESPHome-facing payloads. Read the module and call sites first.
4. **Frontend**: templates/CSS/JS; keep scannable at distance, never hide loading/stale/error states, 800×480 constraints.
5. **Testing**: deterministic tests with mocked network/BLE/time; don't weaken assertions; report manual checks separately.

For larger tasks, spawn subagents per role (Explore for research, general-purpose for implementation); for small ones, just do it.

## Testing notes

Existing tests cover only calendar backend/view/routes (`tests/`). The renderer, data sources and routes for the
dashboard are untested. When touching `epaper.py`, add or extend a smoke test (render every layout with mocked
data sources, assert size/mode, no exception with every source failing) and visually check the PNG.
