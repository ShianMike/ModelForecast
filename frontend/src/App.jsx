import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import Header from "./components/Header";
import Sidebar from "./components/Sidebar";
import ForecastMap from "./components/ForecastMap";
import ColorBar from "./components/ColorBar";
import AnimationControls from "./components/AnimationControls";
import Meteogram from "./components/Meteogram";
import SoundingProfile from "./components/SoundingProfile";
import CrossSection from "./components/CrossSection";
import EnsemblePlume from "./components/EnsemblePlume";
import { validZuluLabel } from "./timeUtils";

import { fetchModels, fetchParameters, fetchForecast, fetchColorScale, fetchMeteogram, fetchSoundingPlot, fetchCrossSection, fetchEnsemble } from "./api";
import GridWorker from "./workers/gridWorker?worker";

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
  effective_bulk_shear: "shear",
  stp_approx: "stp",
  scp: "scp",
  ship: "ship",
  critical_angle_composite: "tornado_composite",
  thickness_1000_500: "thickness",
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

/* ── URL hash state helpers ──────────────────────────────── */
function parseHash() {
  try {
    const hash = window.location.hash.slice(1);
    if (!hash) return {};
    return Object.fromEntries(new URLSearchParams(hash));
  } catch { return {}; }
}
function writeHash(state) {
  const params = new URLSearchParams();
  if (state.model) params.set("model", state.model);
  if (state.param) params.set("param", state.param);
  if (state.fhour != null) params.set("fhour", String(state.fhour));
  if (state.region) params.set("region", state.region);
  const str = params.toString();
  window.history.replaceState(null, "", str ? `#${str}` : window.location.pathname);
}

/* ── Region presets ──────────────────────────────────────── */
const BUILTIN_REGIONS = {
  conus:  { north: 50, south: 24, west: -125, east: -66, label: "CONUS" },
  namer:  { north: 72, south: 10, west: -170, east: -50, label: "N. America" },
  global: { north: 85, south: -85, west: -180, east: 180, label: "Global" },
  natl:   { north: 60, south: 5, west: -100, east: -10, label: "N. Atlantic" },
  wpac:   { north: 50, south: -10, west: 100, east: 180, label: "W. Pacific" },
};

/* Load user-saved custom regions from localStorage */
function loadCustomRegions() {
  try {
    const raw = localStorage.getItem("mf_custom_regions");
    return raw ? JSON.parse(raw) : {};
  } catch { return {}; }
}
function saveCustomRegions(regions) {
  try { localStorage.setItem("mf_custom_regions", JSON.stringify(regions)); } catch { /* noop */ }
}

const DIFF_SCALE = [
  [-20, 0, 0, 200, 255],
  [-10, 50, 100, 255, 220],
  [-5, 100, 180, 255, 180],
  [-1, 180, 220, 255, 120],
  [0, 255, 255, 255, 40],
  [1, 255, 220, 180, 120],
  [5, 255, 180, 100, 180],
  [10, 255, 100, 50, 220],
  [20, 200, 0, 0, 255],
];

function computeDiffValuesSync(vals, prevVals) {
  if (!Array.isArray(vals) || !Array.isArray(prevVals)) return null;
  const is2D = Array.isArray(vals[0]);
  if (is2D) {
    return vals.map((row, i) =>
      row.map((v, j) => {
        const pv = prevVals[i]?.[j];
        if (!Number.isFinite(v) || !Number.isFinite(pv)) return null;
        return v - pv;
      })
    );
  }
  return vals.map((v, i) => {
    const pv = prevVals[i];
    if (!Number.isFinite(v) || !Number.isFinite(pv)) return null;
    return v - pv;
  });
}

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
  const initHash = useRef(parseHash());
  const [selectedModel, setSelectedModel] = useState(initHash.current.model || "gfs");
  const [selectedParam, setSelectedParam] = useState(initHash.current.param || "temperature_2m");
  const [fhour, setFhour] = useState(initHash.current.fhour ? Number(initHash.current.fhour) : 0);
  const [maxFhour, setMaxFhour] = useState(384);
  const [fhourStep, setFhourStep] = useState(3);
  const [region, setRegion] = useState(initHash.current.region || "conus");
  const [customRegions, setCustomRegions] = useState(loadCustomRegions);
  const REGIONS = useMemo(() => ({ ...BUILTIN_REGIONS, ...customRegions }), [customRegions]);
  const [bbox, setBbox] = useState(REGIONS[initHash.current.region] || BUILTIN_REGIONS.conus);

  /* ── Model comparison state ────────────────────────────── */
  const [compareMode, setCompareMode] = useState(false);
  const [compareModel, setCompareModel] = useState("nam");
  const [compareGridData, setCompareGridData] = useState(null);
  const [compareGridLoading, setCompareGridLoading] = useState(false);
  const gridRequestIdRef = useRef(0);
  const compareRequestIdRef = useRef(0);

  /* ── Map ref for reading current bounds (custom region save) ── */
  const mapInstanceRef = useRef(null);

  const handleSaveRegion = useCallback((name) => {
    const map = mapInstanceRef.current;
    if (!map || !name) return;
    const bounds = map.getBounds();
    const key = name.toLowerCase().replace(/[^a-z0-9]/g, "_");
    const newRegion = {
      north: Math.round(bounds.getNorth() * 100) / 100,
      south: Math.round(bounds.getSouth() * 100) / 100,
      west: Math.round(bounds.getWest() * 100) / 100,
      east: Math.round(bounds.getEast() * 100) / 100,
      label: name,
      custom: true,
    };
    const updated = { ...customRegions, [key]: newRegion };
    setCustomRegions(updated);
    saveCustomRegions(updated);
    setRegion(key);
  }, [customRegions]);

  const handleDeleteRegion = useCallback((key) => {
    const updated = { ...customRegions };
    delete updated[key];
    setCustomRegions(updated);
    saveCustomRegions(updated);
    if (region === key) setRegion("conus");
  }, [customRegions, region]);

  /* ── Gridded data ──────────────────────────────────────── */
  const [gridData, setGridData] = useState(null);
  const [gridLoading, setGridLoading] = useState(false);
  const [gridError, setGridError] = useState(null);
  const [overlayOpacity, setOverlayOpacity] = useState(0.5);
  const [showContours, setShowContours] = useState(false);
  const [showWindParticles, setShowWindParticles] = useState(true);
  const [useWebGL, setUseWebGL] = useState(false);
  const [diffMode, setDiffMode] = useState(false); /* show field differences between frames */

  /* ── Resolve parameter display name & unit from categories ── */
  const paramInfo = useMemo(() => {
    if (!parameterCategories || !selectedParam) return { name: selectedParam, unit: "" };
    for (const cat of Object.values(parameterCategories)) {
      const p = cat.params?.[selectedParam];
      if (p) return { name: p.name || selectedParam, unit: p.unit || "" };
    }
    return { name: selectedParam, unit: "" };
  }, [selectedParam, parameterCategories]);

  /* ── Export current map view as PNG ────────────────────── */
  const handleExport = useCallback(async () => {
    const maps = document.querySelectorAll(".forecast-map");
    if (!maps.length) return;

    /**
     * Manually compose a screenshot of a Leaflet map element.
     * html-to-image cannot capture cross-origin tile images, so we:
     *   1. Draw the map background colour.
     *   2. Re-fetch each tile <img> with crossOrigin="anonymous" (CartoCDN supports CORS)
     *      so the browser issues a fresh CORS request, separate from the non-CORS cache entry.
     *   3. Draw every <canvas> inside the container (CanvasOverlay, deck.gl, etc.)
     *      using getBoundingClientRect to get the correct on-screen position.
     */
    const SCALE = 2;
    const captureMapEl = async (mapEl) => {
      const rect = mapEl.getBoundingClientRect();
      const W = Math.round(rect.width);
      const H = Math.round(rect.height);

      const out = document.createElement("canvas");
      out.width = W * SCALE;
      out.height = H * SCALE;
      const ctx = out.getContext("2d");
      ctx.scale(SCALE, SCALE);

      // Background
      const isDark = document.documentElement.getAttribute("data-theme") !== "light";
      ctx.fillStyle = isDark ? "#1a1a2e" : "#e8e8e8";
      ctx.fillRect(0, 0, W, H);

      // Tile images — re-fetched with CORS so they can be drawn on canvas
      const tileImgs = mapEl.querySelectorAll(".leaflet-tile-pane img.leaflet-tile-loaded");
      await Promise.all(Array.from(tileImgs).map(async (tileImg) => {
        const tr = tileImg.getBoundingClientRect();
        const x = tr.left - rect.left;
        const y = tr.top - rect.top;
        const w = tr.width;
        const h = tr.height;
        if (w <= 0 || h <= 0) return;
        const img = new Image();
        img.crossOrigin = "anonymous";
        await new Promise((res) => { img.onload = res; img.onerror = res; img.src = tileImg.src; });
        if (img.naturalWidth > 0) ctx.drawImage(img, x, y, w, h);
      }));

      // All canvases (CanvasOverlay, deck.gl, leaflet-velocity, etc.)
      const canvases = mapEl.querySelectorAll("canvas");
      for (const c of canvases) {
        try {
          const cr = c.getBoundingClientRect();
          if (cr.width <= 0 || cr.height <= 0) continue;
          const cx = cr.left - rect.left;
          const cy = cr.top - rect.top;
          ctx.drawImage(c, cx, cy, cr.width, cr.height);
        } catch { /* tainted or zero-size canvas — skip */ }
      }

      return out;
    };

      /* Helper: draw annotations onto a canvas context at a given x-offset */
      const annotatePanel = (ctx, xOff, W, H, scale, modelLabel, zuluStr) => {
        const PAD = 16;

        /* Top-left: parameter name */
        const titleText = `${paramInfo.name}${paramInfo.unit ? ` (${paramInfo.unit})` : ""}`;
        const titleFontSize = Math.round(16 * scale);
        ctx.font = `bold ${titleFontSize}px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
        const titleMetrics = ctx.measureText(titleText);
        const titlePadH = Math.round(12 * scale);
        const titlePadV = Math.round(8 * scale);
        const titleX = xOff + PAD * scale;
        const titleY = PAD * scale;
        const titleBoxW = titleMetrics.width + titlePadH * 2;
        const titleBoxH = titleFontSize + titlePadV * 2;
        ctx.fillStyle = "rgba(0,0,0,0.7)";
        ctx.beginPath();
        ctx.roundRect(titleX, titleY, titleBoxW, titleBoxH, 6 * scale);
        ctx.fill();
        ctx.fillStyle = "#ffffff";
        ctx.textBaseline = "middle";
        ctx.textAlign = "left";
        ctx.fillText(titleText, titleX + titlePadH, titleY + titleBoxH / 2);

        /* Bottom-left: model + valid time */
        const fhourLabel = `F${String(fhour).padStart(3, "0")}`;
        const stampText = `${modelLabel}  ${fhourLabel}  ${zuluStr}`;
        const stampFontSize = Math.round(13 * scale);
        ctx.font = `bold ${stampFontSize}px "SF Mono", "Cascadia Code", "Consolas", monospace`;
        const stampMetrics = ctx.measureText(stampText);
        const stampPadH = Math.round(10 * scale);
        const stampPadV = Math.round(6 * scale);
        const stampBoxW = stampMetrics.width + stampPadH * 2;
        const stampBoxH = stampFontSize + stampPadV * 2;
        const stampX = xOff + PAD * scale;
        const stampY = H - PAD * scale - stampBoxH;
        ctx.fillStyle = "rgba(0,0,0,0.7)";
        ctx.beginPath();
        ctx.roundRect(stampX, stampY, stampBoxW, stampBoxH, 6 * scale);
        ctx.fill();
        ctx.fillStyle = "#ffffff";
        ctx.textBaseline = "middle";
        ctx.textAlign = "left";
        ctx.fillText(stampText, stampX + stampPadH, stampY + stampBoxH / 2);

        /* Bottom-right: color legend */
        const stops = gridData?.color_scale;
        if (stops && stops.length >= 2) {
          const barW = Math.round(220 * scale);
          const barH = Math.round(12 * scale);
          const legendPad = Math.round(10 * scale);
          const legendGap = Math.round(4 * scale);
          const labelFontSize = Math.round(10 * scale);
          ctx.font = `bold ${labelFontSize}px "SF Mono", "Cascadia Code", "Consolas", monospace`;

          const tickStep = Math.max(1, Math.floor(stops.length / 6));
          const ticks = stops.filter((_, i) => i % tickStep === 0 || i === stops.length - 1);

          const boxW = barW + legendPad * 2;
          const boxH = barH + legendGap + labelFontSize + legendPad * 2;
          const boxX = xOff + W - PAD * scale - boxW;
          const boxY = H - PAD * scale - boxH;

          ctx.fillStyle = "rgba(0,0,0,0.7)";
          ctx.beginPath();
          ctx.roundRect(boxX, boxY, boxW, boxH, 6 * scale);
          ctx.fill();

          const barX = boxX + legendPad;
          const barY = boxY + legendPad;
          const grad = ctx.createLinearGradient(barX, 0, barX + barW, 0);
          for (let i = 0; i < stops.length; i++) {
            const t = i / (stops.length - 1);
            const s = stops[i];
            grad.addColorStop(t, `rgb(${s[1]},${s[2]},${s[3]})`);
          }
          ctx.fillStyle = grad;
          ctx.fillRect(barX, barY, barW, barH);
          ctx.strokeStyle = "rgba(255,255,255,0.3)";
          ctx.lineWidth = 1;
          ctx.strokeRect(barX, barY, barW, barH);

          ctx.fillStyle = "rgba(255,255,255,0.9)";
          ctx.font = `${labelFontSize}px "SF Mono", "Cascadia Code", "Consolas", monospace`;
          ctx.textBaseline = "top";
          ctx.textAlign = "center";
          for (const tick of ticks) {
            const t = stops.indexOf(tick) / (stops.length - 1);
            const x = barX + t * barW;
            ctx.fillText(String(tick[0]), x, barY + barH + legendGap);
          }
        }
      };

    try {
      const isCompare = compareMode && maps.length >= 2;

      /* Capture the primary map */
      const primaryEl = maps[0];
      const primaryCanvas = await captureMapEl(primaryEl);
      const W = primaryCanvas.width;
      const H = primaryCanvas.height;
      const scale = SCALE; // captureMapEl always uses SCALE=2

      if (isCompare) {
        /* Capture the comparison map */
        const compareEl = maps[1];
        const compareCanvas = await captureMapEl(compareEl);
        const W2 = compareCanvas.width;
        const H2 = compareCanvas.height;
        const GAP = Math.round(4 * scale);

        const out = document.createElement("canvas");
        out.width = W + GAP + W2;
        out.height = Math.max(H, H2);
        const ctx = out.getContext("2d");

        /* Dark background for the gap */
        ctx.fillStyle = "#1a1a2e";
        ctx.fillRect(0, 0, out.width, out.height);

        /* Draw both maps */
        ctx.drawImage(primaryCanvas, 0, 0, W, H);
        ctx.drawImage(compareCanvas, W + GAP, 0, W2, H2);

        /* Annotate each panel */
        const primaryZulu = validZuluLabel(fhour, gridData?.valid_time, gridData?.run);
        annotatePanel(ctx, 0, W, H, scale, selectedModel?.toUpperCase() || "", primaryZulu);

        const compareZulu = validZuluLabel(fhour, compareGridData?.valid_time, compareGridData?.run);
        annotatePanel(ctx, W + GAP, W2, H2, scale, compareModel?.toUpperCase() || "", compareZulu);

        const link = document.createElement("a");
        link.download = `${selectedModel}_vs_${compareModel}_${selectedParam}_F${String(fhour).padStart(3, "0")}.png`;
        link.href = out.toDataURL("image/png");
        link.click();
      } else {
        /* Single map export — annotate directly on the captured canvas */
        const ctx = primaryCanvas.getContext("2d");
        const zuluLabel = validZuluLabel(fhour, gridData?.valid_time, gridData?.run);
        annotatePanel(ctx, 0, W, H, scale, selectedModel?.toUpperCase() || "", zuluLabel);

        const link = document.createElement("a");
        link.download = `${selectedModel}_${selectedParam}_F${String(fhour).padStart(3, "0")}.png`;
        link.href = primaryCanvas.toDataURL("image/png");
        link.click();
      }
    } catch (err) {
      console.error("Export failed:", err);
    }
  }, [selectedModel, selectedParam, fhour, paramInfo, gridData, compareMode, compareModel, compareGridData]);

  /* Write URL hash when state changes */
  useEffect(() => {
    writeHash({ model: selectedModel, param: selectedParam, fhour, region });
  }, [selectedModel, selectedParam, fhour, region]);

  /* ── Meteogram state ────────────────────────────────────── */
  const [meteogramData, setMeteogramData] = useState(null);
  const [meteogramLoading, setMeteogramLoading] = useState(false);
  const [meteogramPoint, setMeteogramPoint] = useState(null);

  /* ── Sounding state ────────────────────────────────────── */
  const [soundingLoading, setSoundingLoading] = useState(false);
  const [soundingPlot, setSoundingPlot] = useState(null);

  /* ── Cross-section state ───────────────────────────────── */
  const [crossData, setCrossData] = useState(null);
  const [crossLoading, setCrossLoading] = useState(false);
  const [crossProgress, setCrossProgress] = useState(null);
  const [crossDrawMode, setCrossDrawMode] = useState(false);
  const crossLineRef = useRef([]); // collected [latlng, latlng]

  /* ── Ensemble plume state ──────────────────────────────── */
  const [ensembleData, setEnsembleData] = useState(null);
  const [ensembleLoading, setEnsembleLoading] = useState(false);

  /* ── Popup panel state (single active popup at a time) ── */
  const [activePanel, setActivePanel] = useState(null); // sounding | meteogram | ensemble | hodograph | cross | null

  /* Keep Header API shape while enforcing single active panel */
  const setShowSounding = useCallback((updater) => {
    setActivePanel((prev) => {
      const current = prev === "sounding";
      const next = typeof updater === "function" ? updater(current) : Boolean(updater);
      if (next) return "sounding";
      return current ? null : prev;
    });
  }, []);

  const setShowMeteogram = useCallback((updater) => {
    setActivePanel((prev) => {
      const current = prev === "meteogram";
      const next = typeof updater === "function" ? updater(current) : Boolean(updater);
      if (next) return "meteogram";
      return current ? null : prev;
    });
  }, []);

  const setShowEnsemble = useCallback((updater) => {
    setActivePanel((prev) => {
      const current = prev === "ensemble";
      const next = typeof updater === "function" ? updater(current) : Boolean(updater);
      if (next) return "ensemble";
      return current ? null : prev;
    });
  }, []);



  /* If user switches away from cross-section, cancel draw mode state */
  useEffect(() => {
    if (activePanel !== "cross" && crossDrawMode) {
      setCrossDrawMode(false);
      crossLineRef.current = [];
    }
  }, [activePanel, crossDrawMode]);

  /* Track last fetched key per point-panel to avoid duplicate refetches on panel toggles */
  const pointPanelFetchKeyRef = useRef({ meteogram: null, sounding: null, ensemble: null });

  const loadMeteogramForPoint = useCallback(async (lat, lon) => {
    setMeteogramLoading(true);
    setMeteogramData(null);
    try {
      const raw = await fetchMeteogram({ model: selectedModel, lat, lon });
      const vars = raw.variables || {};
      setMeteogramData({
        time: raw.times || [],
        temperature_2m: vars.temperature_2m || [],
        dewpoint_2m: vars.dew_point_2m || [],
        wind_speed_10m: vars.wind_speed_10m || [],
        wind_gusts_10m: vars.wind_gusts_10m || [],
        precipitation: vars.precipitation || [],
        cape: vars.cape || [],
        cloud_cover: vars.cloud_cover || [],
        units: raw.units || {},
      });
    } catch {
      setMeteogramData(null);
    } finally {
      setMeteogramLoading(false);
    }
  }, [selectedModel]);

  const loadSoundingForPoint = useCallback(async (lat, lon) => {
    setSoundingLoading(true);
    setSoundingPlot(null);
    try {
      const plot = await fetchSoundingPlot({
        model: selectedModel,
        lat,
        lon,
        fhour,
        theme,
        colorblind,
        run: gridData?.run,
      });
      setSoundingPlot(plot);
    } catch {
      setSoundingPlot(null);
    } finally {
      setSoundingLoading(false);
    }
  }, [selectedModel, fhour, theme, colorblind, gridData?.run]);

  const loadEnsembleForPoint = useCallback(async (lat, lon) => {
    setEnsembleLoading(true);
    setEnsembleData(null);
    try {
      const raw = await fetchEnsemble({ variable: selectedParam, lat, lon });
      setEnsembleData(raw);
    } catch {
      setEnsembleData(null);
    } finally {
      setEnsembleLoading(false);
    }
  }, [selectedParam]);

  /* Lazy-load panel data when user switches panel for an already selected point */
  useEffect(() => {
    if (!meteogramPoint) return;
    const { lat, lon } = meteogramPoint;

    if (activePanel === "meteogram") {
      const key = `${selectedModel}:${lat.toFixed(3)}:${lon.toFixed(3)}`;
      if (pointPanelFetchKeyRef.current.meteogram !== key) {
        pointPanelFetchKeyRef.current.meteogram = key;
        void loadMeteogramForPoint(lat, lon);
      }
      return;
    }

    if (activePanel === "sounding") {
      const key = `${selectedModel}:${fhour}:${lat.toFixed(3)}:${lon.toFixed(3)}`;
      if (pointPanelFetchKeyRef.current.sounding !== key) {
        pointPanelFetchKeyRef.current.sounding = key;
        void loadSoundingForPoint(lat, lon);
      }
      return;
    }

    if (activePanel === "ensemble") {
      const key = `${selectedParam}:${lat.toFixed(3)}:${lon.toFixed(3)}`;
      if (pointPanelFetchKeyRef.current.ensemble !== key) {
        pointPanelFetchKeyRef.current.ensemble = key;
        void loadEnsembleForPoint(lat, lon);
      }
    }
  }, [activePanel, meteogramPoint, selectedModel, fhour, selectedParam, loadMeteogramForPoint, loadSoundingForPoint, loadEnsembleForPoint]);

  const handleMapClick = useCallback(async (latlng) => {
    const { lat, lng } = latlng;

    /* Cross-section draw mode: collect two points */
    if (crossDrawMode) {
      crossLineRef.current.push({ lat, lon: lng });
      if (crossLineRef.current.length >= 2) {
        const [p1, p2] = crossLineRef.current;
        setCrossDrawMode(false);
        crossLineRef.current = [];
        setCrossLoading(true);
        setCrossProgress(null);
        setActivePanel("cross");
        try {
          const data = await fetchCrossSection({
            model: selectedModel, variable: selectedParam, fhour,
            lat1: p1.lat, lon1: p1.lon, lat2: p2.lat, lon2: p2.lon,
            onProgress: setCrossProgress,
          });
          setCrossData(data);
        } catch {
          setCrossData(null);
        } finally {
          setCrossLoading(false);
          setCrossProgress(null);
        }
      }
      return;
    }

    setMeteogramPoint({ lat, lon: lng });

    /* If no point-based panel is active, default to sounding */
    const panelToLoad =
      (activePanel === "sounding" || activePanel === "meteogram" || activePanel === "ensemble")
        ? activePanel
        : "sounding";

    if (panelToLoad !== activePanel) setActivePanel(panelToLoad);

    if (panelToLoad === "meteogram") {
      pointPanelFetchKeyRef.current.meteogram = `${selectedModel}:${lat.toFixed(3)}:${lng.toFixed(3)}`;
      await loadMeteogramForPoint(lat, lng);
      return;
    }

    if (panelToLoad === "ensemble") {
      pointPanelFetchKeyRef.current.ensemble = `${selectedParam}:${lat.toFixed(3)}:${lng.toFixed(3)}`;
      await loadEnsembleForPoint(lat, lng);
      return;
    }

    /* sounding and hodograph both use sounding data */
    pointPanelFetchKeyRef.current.sounding = `${selectedModel}:${fhour}:${lat.toFixed(3)}:${lng.toFixed(3)}`;
    await loadSoundingForPoint(lat, lng);
  }, [activePanel, selectedModel, fhour, crossDrawMode, selectedParam, loadMeteogramForPoint, loadSoundingForPoint, loadEnsembleForPoint]);

  /* ── Animation state ───────────────────────────────────── */
  const [playing, setPlaying] = useState(false);
  const [animSpeed, setAnimSpeed] = useState(1);
  const animRef = useRef(null);
  const diffWorkerRef = useRef(null);
  const diffRequestIdRef = useRef(0);

  /* ── Client-side frame cache (avoids re-fetching viewed frames) ── */
  const frameCacheRef = useRef(new Map());
  const prefetchInFlightRef = useRef(new Set());
  const FRAME_CACHE_MAX = 200;
  const frameCacheKey = (m, p, h, r) => `${m}:${p}:${h}:${r}`;

  const frameCacheGet = useCallback((key) => {
    const cache = frameCacheRef.current;
    if (!cache.has(key)) return null;
    const value = cache.get(key);
    /* Access updates insertion order so the map behaves as true LRU. */
    cache.delete(key);
    cache.set(key, value);
    return value;
  }, []);

  const frameCacheSet = useCallback((key, data) => {
    const cache = frameCacheRef.current;
    if (cache.has(key)) cache.delete(key);
    cache.set(key, data);
    while (cache.size > FRAME_CACHE_MAX) {
      const oldest = cache.keys().next().value;
      cache.delete(oldest);
    }
  }, [FRAME_CACHE_MAX]);

  /* ── Color scale cache (per cmap name) ── */
  const colorScaleCacheRef = useRef({});

  useEffect(() => {
    const worker = new GridWorker();
    diffWorkerRef.current = worker;
    return () => {
      worker.terminate();
      diffWorkerRef.current = null;
    };
  }, []);

  /* Update model constraints when model changes */
  useEffect(() => {
    const info = models[selectedModel];
    if (info) {
      setMaxFhour(info.maxHour || 384);
      setFhourStep(info.step || 3);
      setFhour((current) => (current > (info.maxHour || 384) ? 0 : current));
    }
  }, [selectedModel, models, fhour]);

  /* Re-fetch parameters filtered for this model */
  useEffect(() => {
    fetchParameters(selectedModel)
      .then((p) => {
        setParameterCategories(p);
        /* If selected param is not in the new set, reset to first available */
        const allParams = Object.values(p).flatMap((c) => Object.keys(c.params || {}));
        setSelectedParam((current) => (
          allParams.length && !allParams.includes(current) ? allParams[0] : current
        ));
      })
      .catch(() => {}); // silently keep previous categories on error
  }, [selectedModel]);

  /* Update bbox when region preset changes */
  useEffect(() => {
    if (REGIONS[region]) setBbox(REGIONS[region]);
  }, [region, REGIONS]);

  /* Clear frame cache when model, param, or region changes */
  useEffect(() => {
    frameCacheRef.current.clear();
    prefetchInFlightRef.current.clear();
  }, [selectedModel, selectedParam, region]);

  /* ── Fetch grid forecast (with client-side frame cache) ── */
  const loadForecast = useCallback(async (hour) => {
    const h = hour ?? fhour;
    const key = frameCacheKey(selectedModel, selectedParam, h, region);
    const requestId = ++gridRequestIdRef.current;

    /* Hit: skip network + loading state entirely */
    const cached = frameCacheGet(key);
    if (cached) {
      if (requestId !== gridRequestIdRef.current) return;
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
      if (requestId !== gridRequestIdRef.current) return;
      if (csStops) data.color_scale = csStops;

      setGridData(data);
      frameCacheSet(key, data);
    } catch (err) {
      if (requestId !== gridRequestIdRef.current) return;
      setGridError(err.message);
    } finally {
      if (requestId === gridRequestIdRef.current) setGridLoading(false);
    }
  }, [selectedModel, selectedParam, fhour, bbox, region, parameterCategories, frameCacheGet, frameCacheSet]);

  const prefetchForecast = useCallback(async (hour) => {
    const h = hour ?? fhour;
    const key = frameCacheKey(selectedModel, selectedParam, h, region);
    if (frameCacheRef.current.has(key) || prefetchInFlightRef.current.has(key)) {
      return;
    }

    prefetchInFlightRef.current.add(key);
    try {
      const data = await fetchForecast({
        model: selectedModel,
        parameter: selectedParam,
        fhour: h,
        bbox,
      });

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
      frameCacheSet(key, data);
    } catch {
      /* Prefetch is best-effort and should never surface UI errors. */
    } finally {
      prefetchInFlightRef.current.delete(key);
    }
  }, [selectedModel, selectedParam, fhour, bbox, region, parameterCategories, frameCacheSet]);

  /* Load forecast when key state changes */
  useEffect(() => {
    if (!initLoading && !initError) loadForecast();
    return () => { gridRequestIdRef.current += 1; };
  }, [loadForecast, initLoading, initError]);

  useEffect(() => {
    if (!playing || initLoading || initError) return;
    const nextHour = fhour + fhourStep > maxFhour ? 0 : fhour + fhourStep;
    void prefetchForecast(nextHour);
  }, [playing, fhour, fhourStep, maxFhour, prefetchForecast, initLoading, initError]);

  /* ── Comparison model fetch ────────────────────────────── */
  useEffect(() => {
    if (!compareMode || !compareModel) { setCompareGridData(null); return; }
    const requestId = ++compareRequestIdRef.current;
    setCompareGridLoading(true);
    (async () => {
      try {
        const data = await fetchForecast({
          model: compareModel,
          parameter: selectedParam,
          fhour,
          bbox,
        });
        /* Attach same color scale */
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
        if (requestId === compareRequestIdRef.current) setCompareGridData(data);
      } catch {
        if (requestId === compareRequestIdRef.current) setCompareGridData(null);
      } finally {
        if (requestId === compareRequestIdRef.current) setCompareGridLoading(false);
      }
    })();
    return () => { compareRequestIdRef.current += 1; };
  }, [compareMode, compareModel, selectedParam, fhour, bbox, parameterCategories]);

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

  /* ── Compute display time labels (shared formatter) ────── */
  const validTimeLabel = validZuluLabel(fhour, gridData?.valid_time, gridData?.run);
  const compareValidTimeLabel = compareGridData
    ? validZuluLabel(fhour, compareGridData?.valid_time, compareGridData?.run)
    : validTimeLabel;

  /* ── Difference mode: compute delta from previous frame (worker-first) ── */
  const [displayGridData, setDisplayGridData] = useState(null);
  useEffect(() => {
    if (!diffMode || !gridData) {
      setDisplayGridData(gridData);
      return;
    }

    const prevHour = Math.max(0, fhour - fhourStep);
    if (prevHour === fhour) {
      setDisplayGridData(gridData);
      return;
    }

    const prevKey = frameCacheKey(selectedModel, selectedParam, prevHour, region);
    const prevData = frameCacheGet(prevKey);
    if (!prevData || !Array.isArray(prevData.values)) {
      setDisplayGridData(gridData);
      return;
    }

    const vals = gridData.values;
    const prevVals = prevData.values;
    const requestId = ++diffRequestIdRef.current;

    const commitSyncFallback = () => {
      if (requestId !== diffRequestIdRef.current) return;
      const diffValues = computeDiffValuesSync(vals, prevVals);
      if (!diffValues) {
        setDisplayGridData(gridData);
        return;
      }
      setDisplayGridData({ ...gridData, values: diffValues, color_scale: DIFF_SCALE });
    };

    const worker = diffWorkerRef.current;
    if (!worker) {
      commitSyncFallback();
      return;
    }

    const onMessage = (event) => {
      const payload = event.data || {};
      if (payload.id !== requestId) return;
      worker.removeEventListener("message", onMessage);
      worker.removeEventListener("error", onError);
      if (!Array.isArray(payload.diffValues)) {
        commitSyncFallback();
        return;
      }
      setDisplayGridData({ ...gridData, values: payload.diffValues, color_scale: DIFF_SCALE });
    };

    const onError = () => {
      worker.removeEventListener("message", onMessage);
      worker.removeEventListener("error", onError);
      commitSyncFallback();
    };

    worker.addEventListener("message", onMessage);
    worker.addEventListener("error", onError);
    worker.postMessage({ id: requestId, values: vals, prevValues: prevVals });

    return () => {
      worker.removeEventListener("message", onMessage);
      worker.removeEventListener("error", onError);
    };
  }, [diffMode, gridData, fhour, fhourStep, selectedModel, selectedParam, region, frameCacheGet]);

  const mapGridData = diffMode ? (displayGridData || gridData) : gridData;

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
        onSaveRegion={handleSaveRegion}
        onDeleteRegion={handleDeleteRegion}
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
          showContours={showContours}
          setShowContours={setShowContours}
          showWindParticles={showWindParticles}
          setShowWindParticles={setShowWindParticles}
          useWebGL={useWebGL}
          setUseWebGL={setUseWebGL}
          diffMode={diffMode}
          setDiffMode={setDiffMode}
          onExport={handleExport}
          compareMode={compareMode}
          setCompareMode={setCompareMode}
          compareModel={compareModel}
          setCompareModel={setCompareModel}
          models={models}
          onCrossSection={() => { setActivePanel("cross"); setCrossDrawMode(true); crossLineRef.current = []; }}
          showSounding={activePanel === "sounding"}
          setShowSounding={setShowSounding}
          showMeteogram={activePanel === "meteogram"}
          setShowMeteogram={setShowMeteogram}
          showEnsemble={activePanel === "ensemble"}
          setShowEnsemble={setShowEnsemble}

        />
        <div className={`map-container${compareMode ? " map-compare" : ""}`}>
          <ForecastMap
            gridData={mapGridData}
            loading={gridLoading}
            error={gridError}
            bbox={bbox}
            parameter={selectedParam}
            overlayOpacity={overlayOpacity}
            validTime={validTimeLabel}
            model={selectedModel}
            onMapReady={(m) => { mapInstanceRef.current = m; }}
            onMapClick={handleMapClick}
            showContours={showContours}
            showWindParticles={showWindParticles}
            useWebGL={useWebGL}
          />
          {compareMode && (
            <ForecastMap
              gridData={compareGridData}
              loading={compareGridLoading}
              error={null}
              bbox={bbox}
              parameter={selectedParam}
              overlayOpacity={overlayOpacity}
              validTime={compareValidTimeLabel}
              model={compareModel}
              showContours={showContours}
              showWindParticles={showWindParticles}
              useWebGL={useWebGL}
            />
          )}
          <ColorBar parameter={selectedParam} parameterCategories={parameterCategories} />
          {activePanel === "meteogram" && (
            <Meteogram
              data={meteogramData}
              loading={meteogramLoading}
              point={meteogramPoint}
              model={selectedModel}
              onClose={() => setActivePanel(null)}
            />
          )}
          {activePanel === "sounding" && (
            <SoundingProfile
              plot={soundingPlot}
              loading={soundingLoading}
              point={meteogramPoint}
              model={selectedModel}
              fhour={fhour}
              onClose={() => setActivePanel(null)}
            />
          )}
          {activePanel === "cross" && (
            <CrossSection
              data={crossData}
              loading={crossLoading}
              progress={crossProgress}
              model={selectedModel}
              onClose={() => { setActivePanel(null); setCrossDrawMode(false); crossLineRef.current = []; }}
              onStartDraw={() => { setCrossDrawMode(true); crossLineRef.current = []; }}
            />
          )}
          {activePanel === "ensemble" && (
            <EnsemblePlume
              data={ensembleData}
              loading={ensembleLoading}
              point={meteogramPoint}
              variable={selectedParam}
              onClose={() => setActivePanel(null)}
            />
          )}

          <AnimationControls
            fhour={fhour}
            setFhour={setFhour}
            maxFhour={maxFhour}
            step={fhourStep}
            playing={playing}
            setPlaying={setPlaying}
            speed={animSpeed}
            setSpeed={setAnimSpeed}
            run={gridData?.run}
            validTime={gridData?.valid_time}
            loading={gridLoading}
          />
        </div>
      </div>
    </div>
  );
}
