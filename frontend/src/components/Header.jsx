import { Sun, Moon, Eye, TrendingUp, GitCompareArrows, Download, Columns2, Scissors, Thermometer, Wind, BarChart3, Cpu } from "lucide-react";
import "./Header.css";

export default function Header({ theme, toggleTheme, colorblind, toggleColorblind, model, parameter, validTime, overlayOpacity, setOverlayOpacity, showContours, setShowContours, showWindParticles, setShowWindParticles, useWebGL, setUseWebGL, diffMode, setDiffMode, onExport, compareMode, setCompareMode, compareModel, setCompareModel, models, onCrossSection, showSounding, setShowSounding, showMeteogram, setShowMeteogram, showEnsemble, setShowEnsemble }) {
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
        <button className="btn-icon" onClick={() => setShowContours(v => !v)} title="Toggle contour lines">
          <TrendingUp size={14} style={showContours ? { color: "var(--accent)" } : {}} />
        </button>
        <button className="btn-icon" onClick={() => setShowWindParticles(v => !v)} title="Toggle wind particle animation">
          <Wind size={14} style={showWindParticles ? { color: "var(--accent)" } : {}} />
        </button>
        <button className="btn-icon" onClick={() => setUseWebGL(v => !v)} title="Toggle WebGL rendering (Deck.gl)">
          <Cpu size={14} style={useWebGL ? { color: "var(--accent)" } : {}} />
        </button>
        <button className="btn-icon" onClick={() => setDiffMode(v => !v)} title="Toggle difference mode (change from previous frame)">
          <GitCompareArrows size={14} style={diffMode ? { color: "var(--accent)" } : {}} />
        </button>
        <button className="btn-icon" onClick={onCrossSection} title="Cross-section tool">
          <Scissors size={14} />
        </button>
        <span className="header-sep" />
        <button className="btn-icon" onClick={() => setShowSounding(v => !v)} title="Toggle sounding (Skew-T)">
          <Thermometer size={14} style={showSounding ? { color: "var(--accent)" } : {}} />
        </button>
        <button className="btn-icon" onClick={() => setShowMeteogram(v => !v)} title="Toggle meteogram">
          <BarChart3 size={14} style={showMeteogram ? { color: "var(--accent)" } : {}} />
        </button>
        <button className="btn-icon" onClick={() => setShowEnsemble(v => !v)} title="Toggle ensemble plume">
          <Wind size={14} style={showEnsemble ? { color: "var(--accent)" } : {}} />
        </button>
        <span className="header-sep" />
        <button className="btn-icon" onClick={() => setCompareMode(v => !v)} title="Toggle model comparison">
          <Columns2 size={14} style={compareMode ? { color: "var(--accent)" } : {}} />
        </button>
        {compareMode && models && (
          <select className="compare-select" value={compareModel} onChange={e => setCompareModel(e.target.value)}>
            {Object.entries(models).filter(([k]) => k !== model).map(([k, v]) => (
              <option key={k} value={k}>{v.label || k.toUpperCase()}</option>
            ))}
          </select>
        )}
        <button className="btn-icon" onClick={toggleColorblind} title="Toggle color-blind mode">
          <Eye size={14} style={colorblind ? { color: "var(--accent)" } : {}} />
        </button>
        <button className="btn-icon" onClick={toggleTheme} title="Toggle theme">
          {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
        </button>
        <button className="btn-icon" onClick={onExport} title="Export current frame as PNG">
          <Download size={14} />
        </button>
      </div>
    </header>
  );
}
