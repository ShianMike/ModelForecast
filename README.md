# Model Forecast Viewer

[![GitHub last commit](https://img.shields.io/github/last-commit/ShianMike/ModelForecast?style=flat-square&color=blue)](https://github.com/ShianMike/ModelForecast/commits/main)
[![GitHub stars](https://img.shields.io/github/stars/ShianMike/ModelForecast?style=flat-square)](https://github.com/ShianMike/ModelForecast/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/ShianMike/ModelForecast?style=flat-square)](https://github.com/ShianMike/ModelForecast/forks)
[![Made with Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Made with React](https://img.shields.io/badge/React-18-61DAFB?style=flat-square&logo=react&logoColor=black)](https://react.dev/)
[![Deployed on Cloud Run](https://img.shields.io/badge/Cloud%20Run-deployed-4285F4?style=flat-square&logo=googlecloud&logoColor=white)](https://model-forecast-omtebvnjea-uc.a.run.app)
[![License](https://img.shields.io/badge/license-Educational%20%2F%20Research-green?style=flat-square)](#license)

A full-stack weather model forecast viewer that fetches real-time gridded forecast data from NOAA NOMADS, decodes GRIB2 with a pure-Python decoder, and renders interactive map overlays with wind arrows, color-coded parameters, and animation controls — inspired by Pivotal Weather and Aguacero.

**Live site:** <https://shianmike.github.io/ModelForecast/>

---

## Features

### Interactive Forecast Map
- **Leaflet-based map** with CartoDB dark/light basemap tiles
- **Gap-free canvas overlay** rendering gridded forecast data with bilinear edge interpolation
- **Wind arrows** — U/V component vectors drawn on the map when wind parameters are selected
- **Opacity slider** — adjustable overlay transparency (0–100%, default 50%)
- **Color bar legend** — dynamic per-parameter color scale with labeled tick marks
- **Region presets** — quick-select CONUS, Northeast, Southeast, Central, West, and more

### NOAA NOMADS Integration
- Direct GRIB2 filter downloads from NOMADS — no API keys, no rate limits
- **Per-model variable/level overrides** — handles model-specific GRIB variable names (e.g., MSLMA for HRRR/RAP pressure, different cloud cover levels for NAM)
- **Automatic latest-run detection** — finds the most recent model cycle with data available
- **Geographic subsetting** — downloads only the bounding box needed, not the full grid
- **30-minute response cache** with 10-minute latest-run cache

### Pure-Python GRIB2 Decoder
- No C libraries required — works on Windows, Linux, macOS without compiled dependencies
- **Grid templates:** 3.0 (regular lat/lon) and 3.30 (Lambert Conformal Conic with bilinear regridding)
- **Packing templates:** 5.0 (simple packing) and 5.40 (JPEG2000)
- **Sign-magnitude encoding** — correctly decodes GRIB2 scale factors (not two's complement)

### Supported Models

| Model | Resolution | Max Forecast | Cycles | Source |
|-------|-----------|-------------|--------|--------|
| **GFS** | 0.25° (~28 km) | 384 hours | 00/06/12/18Z | NOMADS `filter_gfs_0p25.pl` |
| **HRRR** | 3 km | 48 hours | Every hour | NOMADS `filter_hrrr_2d.pl` |
| **NAM** | 12 km | 84 hours | 00/06/12/18Z | NOMADS `filter_nam.pl` |
| **RAP** | 13 km | 51 hours | Every hour | NOMADS `filter_rap.pl` |

### Forecast Parameters (18)

| Category | Parameters |
|----------|-----------|
| **Surface** | Temperature (2m), Dewpoint (2m), Relative Humidity (2m), Surface Pressure (MSLP) |
| **Wind** | Wind Speed (10m), Wind Gusts (10m) |
| **Precipitation** | Accumulated Precipitation, Snowfall |
| **Radiation** | Shortwave Radiation, Cloud Cover |
| **Convective** | CAPE, Convective Inhibition (CIN) |
| **Visibility** | Surface Visibility |
| **Upper-Air** | 500 hPa Geopotential Height, 850 hPa Temperature, 250/500/850 hPa Wind Speed |

### Animation Controls
- Play/pause/step through forecast hours
- Adjustable playback speed
- Timeline slider with forecast hour labels
- Frame caching for smooth playback

### Theme Support
- Dark theme (default) and light theme toggle
- Consistent styling across all components

---

## Project Structure

```
├── app.py                 # Flask entry point (CORS, Talisman, rate limits, SPA catch-all)
├── gunicorn.conf.py       # Gunicorn WSGI config (reads PORT from env)
├── requirements.txt       # Python dependencies
├── Dockerfile             # Multi-stage build (Node + Python)
├── .github/
│   └── workflows/
│       └── deploy.yml     # GitHub Actions → Cloud Run CI/CD
├── forecast/              # Core data package
│   ├── nomads.py            # NOMADS GRIB filter client (4 models, 18 variables)
│   ├── grib2.py             # Pure-Python GRIB2 decoder (lat/lon + Lambert grids)
│   ├── parameters.py        # Parameter definitions, color scales, categories
│   └── open_meteo.py        # Open-Meteo client (legacy, unused for grid forecasts)
├── routes/                # Flask API blueprints
│   ├── __init__.py          # Blueprint registry
│   ├── forecast_routes.py   # /api/forecast, /api/color-scale
│   ├── meta.py              # /api/health, /api/models, /api/parameters
│   └── helpers.py           # NaN-safe JSON serializer
└── frontend/              # React 18 + Vite 6
    ├── src/
    │   ├── App.jsx              # Main app, state management, frame cache
    │   ├── api.js               # API client
    │   └── components/
    │       ├── Sidebar.jsx              # Model/parameter/region selection
    │       ├── Header.jsx               # Top bar, theme toggle, opacity slider
    │       ├── ForecastMap.jsx          # Leaflet map container
    │       ├── CanvasOverlay.jsx        # Canvas grid renderer + wind arrows
    │       ├── ColorBar.jsx             # Color scale legend
    │       ├── AnimationControls.jsx    # Play/pause/step/speed controls
    │       └── ParameterPicker.jsx      # Grouped parameter selector
    ├── public/
    │   └── manifest.json    # PWA manifest
    ├── package.json
    └── vite.config.js
```

---

## Quick Start

### Backend (Python)

```bash
pip install -r requirements.txt
python app.py
# → http://localhost:5001
```

### Frontend (React)

```bash
cd frontend
npm install
npm run dev
# → http://localhost:3002
```

The Vite dev server proxies `/api` requests to the backend at `localhost:5001`.

---

## Deployment

| Component | Platform | Region | URL |
|-----------|----------|--------|-----|
| **Full-stack** | Google Cloud Run | us-central1 | `https://model-forecast-omtebvnjea-uc.a.run.app` |

**Cloud Run configuration:** 512 MiB memory, 1 vCPU, max 3 instances, 300 s timeout, 2 workers × 4 threads.

### CI/CD Pipeline

Every push to `main` triggers a GitHub Actions workflow that:

1. Authenticates to GCP via Workload Identity Federation (keyless)
2. Builds a multi-stage Docker image (Node 20 frontend build + Python 3.12 runtime)
3. Pushes to Artifact Registry (`us-central1-docker.pkg.dev`)
4. Deploys to Cloud Run

No service account keys are stored — authentication uses OIDC tokens scoped to the repository.

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/health` | Health check |
| `GET` | `/api/models` | List available models with metadata (resolution, max hour, step) |
| `GET` | `/api/parameters?model={m}` | Get supported parameters for a model, grouped by category |
| `GET` | `/api/forecast?model={m}&variable={v}&fhour={h}&lat_min=...` | Gridded forecast data (lats, lons, values, wind U/V) |
| `GET` | `/api/color-scale?cmap={name}` | Color scale stops for a parameter's colormap |

### `GET /api/forecast` — Query Parameters

| Parameter | Required | Example | Description |
|-----------|----------|---------|-------------|
| `model` | Yes | `gfs` | Model name (gfs, hrrr, nam, rap) |
| `variable` | Yes | `temperature_2m` | Parameter key |
| `fhour` | Yes | `6` | Forecast hour |
| `lat_min` | No | `24.0` | Bounding box south edge (default: CONUS) |
| `lat_max` | No | `50.0` | Bounding box north edge |
| `lon_min` | No | `-125.0` | Bounding box west edge |
| `lon_max` | No | `-66.0` | Bounding box east edge |

---

## Security

- **HTTPS:** forced in production via Flask-Talisman (HSTS 2-year preload)
- **Content Security Policy:** restrictive CSP with nonce-based script-src
- **CORS:** locked to production origins only (localhost allowed in development)
- **Rate limiting:** Flask-Limiter — 200 req/min global, 30 req/sec burst
- **Security headers:** X-Content-Type-Options, X-Frame-Options DENY, COOP, CORP, Permissions-Policy, Referrer-Policy
- **Input validation:** path-traversal blocking, 16 MB request-size limit
- **Workload Identity Federation:** keyless GCP auth for CI/CD — no service account keys in secrets

---

## Dependencies

### Python

| Package | Purpose |
|---------|---------|
| Flask 3.1 | Web framework |
| flask-cors 4.0 | CORS headers |
| flask-limiter 3.5 | Rate limiting |
| flask-talisman 1.1 | Security headers |
| gunicorn 22.0 | WSGI server |
| requests 2.31 | NOMADS HTTP client |
| numpy 1.26 | Array operations |
| Pillow 10.0 | Image generation for color scales |

### Frontend

| Package | Purpose |
|---------|---------|
| React 18.3 | UI framework |
| Vite 6 | Build tooling |
| Leaflet + React-Leaflet | Interactive maps |
| D3 7.9 | Data visualization |
| Recharts 3.7 | Charts |
| Lucide React | SVG iconography |

---

## Related Projects

- **[Sounding Analysis](https://github.com/ShianMike/SoundingAnalysis)** — Upper-air sounding analysis platform with Skew-T, hodograph, 50+ parameters, risk scanner, and radar overlays

---

## License

This project is for educational and research purposes.
