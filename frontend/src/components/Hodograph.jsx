import { useRef, useEffect, useCallback, useState } from "react";
import { X, Minus } from "lucide-react";
import useDraggable from "../hooks/useDraggable";
import "./Hodograph.css";

const MIN_SPEED = 40; // minimum outer ring

/* Pivotal Weather–style height AGL layers */
const LAYER_COLORS = [
  { minKm: 0,  maxKm: 3,  color: "#ef4444", label: "0–3 km" },   // red
  { minKm: 3,  maxKm: 6,  color: "#22c55e", label: "3–6 km" },   // green
  { minKm: 6,  maxKm: 9,  color: "#3b82f6", label: "6–9 km" },   // blue
  { minKm: 9,  maxKm: 12, color: "#a855f7", label: "9–12 km" },  // purple
];

function getLayerColor(heightAglM) {
  const km = heightAglM / 1000;
  for (const l of LAYER_COLORS) {
    if (km >= l.minKm && km < l.maxKm) return l.color;
  }
  return "#888";
}

export default function Hodograph({ data, loading, point, model, onClose }) {
  const canvasRef = useRef(null);
  const { offset, handleMouseDown } = useDraggable();
  const [collapsed, setCollapsed] = useState(false);
  const dragStyle = { transform: `translate(${offset.x}px, ${offset.y}px)` };

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || !data?.profile?.length) return;

    const dpr = window.devicePixelRatio || 1;
    const W = canvas.clientWidth;
    const H = canvas.clientHeight;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);

    const isDark = document.documentElement.getAttribute("data-theme") !== "light";
    const BG = isDark ? "#0a0a0a" : "#fff";
    const GRID = isDark ? "rgba(200,200,200,0.12)" : "rgba(100,100,100,0.15)";
    const AXIS = isDark ? "rgba(200,200,200,0.25)" : "rgba(100,100,100,0.3)";
    const TEXT = isDark ? "#aaa" : "#555";
    const RING_TEXT = isDark ? "#666" : "#999";

    ctx.fillStyle = BG;
    ctx.fillRect(0, 0, W, H);

    const cx = W / 2;
    const cy = H / 2;
    const radius = Math.min(W, H) / 2 - 24;

    /* Auto-scale: compute max wind speed, round up to nearest 20 */
    const allSpeeds = data.profile
      .filter(p => p.wind_speed != null)
      .map(p => p.wind_speed);
    const maxWind = Math.max(...allSpeeds, 0);
    const maxSpeed = Math.max(MIN_SPEED, Math.ceil(maxWind / 20) * 20);
    const rings = [];
    for (let r = 20; r <= maxSpeed; r += 20) rings.push(r);
    const scale = radius / maxSpeed;

    /* Speed rings */
    for (const r of rings) {
      const rPx = r * scale;
      ctx.strokeStyle = GRID;
      ctx.lineWidth = 0.5;
      ctx.beginPath();
      ctx.arc(cx, cy, rPx, 0, Math.PI * 2);
      ctx.stroke();
      /* Label */
      ctx.fillStyle = RING_TEXT;
      ctx.font = "9px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(`${r}`, cx + rPx + 1, cy - 3);
    }

    /* Crosshairs (N-S, E-W) */
    ctx.strokeStyle = AXIS;
    ctx.lineWidth = 0.8;
    ctx.beginPath();
    ctx.moveTo(cx - radius - 10, cy);
    ctx.lineTo(cx + radius + 10, cy);
    ctx.moveTo(cx, cy - radius - 10);
    ctx.lineTo(cx, cy + radius + 10);
    ctx.stroke();

    /* Cardinal labels */
    ctx.fillStyle = TEXT;
    ctx.font = "bold 10px sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("N", cx, cy - radius - 14);
    ctx.fillText("S", cx, cy + radius + 14);
    ctx.fillText("E", cx + radius + 14, cy);
    ctx.fillText("W", cx - radius - 14, cy);

    /* Plot hodograph trace */
    const sorted = [...data.profile]
      .filter(p => p.wind_speed != null && p.wind_direction != null)
      .sort((a, b) => b.pressure - a.pressure); // surface first

    if (sorted.length < 2) {
      ctx.fillStyle = TEXT;
      ctx.font = "12px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("Insufficient wind data", cx, cy);
      return;
    }

    /* Surface height for AGL computation */
    const sfcHeight = sorted[0]?.height ?? 0;

    /* Convert wind to u,v components (meteorological → math) */
    const points = sorted.map(p => {
      const dirRad = (p.wind_direction * Math.PI) / 180;
      const u = -p.wind_speed * Math.sin(dirRad);
      const v = -p.wind_speed * Math.cos(dirRad);
      const agl = (p.height ?? 0) - sfcHeight;
      return {
        x: cx + u * scale,
        y: cy - v * scale, // canvas y is inverted
        pressure: p.pressure,
        speed: p.wind_speed,
        dir: p.wind_direction,
        agl,
      };
    });

    /* Draw line segments colored by height AGL layer */
    ctx.lineWidth = 2.5;
    ctx.lineCap = "round";
    for (let i = 0; i < points.length - 1; i++) {
      const p1 = points[i];
      const p2 = points[i + 1];
      ctx.strokeStyle = getLayerColor(p1.agl);
      ctx.beginPath();
      ctx.moveTo(p1.x, p1.y);
      ctx.lineTo(p2.x, p2.y);
      ctx.stroke();
    }

    /* Draw dots at each level */
    for (const p of points) {
      ctx.fillStyle = getLayerColor(p.agl);
      ctx.beginPath();
      ctx.arc(p.x, p.y, 3, 0, Math.PI * 2);
      ctx.fill();
    }

    /* Label key levels (km AGL) */
    const labelKm = [1, 3, 6, 9];
    ctx.font = "bold 8px sans-serif";
    ctx.textAlign = "left";
    ctx.textBaseline = "bottom";
    let lastLabelY = -Infinity;
    for (const p of points) {
      const km = p.agl / 1000;
      const closest = labelKm.find(k => Math.abs(km - k) < 0.5);
      if (closest != null && Math.abs(p.y - lastLabelY) > 14) {
        ctx.fillStyle = getLayerColor(p.agl);
        ctx.fillText(`${closest} km`, p.x + 5, p.y - 2);
        lastLabelY = p.y;
      }
    }

    /* Origin dot */
    ctx.fillStyle = isDark ? "#fff" : "#000";
    ctx.beginPath();
    ctx.arc(cx, cy, 2.5, 0, Math.PI * 2);
    ctx.fill();

    /* Legend — compact inline */
    ctx.font = "bold 8px sans-serif";
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    const lx = 6;
    LAYER_COLORS.forEach((l, i) => {
      const iy = 12 + i * 11;
      ctx.fillStyle = l.color;
      ctx.fillRect(lx, iy - 3, 8, 6);
      ctx.fillStyle = isDark ? "#bbb" : "#555";
      ctx.fillText(l.label, lx + 11, iy);
    });

  }, [data]);

  useEffect(() => { draw(); }, [draw]);
  useEffect(() => {
    const fn = () => draw();
    window.addEventListener("resize", fn);
    return () => window.removeEventListener("resize", fn);
  }, [draw]);

  if (loading) {
    return (
      <div className="hodograph fade-in" style={dragStyle}>
        <div className="hodograph-header drag-handle" onMouseDown={handleMouseDown}>
          <span>Loading hodograph…</span>
          <button className="btn-icon" onClick={onClose}><X size={14} /></button>
        </div>
        <div className="hodograph-loading"><div className="spinner" /></div>
      </div>
    );
  }
  if (!data?.profile?.length) {
    return (
      <div className="hodograph fade-in" style={dragStyle}>
        <div className="hodograph-header drag-handle" onMouseDown={handleMouseDown}>
          <span>Hodograph</span>
          <button className="btn-icon" onClick={onClose}><X size={14} /></button>
        </div>
        <div className="hodograph-empty">No sounding data available.</div>
      </div>
    );
  }

  return (
    <div className="hodograph fade-in" style={dragStyle}>
      <div className="hodograph-header drag-handle" onMouseDown={handleMouseDown}>
        <div>
          <strong>{model?.toUpperCase()}</strong> Hodograph — F{String(data.forecast_hour).padStart(3, "0")}
          <br />
          <span className="mono" style={{ fontSize: 11 }}>
            {point?.lat?.toFixed(2)}°N, {point?.lon?.toFixed(2)}°W
          </span>
        </div>
        <div style={{ display: "flex", gap: 2 }}>
          <button className="btn-icon" onClick={() => setCollapsed(c => !c)}><Minus size={14} /></button>
          <button className="btn-icon" onClick={onClose}><X size={14} /></button>
        </div>
      </div>
      {!collapsed && <canvas ref={canvasRef} className="hodograph-canvas" />}
    </div>
  );
}
