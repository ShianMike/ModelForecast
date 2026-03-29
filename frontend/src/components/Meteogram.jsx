import { X } from "lucide-react";
import {
  LineChart, Line, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend,
} from "recharts";
import "./Meteogram.css";

export default function Meteogram({ data, loading, point, model, onClose }) {
  if (loading) {
    return (
      <div className="meteogram card fade-in">
        <div className="meteogram-header">
          <span>Loading meteogram…</span>
          <button className="btn-icon" onClick={onClose}><X size={14} /></button>
        </div>
        <div className="meteogram-loading"><div className="spinner" /></div>
      </div>
    );
  }
  if (!data || !data.time) {
    return (
      <div className="meteogram card fade-in">
        <div className="meteogram-header">
          <span>Meteogram</span>
          <button className="btn-icon" onClick={onClose}><X size={14} /></button>
        </div>
        <div className="meteogram-empty">No data available for this point.</div>
      </div>
    );
  }

  /* Build chart data */
  const chartData = data.time.map((t, i) => ({
    time: new Date(t).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", hour12: false }),
    temp: data.temperature_2m?.[i],
    dewpoint: data.dewpoint_2m?.[i],
    wind: data.wind_speed_10m?.[i],
    gusts: data.wind_gusts_10m?.[i],
    precip: data.precipitation?.[i],
  }));

  return (
    <div className="meteogram card fade-in">
      <div className="meteogram-header">
        <div>
          <strong>{model?.toUpperCase()}</strong> Meteogram —{" "}
          <span className="mono">{point.lat.toFixed(2)}°N, {point.lon.toFixed(2)}°W</span>
        </div>
        <button className="btn-icon" onClick={onClose}><X size={14} /></button>
      </div>

      {/* Temperature + Dewpoint */}
      <div className="meteogram-panel">
        <div className="section-label">Temperature & Dewpoint (°F)</div>
        <ResponsiveContainer width="100%" height={160}>
          <LineChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis dataKey="time" tick={{ fontSize: 10, fill: "var(--text-muted)" }} interval="preserveStartEnd" />
            <YAxis tick={{ fontSize: 10, fill: "var(--text-muted)" }} />
            <Tooltip contentStyle={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }} />
            <Line type="monotone" dataKey="temp" stroke="var(--red)" dot={false} strokeWidth={2} name="Temp" />
            <Line type="monotone" dataKey="dewpoint" stroke="var(--green)" dot={false} strokeWidth={2} name="Dewpoint" />
            <Legend wrapperStyle={{ fontSize: 10 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* Wind */}
      <div className="meteogram-panel">
        <div className="section-label">Wind Speed & Gusts (kt)</div>
        <ResponsiveContainer width="100%" height={120}>
          <BarChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis dataKey="time" tick={{ fontSize: 10, fill: "var(--text-muted)" }} interval="preserveStartEnd" />
            <YAxis tick={{ fontSize: 10, fill: "var(--text-muted)" }} />
            <Tooltip contentStyle={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }} />
            <Bar dataKey="gusts" fill="var(--orange)" opacity={0.5} name="Gusts" />
            <Bar dataKey="wind" fill="var(--cyan)" name="Wind" />
            <Legend wrapperStyle={{ fontSize: 10 }} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Precipitation */}
      <div className="meteogram-panel">
        <div className="section-label">Precipitation (mm)</div>
        <ResponsiveContainer width="100%" height={100}>
          <BarChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis dataKey="time" tick={{ fontSize: 10, fill: "var(--text-muted)" }} interval="preserveStartEnd" />
            <YAxis tick={{ fontSize: 10, fill: "var(--text-muted)" }} />
            <Tooltip contentStyle={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }} />
            <Bar dataKey="precip" fill="var(--accent)" name="Precip" />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
