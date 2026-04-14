# Model Forecast Viewer

[![GitHub last commit](https://img.shields.io/github/last-commit/ShianMike/ModelForecast?style=flat-square&color=blue)](https://github.com/ShianMike/ModelForecast/commits/main)
[![GitHub stars](https://img.shields.io/github/stars/ShianMike/ModelForecast?style=flat-square)](https://github.com/ShianMike/ModelForecast/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/ShianMike/ModelForecast?style=flat-square)](https://github.com/ShianMike/ModelForecast/forks)
[![Made with Python](https://img.shields.io/badge/Python-3.14-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Made with React](https://img.shields.io/badge/React-18-61DAFB?style=flat-square&logo=react&logoColor=black)](https://react.dev/)
[![Deployed on Cloud Run](https://img.shields.io/badge/Cloud%20Run-deployed-4285F4?style=flat-square&logo=googlecloud&logoColor=white)](https://model-forecast-693545589581.us-central1.run.app)
[![License](https://img.shields.io/badge/license-Educational%20%2F%20Research-green?style=flat-square)](#license)

A full-stack weather model forecast viewer that fetches real-time gridded forecast data from NOAA NOMADS, decodes GRIB2 with a pure-Python decoder, and renders interactive map overlays with wind arrows, color-coded parameters, contour lines, animation controls, and point analysis tools — inspired by Pivotal Weather and Aguacero.

**Live site:** <https://shianmike.github.io/ModelForecast/>

---

## Features

### Interactive Forecast Map
- **Leaflet-based map** with CartoDB dark/light basemap tiles
- **Gap-free canvas overlay** rendering gridded forecast data with bilinear edge interpolation
- **Contour lines** with labeled isolines placed on actual contour midpoints
- **Wind arrows** — U/V component vectors drawn on the map when wind parameters are selected
- **Opacity slider** — adjustable overlay transparency (0–100%)
- **Color bar legend** — dynamic per-parameter color scale with labeled tick marks
- **Region presets** — CONUS, North America, Global, North Atlantic, West Pacific, and custom saved regions

### Point Analysis Tools
- **Sounding Profile** — click map to fetch a full Skew-T/Log-P plot via [Sounding Analysis](https://github.com/ShianMike/SoundingAnalysis) integration, with download and external link buttons
- **Meteogram** — time-series forecast at a clicked point across all forecast hours
- **Cross-Section** — vertical cross-section along a user-drawn line
- **Ensemble Plume** — GFS ensemble spread with percentile bands at a clicked point

### Severe Weather Composites
Server-side computed derived parameters:
- **STP (Sig Tornado Parameter)** — approximated from CAPE, CIN, bulk shear
- **SCP (Supercell Composite)** — CAPE × shear × SRH proxy
- **SHIP (Sig Hail Parameter)** — CAPE × mixing ratio × lapse rate × shear
- **Tornado Composite** — multi-ingredient composite with CIN damping
- **Effective Bulk Shear** — |V500 − Vsfc| scalar approximation
- **1000–500 hPa Thickness** — geopotential thickness

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

### Forecast Parameters (20 unique, 5 categories)

| Category | Parameters |
|----------|-----------|
| **Surface & Near-Surface** | Temperature (2m), Dewpoint (2m), Wind Speed (10m), Wind Gusts (10m), Surface Pressure (MSLP) |
| **Precipitation & Moisture** | Accumulated Precipitation, Snowfall, CAPE, Cloud Cover |
| **Upper Air** | 500 hPa Geopotential Height, 850 hPa Temperature, 250/500/850 hPa Wind Speed, 1000–500 hPa Thickness |
| **Severe Weather** | CAPE, CIN, Wind Gusts, Visibility, Effective Bulk Shear |
| **Severe Composites** | STP (Sig Tornado), SCP (Supercell), SHIP (Sig Hail), Tornado Composite |

### Export & Sharing
- **PNG export** — composited map image with parameter name, model/init-time/valid-time stamp, and color legend overlay
- **Shareable URLs** — model, parameter, forecast hour, and region encoded in the URL hash

### Animation Controls
- Play/pause/step through forecast hours with weekday + Zulu date/time display
- Adjustable playback speed (0.5×, 1×, 2×, 4×)
- Frame caching for smooth playback

### Additional Features
- **Dark/light theme** toggle (persisted)
- **Colorblind mode** (persisted)
- **Custom regions** — save/delete map bounds to localStorage
- **Draggable panels** — all point analysis panels can be repositioned and minimized

---

## Project Structure

```
├── app.py                 # Flask entry point (CORS, Talisman, rate limits, SPA catch-all)
├── gunicorn.conf.py       # Gunicorn WSGI config (reads PORT from env)
├── requirements.txt       # Python dependencies
├── Dockerfile             # Multi-stage build (Node + Python)
├── deploy.ps1             # Frontend build + GitHub Pages deploy script
├── forecast/              # Core data package
│   ├── nomads.py            # NOMADS GRIB filter client (4 models, 20 variables)
│   ├── grib2.py             # Pure-Python GRIB2 decoder (lat/lon + Lambert grids)
│   ├── parameters.py        # Parameter definitions, 18 color scales, categories
│   └── open_meteo.py        # Open-Meteo client (ensemble plume data)
├── routes/                # Flask API blueprints
│   ├── __init__.py          # Blueprint registry
│   ├── forecast_routes.py   # /api/forecast, /api/color-scale, sounding, meteogram, cross-section, ensemble, composites
│   ├── meta.py              # /api/health, /api/models, /api/parameters
│   └── helpers.py           # NaN-safe JSON serializer
└── frontend/              # React 18 + Vite 6
    ├── src/
    │   ├── App.jsx              # Main app, state management, frame cache, export
    │   ├── api.js               # API client (10 endpoints)
    │   └── components/
    │       ├── Sidebar.jsx              # Model/parameter/region selection
    │       ├── Header.jsx               # Top bar, theme toggle, opacity slider
    │       ├── ForecastMap.jsx          # Leaflet map container
    │       ├── CanvasOverlay.jsx        # Canvas grid renderer + wind arrows + contour lines
    │       ├── ColorBar.jsx             # Color scale legend
    │       ├── AnimationControls.jsx    # Play/pause/step/speed + Zulu time display
    │       ├── ParameterPicker.jsx      # Grouped parameter selector
    │       ├── SoundingProfile.jsx      # Skew-T sounding viewer (via SA proxy)
    │       ├── Meteogram.jsx            # Point time-series chart
    │       ├── CrossSection.jsx         # Vertical cross-section viewer
    │       └── EnsemblePlume.jsx        # Ensemble spread chart
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

| Component | Platform | URL |
|-----------|----------|-----|
| **Backend** | Google Cloud Run (us-central1) | `https://model-forecast-693545589581.us-central1.run.app` |
| **Frontend** | GitHub Pages | `https://shianmike.github.io/ModelForecast/` |

**Cloud Run config:** 1 GiB memory, 1 vCPU, max 2 instances, 300 s timeout, 10 concurrency.
Persistent run cache: `gs://model-forecast-run-cache-693545589581`

### Deploy Commands

```powershell
# Backend → Cloud Run
gcloud run deploy model-forecast --source . --project model-forecast-app --region us-central1 --platform managed --allow-unauthenticated --memory 1Gi --cpu 1 --timeout 300 --max-instances 2 --concurrency 10 --port 8080 --set-env-vars "GUNICORN_THREADS=4,WEB_CONCURRENCY=2,FORECAST_CACHE_BUCKET=model-forecast-run-cache-693545589581" --quiet

# Frontend → GitHub Pages
.\deploy.ps1
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/health` | Health check |
| `GET` | `/api/models` | List available models with metadata |
| `GET` | `/api/parameters?model={m}` | Parameters grouped by category |
| `GET` | `/api/forecast?model={m}&variable={v}&fhour={h}` | Gridded forecast data (lats, lons, values, wind U/V) |
| `GET` | `/api/color-scale?cmap={name}` | Color scale stops for a parameter's colormap |
| `GET` | `/api/meteogram?model={m}&lat={lat}&lon={lon}` | Full time-series at a single point |
| `GET` | `/api/sounding?model={m}&lat={lat}&lon={lon}&fhour={h}` | Vertical profile data at a point |
| `GET` | `/api/sounding-plot?model={m}&lat={lat}&lon={lon}&fhour={h}` | Skew-T plot image (proxied from Sounding Analysis) |
| `GET` | `/api/cross-section?model={m}&variable={v}&fhour={h}&lat1=...` | Vertical cross-section along a line |
| `GET` | `/api/ensemble?lat={lat}&lon={lon}&variable={v}` | Ensemble plume data from Open-Meteo |

---

## Security

- **HTTPS:** forced in production via Flask-Talisman (HSTS 2-year preload)
- **Content Security Policy:** restrictive CSP allowing only required external resources (Carto, OSM, Google Fonts, NOMADS)
- **CORS:** production origin (`shianmike.github.io`) + localhost for development
- **Rate limiting:** Flask-Limiter — 2000 req/min, 120 req/sec (production only)
- **Security headers:** X-Content-Type-Options, X-Frame-Options DENY, COOP, CORP, Permissions-Policy, Referrer-Policy
- **Input validation:** path-traversal blocking, 16 MB request-size limit

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
| html2canvas-pro | PNG export |
| Lucide React | SVG iconography |

---

## Related Projects

- **[Sounding Analysis](https://github.com/ShianMike/SoundingAnalysis)** — Upper-air sounding analysis platform with Skew-T, hodograph, 50+ parameters, risk scanner, and radar overlays. Model Forecast integrates with SA for point sounding plots.

---

## License

This project is for educational and research purposes.
