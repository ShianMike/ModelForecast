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

/**
 * Draw a standard meteorological wind barb at (cx, cy).
 * u, v are in m/s — converted to knots internally.
 * staffLen is the length of the barb staff in pixels.
 *
 * Convention: staff points INTO the wind (toward where wind comes FROM).
 * Barbs on the LEFT side of the staff (Northern Hemisphere standard).
 *   pennant (▲) = 50 kt,  full barb = 10 kt,  half barb = 5 kt
 */
const MS_TO_KT = 1.94384;

/**
 * Draw a single north-pointing wind barb for a given speed bucket onto an
 * offscreen canvas context.  The barb is drawn pointing straight UP (north)
 * so callers can simply rotate the sprite to the correct wind direction.
 *
 * cx, cy = center of the barb origin; staffLen = length of the staff line.
 */
function _drawBarbNorth(ctx, cx, cy, speedKt, staffLen) {
  if (speedKt < 2.5) {
    /* Calm — draw a circle */
    ctx.beginPath();
    ctx.arc(cx, cy, staffLen * 0.18, 0, Math.PI * 2);
    ctx.stroke();
    return;
  }

  /* Staff points straight up (north) from (cx, cy) */
  const tipX = cx;
  const tipY = cy - staffLen;

  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.lineTo(tipX, tipY);
  ctx.stroke();

  /* Decompose speed into pennants, full barbs, half barbs */
  let remaining = Math.round(speedKt / 5) * 5;
  const pennants = Math.floor(remaining / 50);
  remaining -= pennants * 50;
  const fullBarbs = Math.floor(remaining / 10);
  remaining -= fullBarbs * 10;
  const halfBarbs = Math.floor(remaining / 5);

  const barbLen = staffLen * 0.4;
  const barbSpacing = staffLen * 0.12;
  let pos = 0; /* distance from tip downward along the staff */

  /* Pennants (filled triangles — barbs on the left of the staff) */
  for (let p = 0; p < pennants; p++) {
    const baseY = tipY + pos;
    const nextPos = pos + barbSpacing * 1.5;
    const nextY = tipY + nextPos;

    ctx.beginPath();
    ctx.moveTo(tipX, baseY);
    ctx.lineTo(tipX - barbLen, baseY);
    ctx.lineTo(tipX, nextY);
    ctx.closePath();
    ctx.fill();

    pos = nextPos;
  }

  /* Full barbs (long lines to the left) */
  for (let f = 0; f < fullBarbs; f++) {
    const by = tipY + pos;
    ctx.beginPath();
    ctx.moveTo(tipX, by);
    ctx.lineTo(tipX - barbLen, by);
    ctx.stroke();
    pos += barbSpacing;
  }

  /* Half barbs (short lines to the left) */
  for (let h = 0; h < halfBarbs; h++) {
    if (pennants === 0 && fullBarbs === 0 && h === 0) {
      pos += barbSpacing;
    }
    const by = tipY + pos;
    ctx.beginPath();
    ctx.moveTo(tipX, by);
    ctx.lineTo(tipX - barbLen * 0.5, by);
    ctx.stroke();
    pos += barbSpacing;
  }
}

/**
 * Wind-barb sprite-sheet cache.
 *
 * Keyed by staffLen, each entry holds an object map from speed-bucket (0, 5,
 * 10, … 100 kt) → offscreen <canvas> containing that barb drawn pointing
 * north.  On the main canvas we simply translate → rotate → drawImage.
 */
const _barbSpriteCache = new Map();

function _getBarbSprite(speedKt, staffLen, lineWidth) {
  const bucket = speedKt < 2.5 ? 0 : Math.round(speedKt / 5) * 5;
  const cacheKey = `${staffLen}:${lineWidth.toFixed(1)}`;

  let sheet = _barbSpriteCache.get(cacheKey);
  if (!sheet) {
    sheet = new Map();
    _barbSpriteCache.set(cacheKey, sheet);
  }

  let sprite = sheet.get(bucket);
  if (sprite) return sprite;

  /* Render a new sprite */
  const size = Math.ceil(staffLen * 2);
  const offscreen = document.createElement("canvas");
  offscreen.width = size;
  offscreen.height = size;
  const ctx = offscreen.getContext("2d");
  ctx.strokeStyle = "rgba(255,255,255,0.9)";
  ctx.fillStyle = "rgba(255,255,255,0.9)";
  ctx.lineWidth = lineWidth;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  _drawBarbNorth(ctx, size / 2, size / 2, bucket, staffLen);

  sprite = { canvas: offscreen, size };
  sheet.set(bucket, sprite);
  return sprite;
}

/**
 * Stamp a wind barb at (cx, cy) using the sprite-sheet cache.
 * u, v in m/s — converted to knots internally.
 */
function drawWindBarb(ctx, cx, cy, u, v, staffLen, lineWidth) {
  const speedKt = Math.sqrt(u * u + v * v) * MS_TO_KT;
  const sprite = _getBarbSprite(speedKt, staffLen, lineWidth);

  if (speedKt < 2.5) {
    /* Calm — no rotation needed, just stamp */
    ctx.drawImage(sprite.canvas, cx - sprite.size / 2, cy - sprite.size / 2);
    return;
  }

  /* Direction wind is coming FROM (meteorological convention).
     The sprite points north (up), so we rotate by the "from" angle
     measured clockwise from north. */
  const fromAngle = Math.atan2(-u, v); /* clockwise from north */
  ctx.save();
  ctx.translate(cx, cy);
  ctx.rotate(fromAngle);
  ctx.drawImage(sprite.canvas, -sprite.size / 2, -sprite.size / 2);
  ctx.restore();
}

/**
 * Marching-squares contour line drawing.
 * Draws iso-lines on the canvas for a 2D grid at given threshold levels.
 */
function drawContours(ctx, map, lats, lons, values, levels, labelSize) {
  if (!values || !Array.isArray(values[0])) return;
  const rows = lats.length;
  const cols = lons.length;

  ctx.save();
  ctx.lineWidth = 1.2;
  ctx.font = `bold ${labelSize}px var(--font-mono), monospace`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";

  /* Minimum pixel distance between any two labels (across all levels) */
  const MIN_LABEL_DIST = 120;
  const allLabelPts = [];

  /* Pre-compute label text + width for each level (avoids measureText inside loops) */
  const labelCache = new Map();
  for (const level of levels) {
    const text = Number.isInteger(level) ? String(level) : level.toFixed(1);
    labelCache.set(level, { text, width: ctx.measureText(text).width });
  }

  for (const level of levels) {
    ctx.strokeStyle = "rgba(255,255,255,0.6)";
    const midpoints = [];

    for (let i = 0; i < rows - 1; i++) {
      for (let j = 0; j < cols - 1; j++) {
        const v00 = values[i][j], v10 = values[i + 1]?.[j];
        const v01 = values[i][j + 1], v11 = values[i + 1]?.[j + 1];
        if (v00 == null || v10 == null || v01 == null || v11 == null) continue;
        if (isNaN(v00) || isNaN(v10) || isNaN(v01) || isNaN(v11)) continue;

        /* Marching squares case index */
        const c = (v00 >= level ? 8 : 0) | (v01 >= level ? 4 : 0) |
                  (v11 >= level ? 2 : 0) | (v10 >= level ? 1 : 0);
        if (c === 0 || c === 15) continue;

        /* Interpolate edge crossings */
        const lerp = (a, b) => (level - a) / (b - a);
        const top    = lerp(v00, v01);
        const bottom = lerp(v10, v11);
        const left   = lerp(v00, v10);
        const right  = lerp(v01, v11);

        /* Pixel positions of cell corners */
        const p00 = map.latLngToContainerPoint([lats[i], lons[j]]);
        const p01 = map.latLngToContainerPoint([lats[i], lons[j+1]]);
        const p10 = map.latLngToContainerPoint([lats[i+1], lons[j]]);
        const p11 = map.latLngToContainerPoint([lats[i+1], lons[j+1]]);

        const edgeT = { x: p00.x + top * (p01.x - p00.x), y: p00.y + top * (p01.y - p00.y) };
        const edgeB = { x: p10.x + bottom * (p11.x - p10.x), y: p10.y + bottom * (p11.y - p10.y) };
        const edgeL = { x: p00.x + left * (p10.x - p00.x), y: p00.y + left * (p10.y - p00.y) };
        const edgeR = { x: p01.x + right * (p11.x - p01.x), y: p01.y + right * (p11.y - p01.y) };

        const segments = [];
        switch (c) {
          case 1: case 14: segments.push([edgeL, edgeB]); break;
          case 2: case 13: segments.push([edgeB, edgeR]); break;
          case 3: case 12: segments.push([edgeL, edgeR]); break;
          case 4: case 11: segments.push([edgeT, edgeR]); break;
          case 5: segments.push([edgeT, edgeL], [edgeB, edgeR]); break;
          case 6: case 9:  segments.push([edgeT, edgeB]); break;
          case 7: case 8:  segments.push([edgeT, edgeL]); break;
          case 10: segments.push([edgeT, edgeR], [edgeB, edgeL]); break;
          default: break;
        }

        for (const [a, b] of segments) {
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
          midpoints.push({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
        }
      }
    }

    /* Place labels on the actual contour line at well-spaced midpoints */
    if (midpoints.length > 0) {
      const { text: label, width: tw } = labelCache.get(level);
      /* Pick midpoints spread across the contour */
      const targetCount = Math.min(12, Math.max(3, Math.round(midpoints.length / 60)));
      const step = Math.max(1, Math.floor(midpoints.length / targetCount));
      for (let k = Math.floor(step / 2); k < midpoints.length; k += step) {
        const pt = midpoints[k];
        /* Skip if too close to a previously placed label (any level) */
        const tooClose = allLabelPts.some(
          lp => Math.abs(lp.x - pt.x) < MIN_LABEL_DIST && Math.abs(lp.y - pt.y) < MIN_LABEL_DIST
        );
        if (tooClose) continue;
        allLabelPts.push(pt);
        /* Background pill */
        ctx.fillStyle = "rgba(0,0,0,0.65)";
        ctx.fillRect(pt.x - tw / 2 - 3, pt.y - labelSize / 2 - 2, tw + 6, labelSize + 4);
        ctx.fillStyle = "rgba(255,255,255,0.9)";
        ctx.fillText(label, pt.x, pt.y);
      }
    }
  }
  ctx.restore();
}

/** Compute nice contour levels for a range of values */
function computeContourLevels(stops, numLevels = 8) {
  const min = stops[0][0];
  const max = stops[stops.length - 1][0];
  const range = max - min;
  /* Choose a nice step */
  const rawStep = range / numLevels;
  const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const nice = [1, 2, 5, 10].map(m => m * mag);
  const step = nice.find(n => n >= rawStep) || nice[nice.length - 1];
  const start = Math.ceil(min / step) * step;
  const levels = [];
  for (let v = start; v <= max; v += step) levels.push(Math.round(v * 1000) / 1000);
  return levels;
}

const CanvasOverlay = forwardRef(function CanvasOverlay({ map, gridData, parameter, opacity = 0.5, showContours = false }, ref) {
  const canvasRef = useRef(null);
  const paneRef = useRef(null);

  useImperativeHandle(ref, () => canvasRef.current);

  useEffect(() => {
    if (!map) return;
    /* Create a canvas in the overlay pane */
    const pane = map.getPane("overlayPane");
    if (!pane) return;
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
    let frameId = null;

    const draw = () => {
      frameId = null;
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

      /* If opacity is 0, clear and bail — no colours should be visible */
      if (opacity <= 0) return;

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
            const a = ((color[3] ?? 180) / 255) * opacity;
            ctx.fillStyle = `rgba(${color[0]},${color[1]},${color[2]},${a})`;
            ctx.fillRect(x0, y0, colW, rowH);
          }
        }

        /* ── Wind barbs (if u/v data present) ──────────────── */
        const uComp = gridData.u_component;
        const vComp = gridData.v_component;
        if (uComp && vComp) {
          ctx.globalAlpha = 1.0; /* barbs at full opacity */
          /* Subsample: enough spacing so barbs don't overlap */
          const zoom = map.getZoom();
          const staffLen = Math.max(14, Math.min(28, 6 + zoom * 2.5));
          const spacing = Math.max(40, staffLen * 2.5);

          /* Find grid step that gives ~spacing px between arrows */
          const p0 = map.latLngToContainerPoint([lats[0], lons[0]]);
          const pLatStep = map.latLngToContainerPoint([lats[Math.min(1, lats.length - 1)], lons[0]]);
          const pLonStep = map.latLngToContainerPoint([lats[0], lons[Math.min(1, lons.length - 1)]]);
          const latPxStep = Math.abs(pLatStep.y - p0.y) || 1;
          const lonPxStep = Math.abs(pLonStep.x - p0.x) || 1;
          const iStep = Math.max(1, Math.round(spacing / latPxStep));
          const jStep = Math.max(1, Math.round(spacing / lonPxStep));

          ctx.strokeStyle = "rgba(255,255,255,0.9)";
          ctx.fillStyle = "rgba(255,255,255,0.9)";
          const barbLineWidth = Math.max(1.5, zoom * 0.35);
          ctx.lineWidth = barbLineWidth;
          ctx.lineCap = "round";
          ctx.lineJoin = "round";

          for (let i = 0; i < lats.length; i += iStep) {
            for (let j = 0; j < lons.length; j += jStep) {
              const u = uComp[i]?.[j];
              const v = vComp[i]?.[j];
              if (u == null || v == null || isNaN(u) || isNaN(v)) continue;
              const pt = map.latLngToContainerPoint([lats[i], lons[j]]);
              drawWindBarb(ctx, pt.x, pt.y, u, v, staffLen, barbLineWidth);
            }
          }
        }

        /* ── Contour lines (if enabled) ────────────────────── */
        if (showContours) {
          ctx.globalAlpha = 1.0;
          const levels = computeContourLevels(stops);
          const labelSz = Math.max(9, Math.min(12, map.getZoom()));
          drawContours(ctx, map, lats, lons, values, levels, labelSz);
        }
      } else {
        /* 1D flat: parallel arrays */
        const dotR = Math.max(3, Math.min(12, 2 + map.getZoom() * 0.8));
        for (let i = 0; i < lats.length; i++) {
          const val = values[i];
          if (val == null || isNaN(val)) continue;
          const pt = map.latLngToContainerPoint([lats[i], lons[i]]);
          const color = interpolateColor(val, stops);
          const a = ((color[3] ?? 180) / 255) * opacity;
          ctx.fillStyle = `rgba(${color[0]},${color[1]},${color[2]},${a})`;
          ctx.fillRect(pt.x - dotR, pt.y - dotR, dotR * 2, dotR * 2);
        }
      }
    };

    const scheduleDraw = () => {
      if (frameId !== null) return;
      frameId = window.requestAnimationFrame(draw);
    };

    scheduleDraw();
    map.on("moveend zoomend resize", scheduleDraw);
    return () => {
      if (frameId !== null) window.cancelAnimationFrame(frameId);
      map.off("moveend zoomend resize", scheduleDraw);
    };
  }, [map, gridData, parameter, opacity, showContours]);

  return null;
});

export default CanvasOverlay;
