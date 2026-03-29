import { useState, useEffect, useCallback, useRef } from "react";
import Header from "./components/Header";
import Sidebar from "./components/Sidebar";
import ForecastMap from "./components/ForecastMap";
import ColorBar from "./components/ColorBar";
import AnimationControls from "./components/AnimationControls";
import { fetchModels, fetchParameters, fetchForecast, fetchColorScale } from "./api";

/* Resolve the cmap name for a parameter from the categories metadata */
const PARAM_CMAP_FALLBACK = {
  temperature_2m: "temperature",
  dewpoint_2m: "dewpoint",
  wind_speed_10m: "wind",
  wind_gusts_10m: "wind",
  surface_pressure: "pressure",
  precipitation: "precip",
  snowfall: "snow",
  cape: "cape",
  cloud_cover: "precip",
  geopotential_height_500hPa: "heights",
  temperature_850hPa: "temperature",
  wind_speed_250hPa: "jet",
  wind_speed_500hPa: "wind",
  wind_speed_850hPa: "wind",
  convective_inhibition: "cin",
  visibility: "wind",
};
function resolveCmap(param, categories) {
  if (categories) {
    for (const cat of Object.values(categories)) {
      const p = cat.params?.[param];
      if (p?.cmap) return p.cmap;
    }
  }
  return PARAM_CMAP_FALLBACK[param] || param;
}
import "./App.css";

/* ── Persisted preferences ───────────────────────────────── */
function loadPref(key, fallback) {
  try { return localStorage.getItem(key) || fallback; } catch { return fallback; }
}
function savePref(key, val) {
  try { localStorage.setItem(key, val); } catch { /* noop */ }
}

/* ── Region presets ──────────────────────────────────────── */
const REGIONS = {
  conus:  { north: 50, south: 24, west: -125, east: -66, label: "CONUS" },
  namer:  { north: 72, south: 10, west: -170, east: -50, label: "N. America" },
  global: { north: 85, south: -85, west: -180, east: 180, label: "Global" },
  natl:   { north: 60, south: 5, west: -100, east: -10, label: "N. Atlantic" },
  wpac:   { north: 50, south: -10, west: 100, east: 180, label: "W. Pacific" },
};

export default function App() {
  /* ── Theme ─────────────────────────────────────────────── */
  const [theme, setThemeState] = useState(() => loadPref("mf_theme", "dark"));
  const [colorblind, setColorblindState] = useState(() => loadPref("mf_cb", "false") === "true");
  useEffect(() => { document.documentElement.setAttribute("data-theme", theme); savePref("mf_theme", theme); }, [theme]);
  useEffect(() => { document.documentElement.setAttribute("data-cb", String(colorblind)); savePref("mf_cb", String(colorblind)); }, [colorblind]);
  const toggleTheme = () => setThemeState(t => t === "dark" ? "light" : "dark");
  const toggleColorblind = () => setColorblindState(v => !v);

  /* ── Bootstrap data ────────────────────────────────────── */
  const [models, setModels] = useState({});
  const [parameterCategories, setParameterCategories] = useState({});
  const [initLoading, setInitLoading] = useState(true);
  const [initError, setInitError] = useState(null);

  useEffect(() => {
    Promise.all([fetchModels(), fetchParameters()])
      .then(([m, p]) => { setModels(m); setParameterCategories(p); })
      .catch(() => setInitError("Failed to connect to API. Is the backend running on :5001?"))
      .finally(() => setInitLoading(false));
  }, []);

  /* ── Forecast state ────────────────────────────────────── */
  const [selectedModel, setSelectedModel] = useState("gfs");
  const [selectedParam, setSelectedParam] = useState("temperature_2m");
  const [fhour, setFhour] = useState(0);
  const [maxFhour, setMaxFhour] = useState(384);
  const [fhourStep, setFhourStep] = useState(3);
  const [region, setRegion] = useState("conus");
  const [bbox, setBbox] = useState(REGIONS.conus);

  /* ── Gridded data ──────────────────────────────────────── */
  const [gridData, setGridData] = useState(null);
  const [gridLoading, setGridLoading] = useState(false);
  const [gridError, setGridError] = useState(null);
  const [overlayOpacity, setOverlayOpacity] = useState(0.5);

  /* ── Animation state ───────────────────────────────────── */
  const [playing, setPlaying] = useState(false);
  const [animSpeed, setAnimSpeed] = useState(1);
  const animRef = useRef(null);

  /* ── Client-side frame cache (avoids re-fetching viewed frames) ── */
  const frameCacheRef = useRef(new Map());
  const frameCacheKey = (m, p, h, r) => `${m}:${p}:${h}:${r}`;

  /* ── Color scale cache (per cmap name) ── */
  const colorScaleCacheRef = useRef({});

  /* Update model constraints when model changes */
  useEffect(() => {
    const info = models[selectedModel];
    if (info) {
      setMaxFhour(info.maxHour || 384);
      setFhourStep(info.step || 3);
      if (fhour > (info.maxHour || 384)) setFhour(0);
    }
  }, [selectedModel, models]);

  /* Re-fetch parameters filtered for this model */
  useEffect(() => {
    fetchParameters(selectedModel)
      .then((p) => {
        setParameterCategories(p);
        // If selected param is not in the new set, reset to first available
        const allParams = Object.values(p).flatMap((c) => Object.keys(c.params || {}));
        if (allParams.length && !allParams.includes(selectedParam)) {
          setSelectedParam(allParams[0]);
        }
      })
      .catch(() => {}); // silently keep previous categories on error
  }, [selectedModel]);

  /* Update bbox when region preset changes */
  useEffect(() => {
    if (REGIONS[region]) setBbox(REGIONS[region]);
  }, [region]);

  /* Clear frame cache when model, param, or region changes */
  useEffect(() => {
    frameCacheRef.current.clear();
  }, [selectedModel, selectedParam, region]);

  /* ── Fetch grid forecast (with client-side frame cache) ── */
  const loadForecast = useCallback(async (hour) => {
    const h = hour ?? fhour;
    const key = frameCacheKey(selectedModel, selectedParam, h, region);

    /* Hit: skip network + loading state entirely */
    const cached = frameCacheRef.current.get(key);
    if (cached) {
      setGridData(cached);
      setGridError(null);
      return;
    }

    setGridLoading(true);
    setGridError(null);
    try {
      const data = await fetchForecast({
        model: selectedModel,
        parameter: selectedParam,
        fhour: h,
        bbox,
      });

      /* Attach matching color scale to grid data for CanvasOverlay */
      const cmap = resolveCmap(selectedParam, parameterCategories);
      let csStops = colorScaleCacheRef.current[cmap];
      if (!csStops) {
        try {
          const cs = await fetchColorScale(cmap);
          csStops = cs.stops || null;
          colorScaleCacheRef.current[cmap] = csStops;
        } catch { csStops = null; }
      }
      if (csStops) data.color_scale = csStops;

      setGridData(data);
      /* Store in cache (cap at 200 frames) */
      if (frameCacheRef.current.size > 200) {
        const oldest = frameCacheRef.current.keys().next().value;
        frameCacheRef.current.delete(oldest);
      }
      frameCacheRef.current.set(key, data);
    } catch (err) {
      setGridError(err.message);
    } finally {
      setGridLoading(false);
    }
  }, [selectedModel, selectedParam, fhour, bbox, region]);

  /* Load forecast when key state changes */
  useEffect(() => {
    if (!initLoading && !initError) loadForecast();
  }, [selectedModel, selectedParam, fhour, bbox, initLoading]);

  /* ── Animation loop ────────────────────────────────────── */
  useEffect(() => {
    if (!playing) {
      if (animRef.current) clearInterval(animRef.current);
      return;
    }
    const interval = 1000 / animSpeed;
    animRef.current = setInterval(() => {
      setFhour(h => {
        const next = h + fhourStep;
        return next > maxFhour ? 0 : next;
      });
    }, interval);
    return () => clearInterval(animRef.current);
  }, [playing, animSpeed, fhourStep, maxFhour]);

  /* ── Compute valid time label ──────────────────────────── */
  const validTimeLabel = gridData?.valid_time || `F${String(fhour).padStart(3, "0")}`;

  if (initLoading) {
    return (
      <div className="app-loading">
        <div className="spinner" />
        <span>Connecting to forecast API…</span>
      </div>
    );
  }
  if (initError) {
    return (
      <div className="app-loading">
        <span className="app-error-text">{initError}</span>
        <button className="btn btn-primary" onClick={() => window.location.reload()}>Retry</button>
      </div>
    );
  }

  return (
    <div className="app">
      <Sidebar
        models={models}
        parameterCategories={parameterCategories}
        selectedModel={selectedModel}
        setSelectedModel={setSelectedModel}
        selectedParam={selectedParam}
        setSelectedParam={setSelectedParam}
        region={region}
        setRegion={setRegion}
        regions={REGIONS}
      />
      <div className="app-main">
        <Header
          theme={theme}
          toggleTheme={toggleTheme}
          colorblind={colorblind}
          toggleColorblind={toggleColorblind}
          model={selectedModel}
          parameter={selectedParam}
          validTime={validTimeLabel}
          overlayOpacity={overlayOpacity}
          setOverlayOpacity={setOverlayOpacity}
        />
        <div className="map-container">
          <ForecastMap
            gridData={gridData}
            loading={gridLoading}
            error={gridError}
            bbox={bbox}
            parameter={selectedParam}
            overlayOpacity={overlayOpacity}
          />
          <ColorBar parameter={selectedParam} parameterCategories={parameterCategories} />
          <AnimationControls
            fhour={fhour}
            setFhour={setFhour}
            maxFhour={maxFhour}
            step={fhourStep}
            playing={playing}
            setPlaying={setPlaying}
            speed={animSpeed}
            setSpeed={setAnimSpeed}
            validTime={validTimeLabel}
            loading={gridLoading}
          />
        </div>
      </div>
    </div>
  );
}
