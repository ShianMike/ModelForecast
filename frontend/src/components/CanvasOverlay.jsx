import { useEffect, useRef, forwardRef, useImperativeHandle } from "react";

/* Default color stops — array format: [value, r, g, b, a] matching backend */
const DEFAULT_STOPS = [
  [-40, 148, 103, 189, 255],
  [-20, 31,  119, 180, 255],
  [0,   44,  160, 44,  255],
  [20,  255, 215, 0,   255],
  [40,  255, 127, 14,  255],
  [60,  214, 39,  40,  255],
  [80,  227, 119, 194, 255],
  [100, 200, 200, 200, 255],
  [120, 255, 255, 255, 255],
];

/* stops are [value, r, g, b, a] arrays */
function interpolateColor(value, stops) {
  if (value <= stops[0][0]) return [stops[0][1], stops[0][2], stops[0][3], stops[0][4]];
  if (value >= stops[stops.length - 1][0]) {
    const s = stops[stops.length - 1];
    return [s[1], s[2], s[3], s[4]];
  }
  for (let i = 0; i < stops.length - 1; i++) {
    if (value >= stops[i][0] && value <= stops[i + 1][0]) {
      const t = (value - stops[i][0]) / (stops[i + 1][0] - stops[i][0]);
      return [
        Math.round(stops[i][1] + t * (stops[i + 1][1] - stops[i][1])),
        Math.round(stops[i][2] + t * (stops[i + 1][2] - stops[i][2])),
        Math.round(stops[i][3] + t * (stops[i + 1][3] - stops[i][3])),
        Math.round(stops[i][4] + t * (stops[i + 1][4] - stops[i][4])),
      ];
    }
  }
  return [0, 0, 0, 0];
}

/**
 * Compute midpoints between grid coordinates for gap-free cell edges.
 * Returns array of length N+1 (edges around N cells).
 */
function computeEdges(coords) {
  if (coords.length < 2) return [coords[0] - 0.5, coords[0] + 0.5];
  const edges = new Array(coords.length + 1);
  edges[0] = coords[0] - (coords[1] - coords[0]) / 2;
  for (let i = 1; i < coords.length; i++) {
    edges[i] = (coords[i - 1] + coords[i]) / 2;
  }
  edges[coords.length] = coords[coords.length - 1] + (coords[coords.length - 1] - coords[coords.length - 2]) / 2;
  return edges;
}

/** Draw a wind arrow from (cx,cy) pointing in meteorological direction */
function drawWindArrow(ctx, cx, cy, u, v, len) {
  const angle = Math.atan2(-v, -u); /* meteorological: direction wind comes FROM → we draw arrow in direction it blows TO */
  const dirAngle = Math.atan2(v, u);
  const endX = cx + Math.cos(dirAngle) * len;
  const endY = cy - Math.sin(dirAngle) * len; /* canvas Y is inverted */
  const headLen = len * 0.35;
  const headAngle = Math.PI / 6;

  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.lineTo(endX, endY);
  /* arrowhead */
  ctx.lineTo(
    endX - headLen * Math.cos(dirAngle - headAngle),
    endY + headLen * Math.sin(dirAngle - headAngle)
  );
  ctx.moveTo(endX, endY);
  ctx.lineTo(
    endX - headLen * Math.cos(dirAngle + headAngle),
    endY + headLen * Math.sin(dirAngle + headAngle)
  );
  ctx.stroke();
}

const CanvasOverlay = forwardRef(function CanvasOverlay({ map, gridData, parameter, opacity = 0.5 }, ref) {
  const canvasRef = useRef(null);
  const paneRef = useRef(null);

  useImperativeHandle(ref, () => canvasRef.current);

  useEffect(() => {
    if (!map) return;
    /* Create a canvas in the overlay pane */
    const pane = map.getPane("overlayPane");
    const canvas = document.createElement("canvas");
    canvas.style.position = "absolute";
    canvas.style.pointerEvents = "none";
    canvas.style.zIndex = "200";
    pane.appendChild(canvas);
    canvasRef.current = canvas;
    paneRef.current = pane;

    return () => {
      if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
    };
  }, [map]);

  /* Re-draw whenever gridData changes or map moves */
  useEffect(() => {
    if (!map || !canvasRef.current || !gridData) return;

    const draw = () => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const size = map.getSize();
      canvas.width = size.x;
      canvas.height = size.y;
      /* Position canvas at the top-left of the map container */
      const topLeft = map.containerPointToLayerPoint([0, 0]);
      canvas.style.transform = `translate(${topLeft.x}px, ${topLeft.y}px)`;

      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.globalAlpha = opacity;

      const { lats, lons, values } = gridData;
      if (!lats || !lons || !values || lats.length === 0) return;

      const stops = gridData.color_scale || DEFAULT_STOPS;
      const is2D = Array.isArray(values[0]);

      if (is2D) {
        /* ── Gap-free 2D grid using midpoint edges ─────────── */
        const latEdges = computeEdges(lats);
        const lonEdges = computeEdges(lons);

        for (let i = 0; i < lats.length; i++) {
          /* Pixel Y edges for this row */
          const ptTop = map.latLngToContainerPoint([latEdges[i + 1], lons[0]]); /* higher lat = top */
          const ptBot = map.latLngToContainerPoint([latEdges[i], lons[0]]);     /* lower lat = bottom */
          const y0 = Math.round(Math.min(ptTop.y, ptBot.y));
          const y1 = Math.round(Math.max(ptTop.y, ptBot.y));
          const rowH = Math.max(y1 - y0, 1);

          for (let j = 0; j < lons.length; j++) {
            const val = values[i]?.[j];
            if (val == null || isNaN(val)) continue;

            const ptLeft = map.latLngToContainerPoint([lats[i], lonEdges[j]]);
            const ptRight = map.latLngToContainerPoint([lats[i], lonEdges[j + 1]]);
            const x0 = Math.round(Math.min(ptLeft.x, ptRight.x));
            const x1 = Math.round(Math.max(ptLeft.x, ptRight.x));
            const colW = Math.max(x1 - x0, 1);

            const color = interpolateColor(val, stops);
            ctx.fillStyle = `rgba(${color[0]},${color[1]},${color[2]},${(color[3] || 180) / 255})`;
            ctx.fillRect(x0, y0, colW, rowH);
          }
        }

        /* ── Wind arrows (if u/v data present) ─────────────── */
        const uComp = gridData.u_component;
        const vComp = gridData.v_component;
        if (uComp && vComp) {
          ctx.globalAlpha = 1.0; /* arrows at full opacity */
          /* Subsample: ~30px min spacing between arrows */
          const zoom = map.getZoom();
          const arrowLen = Math.max(8, Math.min(20, 4 + zoom * 2));
          const spacing = Math.max(30, arrowLen * 2.5);

          /* Find grid step that gives ~spacing px between arrows */
          const p0 = map.latLngToContainerPoint([lats[0], lons[0]]);
          const pLatStep = map.latLngToContainerPoint([lats[Math.min(1, lats.length - 1)], lons[0]]);
          const pLonStep = map.latLngToContainerPoint([lats[0], lons[Math.min(1, lons.length - 1)]]);
          const latPxStep = Math.abs(pLatStep.y - p0.y) || 1;
          const lonPxStep = Math.abs(pLonStep.x - p0.x) || 1;
          const iStep = Math.max(1, Math.round(spacing / latPxStep));
          const jStep = Math.max(1, Math.round(spacing / lonPxStep));

          ctx.strokeStyle = "rgba(255,255,255,0.85)";
          ctx.lineWidth = Math.max(1.5, zoom * 0.3);
          ctx.lineCap = "round";

          for (let i = 0; i < lats.length; i += iStep) {
            for (let j = 0; j < lons.length; j += jStep) {
              const u = uComp[i]?.[j];
              const v = vComp[i]?.[j];
              if (u == null || v == null || isNaN(u) || isNaN(v)) continue;
              const speed = Math.sqrt(u * u + v * v);
              if (speed < 0.5) continue; /* skip calm */
              const pt = map.latLngToContainerPoint([lats[i], lons[j]]);
              drawWindArrow(ctx, pt.x, pt.y, u, v, arrowLen);
            }
          }
        }
      } else {
        /* 1D flat: parallel arrays */
        const dotR = Math.max(3, Math.min(12, 2 + map.getZoom() * 0.8));
        for (let i = 0; i < lats.length; i++) {
          const val = values[i];
          if (val == null || isNaN(val)) continue;
          const pt = map.latLngToContainerPoint([lats[i], lons[i]]);
          const color = interpolateColor(val, stops);
          ctx.fillStyle = `rgba(${color[0]},${color[1]},${color[2]},${(color[3] || 180) / 255})`;
          ctx.fillRect(pt.x - dotR, pt.y - dotR, dotR * 2, dotR * 2);
        }
      }
    };

    draw();
    map.on("moveend zoomend", draw);
    return () => map.off("moveend zoomend", draw);
  }, [map, gridData, parameter, opacity]);

  return null;
});

export default CanvasOverlay;
