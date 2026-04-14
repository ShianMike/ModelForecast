import { useState, useMemo } from "react";
import { X, Minus } from "lucide-react";
import useDraggable from "../hooks/useDraggable";
import {
  LineChart, Line, BarChart, Bar, AreaChart, Area,
  XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend,
} from "recharts";
import "./Meteogram.css";

const RANGE_OPTIONS = [
  { label: "1d", hours: 24 },
  { label: "3d", hours: 72 },
  { label: "5d", hours: 120 },
  { label: "All", hours: Infinity },
];

function fmtVal(v) { return v != null ? Number(v).toFixed(2) : "—"; }

function MeteogramTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 11, padding: "6px 10px" }}>
      <div style={{ fontWeight: 600, marginBottom: 2 }}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color || "var(--text-primary)" }}>
          {p.name}: {fmtVal(p.value)}
        </div>
      ))}
    </div>
  );
}

export default function Meteogram({ data, loading, point, model, onClose }) {
  const { offset, handleMouseDown } = useDraggable();
  const [collapsed, setCollapsed] = useState(false);
  const [rangeHours, setRangeHours] = useState(24);

  const chartData = useMemo(() => {
    if (!data?.time?.length) return [];
    const t0 = data.time[0] ? new Date(data.time[0]).getTime() : 0;
    const cutoff = rangeHours === Infinity ? Infinity : t0 + rangeHours * 3600_000;
    return data.time
      .map((t, i) => {
        if (new Date(t).getTime() > cutoff) return null;
        return {
          time: new Date(t).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", hour12: false }),
          temp: data.temperature_2m?.[i],
          dewpoint: data.dewpoint_2m?.[i],
          wind: data.wind_speed_10m?.[i],
          gusts: data.wind_gusts_10m?.[i],
          precip: data.precipitation?.[i],
          cape: data.cape?.[i],
          cloud: data.cloud_cover?.[i],
        };
      })
      .filter(Boolean);
  }, [data, rangeHours]);

  const dragStyle = { transform: `translate(${offset.x}px, ${offset.y}px)` };

  if (loading) {
    return (
      <div className="meteogram card fade-in" style={dragStyle}>
        <div className="meteogram-header drag-handle" onMouseDown={handleMouseDown}>
          <span>Preparing Meteogram</span>
          <button className="btn-icon" onClick={onClose}><X size={14} /></button>
        </div>
        <div className="meteogram-skeleton" aria-hidden="true">
          <div className="meteogram-skeleton-toolbar">
            <div className="meteogram-skeleton-pill skeleton" />
            <div className="meteogram-skeleton-pill skeleton" />
            <div className="meteogram-skeleton-pill skeleton" />
            <div className="meteogram-skeleton-pill skeleton" />
          </div>
          <div className="meteogram-skeleton-panel skeleton"><div className="meteogram-skeleton-grid" /></div>
          <div className="meteogram-skeleton-panel skeleton"><div className="meteogram-skeleton-grid" /></div>
          <div className="meteogram-skeleton-panel skeleton"><div className="meteogram-skeleton-grid" /></div>
        </div>
      </div>
    );
  }
  if (!data || !data.time || !data.time.length) {
    return (
      <div className="meteogram card fade-in" style={dragStyle}>
        <div className="meteogram-header drag-handle" onMouseDown={handleMouseDown}>
          <span>Meteogram</span>
          <button className="btn-icon" onClick={onClose}><X size={14} /></button>
        </div>
        <div className="meteogram-empty">Click on the map to load a meteogram.</div>
      </div>
    );
  }

  return (
    <div className={`meteogram card fade-in${collapsed ? " meteogram-collapsed" : ""}`} style={dragStyle}>
      <div className="meteogram-header drag-handle" onMouseDown={handleMouseDown}>
        <div>
          <strong>{model?.toUpperCase()}</strong> Meteogram —{" "}
          <span className="mono">{point?.lat?.toFixed(2)}°N, {point?.lon?.toFixed(2)}°W</span>
        </div>
        <div className="meteogram-range-btns">
          {RANGE_OPTIONS.map(opt => (
            <button key={opt.label}
              className={`range-btn${rangeHours === opt.hours ? " active" : ""}`}
              onClick={() => setRangeHours(opt.hours)}>{opt.label}</button>
          ))}
        </div>
        <div style={{ display: "flex", gap: 2 }}>
          <button className="btn-icon" onClick={() => setCollapsed(c => !c)}><Minus size={14} /></button>
          <button className="btn-icon" onClick={onClose}><X size={14} /></button>
        </div>
      </div>

      {/* Temperature + Dewpoint */}
      {!collapsed && <>
      <div className="meteogram-panel">
        <div className="section-label">Temperature & Dewpoint (°F)</div>
        <ResponsiveContainer width="100%" height={140}>
          <LineChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis dataKey="time" tick={{ fontSize: 10, fill: "var(--text-muted)" }} interval="preserveStartEnd" />
            <YAxis tick={{ fontSize: 10, fill: "var(--text-muted)" }} />
            <Tooltip content={<MeteogramTooltip />} />
            <Line type="monotone" dataKey="temp" stroke="var(--red)" dot={false} strokeWidth={2} name="Temp" />
            <Line type="monotone" dataKey="dewpoint" stroke="var(--green)" dot={false} strokeWidth={2} name="Dewpoint" />
            <Legend wrapperStyle={{ fontSize: 10 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* Wind */}
      <div className="meteogram-panel">
        <div className="section-label">Wind Speed & Gusts (kt)</div>
        <ResponsiveContainer width="100%" height={110}>
          <BarChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis dataKey="time" tick={{ fontSize: 10, fill: "var(--text-muted)" }} interval="preserveStartEnd" />
            <YAxis tick={{ fontSize: 10, fill: "var(--text-muted)" }} />
            <Tooltip content={<MeteogramTooltip />} />
            <Bar dataKey="gusts" fill="var(--orange)" opacity={0.5} name="Gusts" />
            <Bar dataKey="wind" fill="var(--cyan)" name="Wind" />
            <Legend wrapperStyle={{ fontSize: 10 }} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Precipitation */}
      <div className="meteogram-panel">
        <div className="section-label">Precipitation (in)</div>
        <ResponsiveContainer width="100%" height={90}>
          <BarChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis dataKey="time" tick={{ fontSize: 10, fill: "var(--text-muted)" }} interval="preserveStartEnd" />
            <YAxis tick={{ fontSize: 10, fill: "var(--text-muted)" }} />
            <Tooltip content={<MeteogramTooltip />} />
            <Bar dataKey="precip" fill="var(--accent)" name="Precip" />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* CAPE */}
      {chartData.some(d => d.cape != null && d.cape > 0) && (
        <div className="meteogram-panel">
          <div className="section-label">CAPE (J/kg)</div>
          <ResponsiveContainer width="100%" height={90}>
            <AreaChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="time" tick={{ fontSize: 10, fill: "var(--text-muted)" }} interval="preserveStartEnd" />
              <YAxis tick={{ fontSize: 10, fill: "var(--text-muted)" }} />
              <Tooltip content={<MeteogramTooltip />} />
              <Area type="monotone" dataKey="cape" stroke="#ff6b35" fill="#ff6b35" fillOpacity={0.3} name="CAPE" />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Cloud Cover */}
      {chartData.some(d => d.cloud != null) && (
        <div className="meteogram-panel">
          <div className="section-label">Cloud Cover (%)</div>
          <ResponsiveContainer width="100%" height={90}>
            <AreaChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="time" tick={{ fontSize: 10, fill: "var(--text-muted)" }} interval="preserveStartEnd" />
              <YAxis tick={{ fontSize: 10, fill: "var(--text-muted)" }} domain={[0, 100]} />
              <Tooltip content={<MeteogramTooltip />} />
              <Area type="monotone" dataKey="cloud" stroke="#8884d8" fill="#8884d8" fillOpacity={0.3} name="Cloud Cover" />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
      </>}
    </div>
  );
}
