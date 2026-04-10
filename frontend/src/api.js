const API_BASE = import.meta.env.VITE_API_URL || "";

function fetchWithTimeout(url, options = {}, timeoutMs = 15000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  return fetch(url, { ...options, signal: controller.signal })
    .catch((err) => {
      if (err.name === "AbortError") throw new Error("Request timed out");
      throw err;
    })
    .finally(() => clearTimeout(timer));
}

async function withRetry(fn, retries = 2, delayMs = 1500) {
  for (let i = 0; i <= retries; i++) {
    try {
      return await fn();
    } catch (err) {
      if (err.rateLimited || i === retries) throw err;
      await new Promise((r) => setTimeout(r, delayMs));
    }
  }
}

/* Health check */
export async function fetchHealth() {
  const res = await fetchWithTimeout(`${API_BASE}/api/health`);
  if (!res.ok) throw new Error("Health check failed");
  return res.json();
}

/* Model list and parameters */
export async function fetchModels() {
  return withRetry(async () => {
    const res = await fetchWithTimeout(`${API_BASE}/api/models`);
    if (!res.ok) throw new Error("Failed to fetch models");
    return res.json();
  });
}

export async function fetchParameters(model) {
  const qs = model ? `?model=${encodeURIComponent(model)}` : "";
  return withRetry(async () => {
    const res = await fetchWithTimeout(`${API_BASE}/api/parameters${qs}`);
    if (!res.ok) throw new Error("Failed to fetch parameters");
    return res.json();
  });
}

/* Gridded forecast data */
export async function fetchForecast({ model, parameter, fhour, bbox }) {
  const params = new URLSearchParams({
    model,
    variable: parameter,
    fhour: String(fhour),
  });
  if (bbox) {
    params.set("lat_max", String(bbox.north));
    params.set("lat_min", String(bbox.south));
    params.set("lon_max", String(bbox.east));
    params.set("lon_min", String(bbox.west));
  }
  const res = await fetchWithTimeout(
    `${API_BASE}/api/forecast?${params}`,
    {},
    30000
  );
  const data = await res.json();
  if (res.status === 429) {
    const err = new Error(data.error || "Rate limited");
    err.rateLimited = true;
    throw err;
  }
  if (!res.ok) throw new Error(data.error || "Forecast fetch failed");
  return data;
}

/* Color scale for a parameter */
export async function fetchColorScale(cmapName) {
  const res = await fetchWithTimeout(
    `${API_BASE}/api/color-scale?cmap=${encodeURIComponent(cmapName)}`
  );
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Color scale fetch failed");
  return data;
}

/* Point time-series for meteogram */
export async function fetchMeteogram({ model, lat, lon }) {
  const params = new URLSearchParams({ model, lat: String(lat), lon: String(lon) });
  const res = await fetchWithTimeout(`${API_BASE}/api/meteogram?${params}`, {}, 20000);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Meteogram fetch failed");
  return data;
}

/* Vertical sounding profile at a point */
export async function fetchSounding({ model, lat, lon, fhour }) {
  const params = new URLSearchParams({ model, lat: String(lat), lon: String(lon), fhour: String(fhour || 0) });
  const res = await fetchWithTimeout(`${API_BASE}/api/sounding?${params}`, {}, 25000);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Sounding fetch failed");
  return data;
}

/* Full sounding plot from Sounding Analysis project */
export async function fetchSoundingPlot({ model, lat, lon, fhour, theme, colorblind }) {
  const params = new URLSearchParams({
    model, lat: String(lat), lon: String(lon), fhour: String(fhour || 0),
    theme: theme || "dark", colorblind: String(!!colorblind),
  });
  const res = await fetchWithTimeout(`${API_BASE}/api/sounding-plot?${params}`, {}, 65000);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Sounding plot fetch failed");
  return data;
}

/* Cross-section along a line */
export async function fetchCrossSection({ model, variable, fhour, lat1, lon1, lat2, lon2 }) {
  const params = new URLSearchParams({
    model, variable, fhour: String(fhour || 0),
    lat1: String(lat1), lon1: String(lon1),
    lat2: String(lat2), lon2: String(lon2),
  });
  const res = await fetchWithTimeout(`${API_BASE}/api/cross-section?${params}`, {}, 30000);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Cross-section fetch failed");
  return data;
}

/* Ensemble plume data at a point */
export async function fetchEnsemble({ variable, lat, lon }) {
  const params = new URLSearchParams({ variable, lat: String(lat), lon: String(lon) });
  const res = await fetchWithTimeout(`${API_BASE}/api/ensemble?${params}`, {}, 25000);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Ensemble fetch failed");
  return data;
}
