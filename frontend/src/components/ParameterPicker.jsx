import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import "./ParameterPicker.css";

export default function ParameterPicker({ categories, selected, onSelect }) {
  /* Backend returns { catKey: { label, params: { paramId: { name, unit, cmap } } } } */
  const [expanded, setExpanded] = useState(() => {
    for (const [cat, catData] of Object.entries(categories || {})) {
      const params = catData.params || catData;
      if (typeof params === "object" && !Array.isArray(params)) {
        if (Object.keys(params).includes(selected)) return cat;
      }
    }
    return Object.keys(categories || {})[0] || "";
  });

  if (!categories || Object.keys(categories).length === 0) {
    return <div className="param-empty">No parameters available</div>;
  }

  return (
    <div className="param-picker">
      {Object.entries(categories).map(([category, catData]) => {
        const label = catData.label || category;
        const paramsObj = catData.params || {};
        const paramList = Object.entries(paramsObj).map(([id, info]) => ({
          id,
          label: info.name || id,
          unit: info.unit || "",
        }));
        return (
          <div key={category} className="param-group">
            <button
              className="param-group-header"
              onClick={() => setExpanded(e => e === category ? "" : category)}
            >
              {expanded === category ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
              <span className="section-label">{label}</span>
              <span className="param-count">{paramList.length}</span>
            </button>
            {expanded === category && (
              <div className="param-list fade-in">
                {paramList.map(p => (
                  <button
                    key={p.id}
                    className={`param-item ${selected === p.id ? "active" : ""}`}
                    onClick={() => onSelect(p.id)}
                  >
                    <span className="param-name">{p.label}</span>
                    {p.unit && <span className="param-unit">{p.unit}</span>}
                  </button>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
