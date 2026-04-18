import { useEffect, useRef, useState, useCallback } from "react";
import L from "leaflet";
import CanvasOverlay from "./CanvasOverlay";
import WindParticleLayer from "./WindParticleLayer";
import DeckGLOverlay from "./DeckGLOverlay";
import "./ForecastMap.css";

const DARK_TILES = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png";
const LIGHT_TILES = "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png";
const TILE_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>';

function findNearestIndex(sortedValues, target) {
  if (!sortedValues || sortedValues.length === 0) return -1;
  let lo = 0;
  let hi = sortedValues.length - 1;

  while (lo < hi) {
    const mid = Math.floor((lo + hi) / 2);
    if (sortedValues[mid] < target) {
      lo = mid + 1;
    } else {
      hi = mid;
    }
  }

  if (lo === 0) return 0;
  const prev = lo - 1;
  return Math.abs(sortedValues[lo] - target) < Math.abs(sortedValues[prev] - target)
    ? lo
    : prev;
}

export default function ForecastMap({ gridData, loading, error, bbox, parameter, overlayOpacity, onMapReady, onMapClick, showContours, showWindParticles, useWebGL }) {
  const mapContainerRef = useRef(null);
  const mapRef = useRef(null);
  const onMapReadyRef = useRef(onMapReady);
  const tileRef = useRef(null);
  const canvasRef = useRef(null);
  const hoverFrameRef = useRef(null);
  const hoverEventRef = useRef(null);
  const cursorStateRef = useRef({ value: null, x: null, y: null });
  const [cursorValue, setCursorValue] = useState(null);
  const [cursorPos, setCursorPos] = useState(null);

  useEffect(() => {
    onMapReadyRef.current = onMapReady;
  }, [onMapReady]);

  /* Initialize Leaflet map */
  useEffect(() => {
    if (mapRef.current || !mapContainerRef.current || !L) return;
    const map = L.map(mapContainerRef.current, {
      center: [39, -98],
      zoom: 4,
      minZoom: -2,
      maxZoom: 20,
      zoomSnap: 0.5,
      zoomDelta: 0.5,
      zoomControl: false,
      attributionControl: false,
    });
    L.control.zoom({ position: "topright" }).addTo(map);

    const isDark = document.documentElement.getAttribute("data-theme") !== "light";
    tileRef.current = L.tileLayer(isDark ? DARK_TILES : LIGHT_TILES, {
      attribution: TILE_ATTR,
      minNativeZoom: 0,
      maxNativeZoom: 18,
      maxZoom: 18,
    }).addTo(map);

    mapRef.current = map;
    if (onMapReadyRef.current) onMapReadyRef.current(map);

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
    hoverEventRef.current = e;
    if (hoverFrameRef.current !== null) return;

    hoverFrameRef.current = window.requestAnimationFrame(() => {
      hoverFrameRef.current = null;
      const evt = hoverEventRef.current;
      if (!evt) return;

      const { lat, lng } = evt.latlng;
      const lats = gridData.lats;
      const lons = gridData.lons;
      const vals = gridData.values;
      const is2D = Array.isArray(vals[0]);

      let val = null;
      if (is2D) {
        const rowIdx = findNearestIndex(lats, lat);
        const colIdx = findNearestIndex(lons, lng);
        val = vals[rowIdx]?.[colIdx];
      } else {
        let minDist = Infinity;
        for (let i = 0; i < lats.length; i++) {
          const d = Math.abs(lats[i] - lat) + Math.abs(lons[i] - lng);
          if (d < minDist) {
            minDist = d;
            val = vals[i];
          }
        }
      }

      const nextValue = val != null && !isNaN(val) ? val.toFixed(1) : null;
      const nextX = evt.containerPoint.x;
      const nextY = evt.containerPoint.y;
      const prev = cursorStateRef.current;
      if (prev.value === nextValue && prev.x === nextX && prev.y === nextY) return;

      cursorStateRef.current = { value: nextValue, x: nextX, y: nextY };
      setCursorValue(nextValue);
      setCursorPos({ x: nextX, y: nextY });
    });
  }, [gridData]);

  useEffect(() => {
    if (!mapRef.current) return;
    const handleMouseOut = () => {
      hoverEventRef.current = null;
      if (hoverFrameRef.current !== null) {
        window.cancelAnimationFrame(hoverFrameRef.current);
        hoverFrameRef.current = null;
      }
      cursorStateRef.current = { value: null, x: null, y: null };
      setCursorValue(null);
    };
    mapRef.current.on("mousemove", handleMouseMove);
    mapRef.current.on("mouseout", handleMouseOut);
    return () => {
      mapRef.current?.off("mousemove", handleMouseMove);
      mapRef.current?.off("mouseout", handleMouseOut);
      if (hoverFrameRef.current !== null) {
        window.cancelAnimationFrame(hoverFrameRef.current);
        hoverFrameRef.current = null;
      }
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
      {mapRef.current && gridData && !useWebGL && (
        <CanvasOverlay
          map={mapRef.current}
          gridData={gridData}
          parameter={parameter}
          opacity={overlayOpacity}
          showContours={showContours}
          ref={canvasRef}
        />
      )}
      {mapRef.current && gridData && useWebGL && (
        <DeckGLOverlay
          map={mapRef.current}
          gridData={gridData}
          colorScale={gridData.color_scale}
          opacity={overlayOpacity}
          visible={true}
        />
      )}
      {mapRef.current && gridData?.u_component && gridData?.v_component && (
        <WindParticleLayer
          map={mapRef.current}
          gridData={gridData}
          visible={showWindParticles !== false}
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
      <div className="map-watermark">modelforecast.app</div>
    </div>
  );
}
