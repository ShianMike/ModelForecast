/**
 * WindParticleLayer — animated wind particle overlay using leaflet-velocity.
 *
 * Renders u/v wind component data as animated particles on the Leaflet map.
 * Expects gridData with u_component and v_component arrays.
 */
import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet-velocity";

/**
 * Convert the API grid format to the GeoJSON-like structure leaflet-velocity expects.
 * leaflet-velocity needs two "header + data" objects — one for U, one for V —
 * following the earth/wind-js JSON convention.
 */
function buildVelocityData(gridData) {
  if (
    !gridData?.u_component?.length ||
    !gridData?.v_component?.length ||
    !gridData?.lats?.length ||
    !gridData?.lons?.length
  ) {
    return null;
  }

  const lats = gridData.lats;
  const lons = gridData.lons;
  const ny = lats.length;
  const nx = lons.length;
  const la1 = lats[0];
  const la2 = lats[ny - 1];
  const lo1 = lons[0];
  const lo2 = lons[nx - 1];
  const dy = ny > 1 ? Math.abs(lats[1] - lats[0]) : 1;
  const dx = nx > 1 ? Math.abs(lons[1] - lons[0]) : 1;

  /* Flatten 2D arrays → 1D (top-to-bottom, left-to-right). leaflet-velocity
     wants north→south ordering, so reverse rows if lats are ascending. */
  const ascending = la1 < la2;
  const flatU = [];
  const flatV = [];
  for (let j = 0; j < ny; j++) {
    const row = ascending ? ny - 1 - j : j;
    for (let i = 0; i < nx; i++) {
      const u = gridData.u_component[row]?.[i];
      const v = gridData.v_component[row]?.[i];
      flatU.push(u ?? 0);
      flatV.push(v ?? 0);
    }
  }

  const header = {
    parameterCategory: 2,
    parameterNumber: 2,
    lo1: lo1,
    lo2: lo2,
    la1: ascending ? la2 : la1,
    la2: ascending ? la1 : la2,
    dx: dx,
    dy: dy,
    nx: nx,
    ny: ny,
  };

  return [
    { header: { ...header, parameterNumber: 2 }, data: flatU },
    { header: { ...header, parameterNumber: 3 }, data: flatV },
  ];
}

export default function WindParticleLayer({ map, gridData, visible }) {
  const layerRef = useRef(null);
  const rafRef = useRef(null);

  useEffect(() => {
    /* Cleanup helper — removes layer + cancels any pending RAF */
    const cleanup = () => {
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      if (layerRef.current) {
        try { map?.removeLayer(layerRef.current); } catch { /* already removed */ }
        layerRef.current = null;
      }
    };

    if (!map || !visible) {
      cleanup();
      return;
    }

    const velocityData = buildVelocityData(gridData);
    if (!velocityData) {
      cleanup();
      return;
    }

    /* Remove previous layer before adding a new one */
    cleanup();

    /* Defer layer creation to the next animation frame so the Leaflet map
       container is fully laid out and React's commit phase has finished.
       leaflet-velocity internally uses setTimeout in onAdd — if we add the
       layer synchronously during a React effect, the cleanup for a *previous*
       effect can fire between addTo and that setTimeout, nullifying _map. */
    let cancelled = false;
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null;
      if (cancelled) return;
      if (!map.getContainer() || !map.getSize()) return;

      const layer = L.velocityLayer({
        displayValues: false,
        data: velocityData,
        maxVelocity: 40,
        velocityScale: 0.008,
        particleAge: 60,
        lineWidth: 1.2,
        particleMultiplier: 1 / 500,
        colorScale: [
          "rgba(36,104,180,0.7)",
          "rgba(60,157,194,0.7)",
          "rgba(128,205,193,0.7)",
          "rgba(151,218,168,0.7)",
          "rgba(198,231,181,0.7)",
          "rgba(238,247,217,0.7)",
          "rgba(255,238,159,0.7)",
          "rgba(252,217,125,0.7)",
          "rgba(255,182,100,0.7)",
          "rgba(252,150,75,0.7)",
          "rgba(250,112,52,0.7)",
          "rgba(245,64,32,0.7)",
          "rgba(237,45,28,0.7)",
          "rgba(220,24,32,0.7)",
          "rgba(180,0,35,0.7)",
        ],
      });

      /* Monkey-patch the two methods that crash when _map is null.
         leaflet-velocity fires these from setTimeout / rAF after onAdd,
         and React cleanup can null _map in between. */
      const origMove = layer._onLayerDidMove;
      layer._onLayerDidMove = function () {
        if (!this._map) return;
        return origMove.apply(this, arguments);
      };
      const origDraw = layer.drawLayer;
      layer.drawLayer = function () {
        if (!this._map) return;
        return origDraw.apply(this, arguments);
      };

      try {
        layer.addTo(map);
      } catch {
        return;
      }
      if (cancelled) {
        try { map.removeLayer(layer); } catch { /* noop */ }
        return;
      }
      layerRef.current = layer;
    });

    return () => {
      cancelled = true;
      cleanup();
    };
  }, [map, gridData, visible]);

  return null;
}
