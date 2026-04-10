import { useState, useMemo } from "react";
import { X, Minus } from "lucide-react";
import useDraggable from "../hooks/useDraggable";
import {
  LineChart, Line, Area, AreaChart, ComposedChart,
  XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend,
} from "recharts";
import "./EnsemblePlume.css";

const RANGE_OPTIONS = [
  { label: "1d", hours: 24 },
  { label: "3d", hours: 72 },
  { label: "5d", hours: 120 },
  { label: "All", hours: Infinity },
];

function fmtVal(v) { return v != null ? Number(v).toFixed(2) : "—"; }

function EnsembleTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const median = payload.find(p => p.dataKey === "p50");
  const p25 = payload.find(p => p.dataKey === "p25");
  const p75 = payload.find(p => p.dataKey === "p75");
  return (
    <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 11, padding: "6px 10px" }}>
      <div style={{ fontWeight: 600, marginBottom: 2 }}>{label}</div>
      {median && <div style={{ color: "var(--accent)" }}>Median: {fmtVal(median.value)}</div>}
      {p25 && <div style={{ color: "var(--text-muted)" }}>25th: {fmtVal(p25.value)}</div>}
      {p75 && <div style={{ color: "var(--text-muted)" }}>75th: {fmtVal(p75.value)}</div>}
    </div>
  );
}

export default function EnsemblePlume({ data, loading, point, variable, onClose }) {
  const { offset, handleMouseDown } = useDraggable();
  const [collapsed, setCollapsed] = useState(false);
  const [rangeHours, setRangeHours] = useState(24);

  const pct = data?.percentiles || {};
  const paramLabel = variable?.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase()) || "Value";
  const sourceVariable = data?.source_variable || variable;
  const usingFallbackVariable = sourceVariable !== variable;
  const sourceLabel = sourceVariable?.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase()) || "Value";

  const chartData = useMemo(() => {
    if (!data?.times?.length) return [];
    const t0 = data.times[0] ? new Date(data.times[0]).getTime() : 0;
    const cutoff = rangeHours === Infinity ? Infinity : t0 + rangeHours * 3600_000;
    return data.times
      .map((t, i) => {
        if (new Date(t).getTime() > cutoff) return null;
        const row = {
          time: new Date(t).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", hour12: false }),
          p10: pct.p10?.[i],
          p25: pct.p25?.[i],
          p50: pct.p50?.[i],
          p75: pct.p75?.[i],
          p90: pct.p90?.[i],
        };
        let hasSignal = [row.p10, row.p25, row.p50, row.p75, row.p90]
          .some(v => v != null && Number.isFinite(Number(v)));
        if (data.members) {
          data.members.forEach((m, mi) => {
            row[`m${mi}`] = m[i];
            if (!hasSignal && m[i] != null && Number.isFinite(Number(m[i]))) {
              hasSignal = true;
            }
          });
        }
        if (!hasSignal) return null;
        return row;
      })
      .filter(Boolean);
  }, [data, pct, rangeHours]);

  const dragStyle = { transform: `translate(${offset.x}px, ${offset.y}px)` };

  if (loading) {
    return (
      <div className="ensemble-plume card fade-in" style={dragStyle}>
        <div className="ensemble-header drag-handle" onMouseDown={handleMouseDown}>
          <span>Loading ensemble data…</span>
          <button className="btn-icon" onClick={onClose}><X size={14} /></button>
        </div>
        <div className="ensemble-loading"><div className="spinner" /></div>
      </div>
    );
  }
  if (!data?.times?.length) {
    return (
      <div className="ensemble-plume card fade-in" style={dragStyle}>
        <div className="ensemble-header drag-handle" onMouseDown={handleMouseDown}>
          <span>Ensemble Plume</span>
          <button className="btn-icon" onClick={onClose}><X size={14} /></button>
        </div>
        <div className="ensemble-empty">No ensemble data available.</div>
      </div>
    );
  }

  return (
    <div className={`ensemble-plume card fade-in${collapsed ? " ensemble-collapsed" : ""}`} style={dragStyle}>
      <div className="ensemble-header drag-handle" onMouseDown={handleMouseDown}>
        <div>
          <strong>GFS Ensemble</strong> — {paramLabel}
          <br />
          <span className="mono" style={{ fontSize: 11 }}>
            {point?.lat?.toFixed(2)}°N, {point?.lon?.toFixed(2)}°W • {data.n_members} members
          </span>
          {usingFallbackVariable && (
            <>
              <br />
              <span className="mono" style={{ fontSize: 10, opacity: 0.8 }}>
                Using ensemble fallback: {sourceLabel}
              </span>
            </>
          )}
        </div>
        <div className="ensemble-range-btns">
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

      {!collapsed && (
        <div className="ensemble-panel">
          {chartData.length < 2 ? (
            <div className="ensemble-empty">Insufficient ensemble signal for this range.</div>
          ) : (
            <div className="ensemble-chart">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                  <XAxis dataKey="time" tick={{ fontSize: 9, fill: "var(--text-muted)" }} interval="preserveStartEnd" />
                  <YAxis tick={{ fontSize: 10, fill: "var(--text-muted)" }} />
                  <Tooltip content={<EnsembleTooltip />} />

                  {/* Spread: 10th–90th percentile fill */}
                  <Area type="monotone" dataKey="p90" stroke="none" fill="var(--accent)" fillOpacity={0.1} legendType="none" connectNulls />
                  <Area type="monotone" dataKey="p10" stroke="none" fill="var(--bg-card)" fillOpacity={1} legendType="none" connectNulls />
                  {/* 25th–75th IQR */}
                  <Area type="monotone" dataKey="p75" stroke="none" fill="var(--accent)" fillOpacity={0.15} legendType="none" connectNulls />
                  <Area type="monotone" dataKey="p25" stroke="none" fill="var(--bg-card)" fillOpacity={1} legendType="none" connectNulls />

                  {/* Individual members (faint) */}
                  {data.members?.map((_, mi) => (
                    <Line key={`m${mi}`} type="monotone" dataKey={`m${mi}`}
                      stroke="var(--text-muted)" strokeWidth={0.5} dot={false}
                      strokeOpacity={0.3} legendType="none" connectNulls />
                  ))}

                  {/* Median (bold) */}
                  <Line type="monotone" dataKey="p50" stroke="var(--accent)" strokeWidth={2.5} dot={false} name="Median" connectNulls />
                  <Line type="monotone" dataKey="p25" stroke="var(--accent)" strokeWidth={1} dot={false} strokeDasharray="4 4" name="25th pctl" connectNulls />
                  <Line type="monotone" dataKey="p75" stroke="var(--accent)" strokeWidth={1} dot={false} strokeDasharray="4 4" name="75th pctl" connectNulls />

                  <Legend wrapperStyle={{ fontSize: 9 }} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
