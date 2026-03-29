import { Sun, Moon, Eye } from "lucide-react";
import "./Header.css";

export default function Header({ theme, toggleTheme, colorblind, toggleColorblind, model, parameter, validTime, overlayOpacity, setOverlayOpacity }) {
  const paramLabel = parameter?.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase()) || "";
  return (
    <header className="header">
      <div className="header-left">
        <div className="header-meta">
          <span className="pill">{model?.toUpperCase()}</span>
          <span className="header-param">{paramLabel}</span>
          <span className="header-time mono">{validTime}</span>
        </div>
      </div>
      <div className="header-actions">
        <div className="opacity-control" title="Overlay opacity">
          <span className="opacity-label mono">{Math.round((overlayOpacity ?? 0.5) * 100)}%</span>
          <input
            type="range"
            className="opacity-slider"
            min="0" max="100" step="5"
            value={Math.round((overlayOpacity ?? 0.5) * 100)}
            onChange={e => setOverlayOpacity(Number(e.target.value) / 100)}
          />
        </div>
        <button className="btn-icon" onClick={toggleColorblind} title="Toggle color-blind mode">
          <Eye size={14} style={colorblind ? { color: "var(--accent)" } : {}} />
        </button>
        <button className="btn-icon" onClick={toggleTheme} title="Toggle theme">
          {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
        </button>
      </div>
    </header>
  );
}
