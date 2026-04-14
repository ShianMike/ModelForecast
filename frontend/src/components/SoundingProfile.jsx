import { useState } from "react";
import { X, Minus, Download, ExternalLink } from "lucide-react";
import useDraggable from "../hooks/useDraggable";
import "./SoundingProfile.css";

const SA_URL = "https://shianmike.github.io/SoundingAnalysis/";

export default function SoundingProfile({ plot, loading, point, model, fhour = 0, onClose }) {
  const { offset, handleMouseDown } = useDraggable();
  const [collapsed, setCollapsed] = useState(false);
  const dragStyle = { transform: `translate(${offset.x}px, ${offset.y}px)` };

  const handleDownload = () => {
    if (!plot?.image) return;
    const link = document.createElement("a");
    link.download = `sounding_${model}_F${String(fhour).padStart(3, "0")}_${point?.lat?.toFixed(2)}N_${point?.lon?.toFixed(2)}W.png`;
    link.href = `data:image/png;base64,${plot.image}`;
    link.click();
  };

  const saLink = point
    ? `${SA_URL}?source=psu&lat=${point.lat.toFixed(2)}&lon=${point.lon.toFixed(2)}&model=${model || "gfs"}&fhour=${fhour}`
    : null;

  if (loading) {
    return (
      <div className="sounding-profile sounding-profile--wide card fade-in" style={dragStyle}>
        <div className="sounding-header drag-handle" onMouseDown={handleMouseDown}>
          <span>Preparing Sounding</span>
          <button className="btn-icon" onClick={onClose}><X size={14} /></button>
        </div>
        <div className="sounding-skeleton" aria-hidden="true">
          <div className="sounding-skeleton-top">
            <div className="sounding-skeleton-line skeleton" />
            <div className="sounding-skeleton-line skeleton" />
            <div className="sounding-skeleton-pill skeleton" />
          </div>
          <div className="sounding-skeleton-chart skeleton">
            <div className="sounding-skeleton-grid" />
          </div>
          <div className="sounding-skeleton-bottom">
            <div className="sounding-skeleton-chip skeleton" />
            <div className="sounding-skeleton-chip skeleton" />
            <div className="sounding-skeleton-chip skeleton" />
            <div className="sounding-skeleton-chip skeleton" />
          </div>
        </div>
      </div>
    );
  }

  if (!plot?.image) {
    return (
      <div className="sounding-profile card fade-in" style={dragStyle}>
        <div className="sounding-header drag-handle" onMouseDown={handleMouseDown}>
          <span>Sounding Profile</span>
          <button className="btn-icon" onClick={onClose}><X size={14} /></button>
        </div>
        <div className="sounding-empty">No sounding data available.</div>
      </div>
    );
  }

  return (
    <div className={`sounding-profile sounding-profile--wide card fade-in${collapsed ? " sounding-collapsed" : ""}`} style={dragStyle}>
      <div className="sounding-header drag-handle" onMouseDown={handleMouseDown}>
        <div>
          <strong>{model?.toUpperCase()}</strong> Sounding — F{String(fhour).padStart(3, "0")}
          <br />
          <span className="mono" style={{ fontSize: 11 }}>
            {point?.lat?.toFixed(2)}°N, {point?.lon?.toFixed(2)}°W
          </span>
        </div>
        <div style={{ display: "flex", gap: 2 }}>
          <button className="btn-icon" onClick={handleDownload} title="Download sounding image">
            <Download size={14} />
          </button>
          {saLink && (
            <a className="btn-icon" href={saLink} target="_blank" rel="noopener noreferrer" title="Open in Sounding Analysis">
              <ExternalLink size={14} />
            </a>
          )}
          <button className="btn-icon" onClick={() => setCollapsed(c => !c)}><Minus size={14} /></button>
          <button className="btn-icon" onClick={onClose}><X size={14} /></button>
        </div>
      </div>
      {!collapsed && (
        <div className="sounding-plot-view">
          <img
            src={`data:image/png;base64,${plot.image}`}
            alt="Sounding analysis"
            className="sounding-plot-img"
          />
        </div>
      )}
    </div>
  );
}
