import { useState, useCallback } from "react";
import { X, Scissors, Minus } from "lucide-react";
import useDraggable from "../hooks/useDraggable";
import {
  ScatterChart, Scatter, XAxis, YAxis, ZAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Cell,
} from "recharts";
import "./CrossSection.css";

/* Interpolate a value to a color on a blue→white→red scale */
function tempColor(val) {
  if (val == null) return "#666";
  const t = Math.max(-40, Math.min(40, val));
  const norm = (t + 40) / 80; // 0..1
  if (norm < 0.5) {
    const f = norm * 2;
    const r = Math.round(50 + f * 205);
    const g = Math.round(50 + f * 205);
    const b = 255;
    return `rgb(${r},${g},${b})`;
  }
  const f = (norm - 0.5) * 2;
  const r = 255;
  const g = Math.round(255 - f * 205);
  const b = Math.round(255 - f * 205);
  return `rgb(${r},${g},${b})`;
}

export default function CrossSection({ data, loading, line, model, onClose, onStartDraw }) {
  const { offset, handleMouseDown } = useDraggable();
  const [collapsed, setCollapsed] = useState(false);
  const dragStyle = { transform: `translate(${offset.x}px, ${offset.y}px)` };

  if (!line && !data && !loading) {
    return (
      <div className="cross-section card fade-in" style={dragStyle}>
        <div className="cross-header drag-handle" onMouseDown={handleMouseDown}>
          <span>Cross-Section Tool</span>
          <button className="btn-icon" onClick={onClose}><X size={14} /></button>
        </div>
        <div className="cross-empty">
          <Scissors size={20} style={{ marginBottom: 8 }} />
          <div>Click two points on the map to draw a cross-section line.</div>
          <button className="btn btn-primary" style={{ marginTop: 12, fontSize: 12 }} onClick={onStartDraw}>
            Start Drawing
          </button>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="cross-section card fade-in" style={dragStyle}>
        <div className="cross-header drag-handle" onMouseDown={handleMouseDown}>
          <span>Loading cross-section…</span>
          <button className="btn-icon" onClick={onClose}><X size={14} /></button>
        </div>
        <div className="cross-loading"><div className="spinner" /></div>
      </div>
    );
  }

  if (!data?.values?.length) {
    return (
      <div className="cross-section card fade-in" style={dragStyle}>
        <div className="cross-header drag-handle" onMouseDown={handleMouseDown}>
          <span>Cross-Section</span>
          <button className="btn-icon" onClick={onClose}><X size={14} /></button>
        </div>
        <div className="cross-empty">No data available.</div>
      </div>
    );
  }

  /* Build scatter data for heatmap-like visualization */
  const chartData = [];
  for (let i = 0; i < data.distances.length; i++) {
    for (let j = 0; j < data.levels.length; j++) {
      const val = data.values[i]?.[j];
      if (val != null) {
        chartData.push({
          distance: data.distances[i],
          pressure: data.levels[j],
          temperature: val,
        });
      }
    }
  }

  return (
    <div className={`cross-section card fade-in${collapsed ? " cross-collapsed" : ""}`} style={dragStyle}>
      <div className="cross-header drag-handle" onMouseDown={handleMouseDown}>
        <div>
          <strong>{model?.toUpperCase()}</strong> Cross-Section — F{String(data.forecast_hour).padStart(3, "0")}
        </div>
        <div style={{ display: "flex", gap: 2 }}>
          <button className="btn-icon" onClick={() => setCollapsed(c => !c)}><Minus size={14} /></button>
          <button className="btn-icon" onClick={onClose}><X size={14} /></button>
        </div>
      </div>

      {!collapsed && <>
      <div className="cross-panel">
        <div className="section-label">Temperature (°C) along line ({data.distances[data.distances.length - 1]} km)</div>
        <ResponsiveContainer width="100%" height={280}>
          <ScatterChart>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis
              dataKey="distance"
              type="number"
              name="Distance"
              unit=" km"
              tick={{ fontSize: 10, fill: "var(--text-muted)" }}
            />
            <YAxis
              dataKey="pressure"
              type="number"
              reversed
              scale="log"
              domain={[200, 1050]}
              ticks={[1000, 850, 700, 500, 300, 200]}
              name="Pressure"
              unit=" hPa"
              tick={{ fontSize: 10, fill: "var(--text-muted)" }}
            />
            <ZAxis dataKey="temperature" range={[80, 80]} />
            <Tooltip
              contentStyle={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }}
              formatter={(val, name) => {
                if (name === "Temperature") return [`${val?.toFixed(1)}°C`];
                return [val];
              }}
            />
            <Scatter data={chartData} shape="square">
              {chartData.map((d, i) => (
                <Cell key={i} fill={tempColor(d.temperature)} />
              ))}
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      </div>

      <div className="cross-legend">
        <span style={{ color: "rgb(50,50,255)" }}>-40°C</span>
        <span style={{ color: "rgb(255,255,255)" }}>0°C</span>
        <span style={{ color: "rgb(255,50,50)" }}>+40°C</span>
      </div>
      </>}
    </div>
  );
}
