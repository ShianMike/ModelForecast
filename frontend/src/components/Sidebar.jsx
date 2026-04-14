import { useState } from "react";
import { Map, Layers, Wind, CloudRain, Globe, Zap, Cloud, Compass, RefreshCw, Snowflake, Leaf, ExternalLink, Plus, Trash2 } from "lucide-react";
import ParameterPicker from "./ParameterPicker";
import "./Sidebar.css";

const MODEL_ICONS = {
  gfs:   Globe,
  hrrr:  Zap,
  ecmwf: Cloud,
  icon:  Compass,
  nam:   Map,
  rap:   RefreshCw,
  jma:   Snowflake,
  gem:   Leaf,
};

export default function Sidebar({
  models, parameterCategories,
  selectedModel, setSelectedModel,
  selectedParam, setSelectedParam,
  region, setRegion, regions,
  onSaveRegion, onDeleteRegion,
}) {
  const [tab, setTab] = useState("models");
  const [saveName, setSaveName] = useState("");

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <Wind size={18} />
        <span>Model Forecast</span>
      </div>

      {/* Tab navigation */}
      <div className="sidebar-tabs">
        <button className={`sidebar-tab ${tab === "models" ? "active" : ""}`} onClick={() => setTab("models")}>
          <Layers size={13} /> Models
        </button>
        <button className={`sidebar-tab ${tab === "params" ? "active" : ""}`} onClick={() => setTab("params")}>
          <CloudRain size={13} /> Parameters
        </button>
        <button className={`sidebar-tab ${tab === "region" ? "active" : ""}`} onClick={() => setTab("region")}>
          <Map size={13} /> Region
        </button>
      </div>

      <div className="sidebar-content">
        {/* Models tab */}
        {tab === "models" && (
          <div className="sidebar-section fade-in">
            <div className="section-label">Select Model</div>
            <div className="model-grid">
              {Object.entries(models).map(([key, info]) => (
                <button
                  key={key}
                  className={`btn model-btn ${selectedModel === key ? "active" : ""}`}
                  onClick={() => setSelectedModel(key)}
                >
                  <span className="model-icon">{(() => { const Icon = MODEL_ICONS[key] || Globe; return <Icon size={16} />; })()}</span>
                  <span className="model-name">{key.toUpperCase()}</span>
                  <span className="model-res">{info.resolution || ''}</span>
                </button>
              ))}
            </div>
            {models[selectedModel] && (
              <div className="model-info">
                <div className="model-info-name">{models[selectedModel].name}</div>
                <div className="model-info-detail">
                  Resolution: {models[selectedModel].resolution} ·
                  Range: {models[selectedModel].maxHour}h ·
                  Step: {models[selectedModel].step}h
                </div>
              </div>
            )}
          </div>
        )}

        {/* Parameters tab */}
        {tab === "params" && (
          <div className="sidebar-section fade-in">
            <ParameterPicker
              categories={parameterCategories}
              selected={selectedParam}
              onSelect={setSelectedParam}
            />
          </div>
        )}

        {/* Region tab */}
        {tab === "region" && (
          <div className="sidebar-section fade-in">
            <div className="section-label">Region Presets</div>
            <div className="region-grid">
              {Object.entries(regions).map(([key, r]) => (
                <div key={key} className="region-item">
                  <button
                    className={`btn ${region === key ? "active" : ""}`}
                    onClick={() => setRegion(key)}
                    style={{ flex: 1 }}
                  >
                    {r.label}
                  </button>
                  {r.custom && (
                    <button
                      className="btn-icon region-delete"
                      onClick={() => onDeleteRegion(key)}
                      title="Delete custom region"
                    >
                      <Trash2 size={12} />
                    </button>
                  )}
                </div>
              ))}
            </div>
            <div className="section-label" style={{ marginTop: 12 }}>Save Current View</div>
            <div className="region-save-row">
              <input
                className="input"
                placeholder="Region name…"
                value={saveName}
                onChange={e => setSaveName(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter" && saveName.trim()) { onSaveRegion(saveName.trim()); setSaveName(""); } }}
              />
              <button
                className="btn btn-primary"
                disabled={!saveName.trim()}
                onClick={() => { onSaveRegion(saveName.trim()); setSaveName(""); }}
              >
                <Plus size={14} />
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="sidebar-footer">
        <a
          href="https://shianmike.github.io/SoundingAnalysis/"
          target="_blank"
          rel="noopener noreferrer"
          className="btn sidebar-link-btn"
        >
          <ExternalLink size={14} />
          Sounding Analysis
        </a>
      </div>
    </aside>
  );
}
