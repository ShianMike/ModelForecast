import { useEffect, useRef, useState, useCallback } from "react";
import { MapPin } from "lucide-react";
import CanvasOverlay from "./CanvasOverlay";
import "./ForecastMap.css";

/* Leaflet loaded from CDN in index.html */
const L = window.L;

const DARK_TILES = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png";
const LIGHT_TILES = "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png";
const TILE_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>';

export default function ForecastMap({ gridData, loading, error, bbox, parameter, overlayOpacity, validTime, model, onMapReady, onMapClick, showContours }) {
  const mapContainerRef = useRef(null);
  const mapRef = useRef(null);
  const tileRef = useRef(null);
  const canvasRef = useRef(null);
  const [cursorValue, setCursorValue] = useState(null);
  const [cursorPos, setCursorPos] = useState(null);

  /* Initialize Leaflet map */
  useEffect(() => {
    if (mapRef.current || !mapContainerRef.current || !L) return;
    const map = L.map(mapContainerRef.current, {
      center: [39, -98],
      zoom: 4,
      zoomControl: false,
      attributionControl: false,
    });
    L.control.zoom({ position: "topright" }).addTo(map);

    const isDark = document.documentElement.getAttribute("data-theme") !== "light";
    tileRef.current = L.tileLayer(isDark ? DARK_TILES : LIGHT_TILES, {
      attribution: TILE_ATTR,
      maxZoom: 18,
    }).addTo(map);

    mapRef.current = map;
    if (onMapReady) onMapReady(map);

    return () => { map.remove(); mapRef.current = null; };
  }, []);

  /* Update tiles on theme change */
  useEffect(() => {
    const obs = new MutationObserver(() => {
      if (!mapRef.current || !tileRef.current) return;
      const isDark = document.documentElement.getAttribute("data-theme") !== "light";
      tileRef.current.setUrl(isDark ? DARK_TILES : LIGHT_TILES);
    });
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);

  /* Fit map to bbox */
  useEffect(() => {
    if (!mapRef.current || !bbox) return;
    mapRef.current.fitBounds([
      [bbox.south, bbox.west],
      [bbox.north, bbox.east],
    ]);
  }, [bbox]);

  /* Cursor value readout */
  const handleMouseMove = useCallback((e) => {
    if (!gridData || !gridData.lats || !gridData.values) {
      setCursorValue(null);
      return;
    }
    const { lat, lng } = e.latlng;
    const lats = gridData.lats;
    const lons = gridData.lons;
    const vals = gridData.values;
    const is2D = Array.isArray(vals[0]);

    let minDist = Infinity, val = null;
    if (is2D) {
      /* 2D grid: find nearest lat index, then nearest lon index */
      let bi = 0, bj = 0;
      let minLatD = Infinity, minLonD = Infinity;
      for (let i = 0; i < lats.length; i++) {
        const d = Math.abs(lats[i] - lat);
        if (d < minLatD) { minLatD = d; bi = i; }
      }
      for (let j = 0; j < lons.length; j++) {
        const d = Math.abs(lons[j] - lng);
        if (d < minLonD) { minLonD = d; bj = j; }
      }
      val = vals[bi]?.[bj];
    } else {
      for (let i = 0; i < lats.length; i++) {
        const d = Math.abs(lats[i] - lat) + Math.abs(lons[i] - lng);
        if (d < minDist) { minDist = d; val = vals[i]; }
      }
    }
    setCursorValue(val != null && !isNaN(val) ? val.toFixed(1) : null);
    setCursorPos({ x: e.containerPoint.x, y: e.containerPoint.y });
  }, [gridData]);

  useEffect(() => {
    if (!mapRef.current) return;
    mapRef.current.on("mousemove", handleMouseMove);
    mapRef.current.on("mouseout", () => setCursorValue(null));
    return () => {
      mapRef.current?.off("mousemove", handleMouseMove);
      mapRef.current?.off("mouseout");
    };
  }, [handleMouseMove]);

  /* Map click → meteogram */
  useEffect(() => {
    if (!mapRef.current || !onMapClick) return;
    const handler = (e) => onMapClick(e.latlng);
    mapRef.current.on("click", handler);
    return () => mapRef.current?.off("click", handler);
  }, [onMapClick]);

  return (
    <div className="forecast-map">
      <div ref={mapContainerRef} className="forecast-map-inner" />
      {mapRef.current && gridData && (
        <CanvasOverlay
          map={mapRef.current}
          gridData={gridData}
          parameter={parameter}
          opacity={overlayOpacity}
          showContours={showContours}
          ref={canvasRef}
        />
      )}
      {loading && (
        <div className="map-loading">
          <div className="spinner" />
          <span>Fetching forecast data…</span>
        </div>
      )}
      {error && (
        <div className="map-error">{error}</div>
      )}
      {cursorValue !== null && cursorPos && (
        <div
          className="cursor-readout"
          style={{ left: cursorPos.x + 16, top: cursorPos.y - 10 }}
        >
          {cursorValue}
        </div>
      )}
    </div>
  );
}
