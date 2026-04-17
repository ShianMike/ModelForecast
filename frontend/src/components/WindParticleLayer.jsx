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

  useEffect(() => {
    if (!map || !visible) {
      if (layerRef.current) {
        map?.removeLayer(layerRef.current);
        layerRef.current = null;
      }
      return;
    }

    const velocityData = buildVelocityData(gridData);
    if (!velocityData) {
      if (layerRef.current) {
        map.removeLayer(layerRef.current);
        layerRef.current = null;
      }
      return;
    }

    /* Remove previous layer before adding a new one */
    if (layerRef.current) {
      map.removeLayer(layerRef.current);
      layerRef.current = null;
    }

    /* Defer layer creation until the map container is fully initialised.
       leaflet-velocity calls _map.getSize() and containerPointToLayerPoint()
       synchronously inside onAdd, which blows up if the container element
       hasn't been laid out yet (returns null). */
    if (!map.getContainer() || !map.getSize()) return;

    const layer = L.velocityLayer({
      displayValues: false,
      data: velocityData,
      maxVelocity: 40,
      velocityScale: 0.008,
      particleAge: 60,
      lineWidth: 1.2,
      particleMultiplier: 1 / 200,
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

    try {
      layer.addTo(map);
    } catch {
      /* leaflet-velocity can still race if the canvas isn't painted yet;
         silently bail — the next effect cycle will retry. */
      return;
    }
    layerRef.current = layer;

    return () => {
      if (layerRef.current) {
        map.removeLayer(layerRef.current);
        layerRef.current = null;
      }
    };
  }, [map, gridData, visible]);

  return null;
}
