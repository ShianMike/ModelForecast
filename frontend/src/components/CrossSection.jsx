import { useState } from "react";
import { X, Scissors, Minus } from "lucide-react";
import useDraggable from "../hooks/useDraggable";
import {
  ScatterChart, Scatter, XAxis, YAxis, ZAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Cell,
} from "recharts";
import "./CrossSection.css";

/* Interpolate a value to a color on a blue→white→red scale */
function valueColor(val, min, max) {
  if (val == null) return "#666";
  if (max <= min) return "rgb(255,255,255)";
  const norm = (Math.max(min, Math.min(max, val)) - min) / (max - min);
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

export default function CrossSection({ data, loading, progress, line, model, onClose, onStartDraw }) {
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
    const pct = progress ? Math.round((progress.completed / progress.total) * 100) : 0;
    return (
      <div className="cross-section card fade-in" style={dragStyle}>
        <div className="cross-header drag-handle" onMouseDown={handleMouseDown}>
          <span>Loading cross-section{progress ? ` (${pct}%)` : "…"}</span>
          <button className="btn-icon" onClick={onClose}><X size={14} /></button>
        </div>
        <div className="cross-loading">
          {progress ? (
            <div className="cross-progress">
              <div className="cross-progress-bar" style={{ width: `${pct}%` }} />
            </div>
          ) : (
            <div className="spinner" />
          )}
        </div>
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
  let minValue = Infinity;
  let maxValue = -Infinity;
  for (let i = 0; i < data.distances.length; i++) {
    for (let j = 0; j < data.levels.length; j++) {
      const val = data.values[i]?.[j];
      if (val != null) {
        minValue = Math.min(minValue, val);
        maxValue = Math.max(maxValue, val);
        chartData.push({
          distance: data.distances[i],
          pressure: data.levels[j],
          value: val,
        });
      }
    }
  }
  const safeMinValue = Number.isFinite(minValue) ? minValue : 0;
  const safeMaxValue = Number.isFinite(maxValue) ? maxValue : safeMinValue;
  const midValue = (safeMinValue + safeMaxValue) / 2;
  const valueLabel = data.label || "Value";
  const unitLabel = data.unit || "";
  const formatValue = (val) => `${Number(val).toFixed(1)}${unitLabel}`;

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
        <div className="section-label">{valueLabel}{unitLabel ? ` (${unitLabel})` : ""} along line ({data.distances[data.distances.length - 1]} km)</div>
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
            <ZAxis dataKey="value" range={[80, 80]} />
            <Tooltip
              contentStyle={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }}
              formatter={(val) => [formatValue(val), valueLabel]}
            />
            <Scatter data={chartData} shape="square">
              {chartData.map((d, i) => (
                <Cell key={i} fill={valueColor(d.value, safeMinValue, safeMaxValue)} />
              ))}
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      </div>

      <div className="cross-legend">
        <span style={{ color: "rgb(50,50,255)" }}>{formatValue(safeMinValue)}</span>
        <span style={{ color: "rgb(255,255,255)" }}>{formatValue(midValue)}</span>
        <span style={{ color: "rgb(255,50,50)" }}>{formatValue(safeMaxValue)}</span>
      </div>
      </>}
    </div>
  );
}
