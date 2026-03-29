import { useEffect, useState } from "react";
import { fetchColorScale } from "../api";
import "./ColorBar.css";

/* Fallback color stops — same [value, r, g, b, a] format as backend */
const FALLBACK_STOPS = [
  [-40, 148, 103, 189, 255],
  [0,   44,  160, 44,  255],
  [40,  255, 127, 14,  255],
  [80,  214, 39,  40,  255],
  [120, 255, 255, 255, 255],
];

export default function ColorBar({ parameter, parameterCategories }) {
  const [stops, setStops] = useState(FALLBACK_STOPS);
  const [unit, setUnit] = useState("");

  /* Resolve the cmap name from parameterCategories metadata */
  useEffect(() => {
    if (!parameter) return;
    let cmap = parameter; // fallback to parameter name
    if (parameterCategories) {
      for (const cat of Object.values(parameterCategories)) {
        const p = cat.params?.[parameter];
        if (p?.cmap) { cmap = p.cmap; break; }
      }
    }
    fetchColorScale(cmap)
      .then(data => {
        if (data.stops?.length) setStops(data.stops);
        if (data.unit) setUnit(data.unit);
      })
      .catch(() => { /* keep fallback */ });
  }, [parameter, parameterCategories]);

  const gradient = stops.map((s, i) => {
    const pct = (i / (stops.length - 1)) * 100;
    return `rgb(${s[1]},${s[2]},${s[3]}) ${pct}%`;
  }).join(", ");

  return (
    <div className="color-bar">
      <div className="color-bar-gradient" style={{ background: `linear-gradient(to right, ${gradient})` }} />
      <div className="color-bar-labels">
        {stops.filter((_, i) => i % Math.max(1, Math.floor(stops.length / 6)) === 0 || i === stops.length - 1).map((s, i) => (
          <span key={i} className="color-bar-label mono">{s[0]}</span>
        ))}
      </div>
      {unit && <div className="color-bar-unit">{unit}</div>}
    </div>
  );
}
