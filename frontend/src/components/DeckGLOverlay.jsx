/**
 * DeckGLOverlay — WebGL-accelerated grid rendering via Deck.gl.
 *
 * Renders the forecast grid as a SolidPolygonLayer of coloured quads,
 * running entirely on the GPU.  Sits as a Leaflet overlay pane so it
 * integrates seamlessly with the existing map.
 */
import { useEffect, useRef, useCallback } from "react";
import { Deck } from "@deck.gl/core";
import { SolidPolygonLayer } from "@deck.gl/layers";

/* Interpolate a value onto a colour scale → [r, g, b, a] (0–255).
 * Stops are [value, r, g, b, a] arrays (same format as backend). */
function valueToColor(val, colorScale, opacity255) {
  if (val == null || isNaN(val) || !colorScale?.length) return [0, 0, 0, 0];

  const stops = colorScale;
  if (val <= stops[0][0]) return [stops[0][1], stops[0][2], stops[0][3], opacity255];
  if (val >= stops[stops.length - 1][0]) {
    const s = stops[stops.length - 1];
    return [s[1], s[2], s[3], opacity255];
  }

  for (let i = 1; i < stops.length; i++) {
    if (val <= stops[i][0]) {
      const lo = stops[i - 1];
      const hi = stops[i];
      const t = (val - lo[0]) / (hi[0] - lo[0]);
      return [
        Math.round(lo[1] + t * (hi[1] - lo[1])),
        Math.round(lo[2] + t * (hi[2] - lo[2])),
        Math.round(lo[3] + t * (hi[3] - lo[3])),
        opacity255,
      ];
    }
  }
  return [0, 0, 0, 0];
}

/**
 * Build quad polygons from a regular lat/lon grid.
 * Each cell becomes a tiny polygon with its colour derived from the value.
 */
function buildPolygons(gridData, colorScale, opacity) {
  const { lats, lons, values } = gridData;
  if (!lats?.length || !lons?.length || !values?.length) return [];

  const ny = lats.length;
  const nx = lons.length;
  const opacity255 = Math.round((opacity ?? 0.5) * 255);
  const polys = [];

  for (let j = 0; j < ny - 1; j++) {
    for (let i = 0; i < nx - 1; i++) {
      const val = values[j]?.[i];
      if (val == null) continue;
      const color = valueToColor(val, colorScale, opacity255);
      if (color[3] === 0) continue;

      const lat0 = lats[j];
      const lat1 = lats[j + 1];
      const lon0 = lons[i];
      const lon1 = lons[i + 1];

      polys.push({
        polygon: [
          [lon0, lat0],
          [lon1, lat0],
          [lon1, lat1],
          [lon0, lat1],
        ],
        color,
      });
    }
  }
  return polys;
}

export default function DeckGLOverlay({ map, gridData, colorScale, opacity, visible }) {
  const deckRef = useRef(null);
  const containerRef = useRef(null);

  /* Sync Deck.gl viewState with Leaflet map on every move.
   * Also counter-translate the container so Leaflet's pane transforms
   * don't double-shift the Deck.gl canvas. */
  const syncViewState = useCallback(() => {
    if (!map || !deckRef.current) return;
    const center = map.getCenter();
    const zoom = map.getZoom();
    const size = map.getSize();

    /* Leaflet applies translate3d to the mapPane during panning.
     * We need to negate that offset so Deck.gl stays aligned. */
    const mapPane = map.getPane("mapPane");
    if (mapPane && containerRef.current) {
      const transform = mapPane.style.transform || "";
      const match = transform.match(/translate3d\(([^,]+),\s*([^,]+)/);
      if (match) {
        const tx = -parseFloat(match[1]);
        const ty = -parseFloat(match[2]);
        containerRef.current.style.transform = `translate(${tx}px, ${ty}px)`;
      }
    }

    deckRef.current.setProps({
      viewState: {
        longitude: center.lng,
        latitude: center.lat,
        zoom: zoom - 1, /* Leaflet zoom → Deck.gl zoom offset */
        pitch: 0,
        bearing: 0,
      },
      width: size.x,
      height: size.y,
    });
  }, [map]);

  /* Create / destroy the Deck instance */
  useEffect(() => {
    if (!map || !visible) {
      if (deckRef.current) {
        deckRef.current.finalize();
        deckRef.current = null;
      }
      if (containerRef.current) {
        containerRef.current.remove();
        containerRef.current = null;
      }
      return;
    }

    /* Create a canvas container inside Leaflet's overlayPane */
    const pane = map.getPane("overlayPane");
    if (!pane) return;

    const container = document.createElement("div");
    container.style.cssText = "position:absolute;top:0;left:0;pointer-events:none;z-index:400;";
    pane.appendChild(container);
    containerRef.current = container;

    const size = map.getSize();
    const center = map.getCenter();
    const zoom = map.getZoom();

    const deck = new Deck({
      parent: container,
      viewState: {
        longitude: center.lng,
        latitude: center.lat,
        zoom: zoom - 1,
        pitch: 0,
        bearing: 0,
      },
      width: size.x,
      height: size.y,
      controller: false,
      layers: [],
      style: { "mix-blend-mode": "normal" },
    });
    deckRef.current = deck;

    map.on("move", syncViewState);
    map.on("zoom", syncViewState);
    map.on("resize", syncViewState);
    syncViewState(); /* initial alignment */

    return () => {
      map.off("move", syncViewState);
      map.off("zoom", syncViewState);
      map.off("resize", syncViewState);
      if (deckRef.current) {
        deckRef.current.finalize();
        deckRef.current = null;
      }
      if (containerRef.current) {
        containerRef.current.remove();
        containerRef.current = null;
      }
    };
  }, [map, visible, syncViewState]);

  /* Update layers whenever grid data or colour scale changes */
  useEffect(() => {
    if (!deckRef.current || !visible || !gridData || !colorScale) return;

    const polys = buildPolygons(gridData, colorScale, opacity);

    deckRef.current.setProps({
      layers: [
        new SolidPolygonLayer({
          id: "forecast-grid",
          data: polys,
          getPolygon: (d) => d.polygon,
          getFillColor: (d) => d.color,
          pickable: false,
          filled: true,
          extruded: false,
        }),
      ],
    });
  }, [gridData, colorScale, opacity, visible]);

  return null;
}
