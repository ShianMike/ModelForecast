import { useEffect, useMemo } from "react";
import { Play, Pause, SkipBack, SkipForward, ChevronLeft, ChevronRight } from "lucide-react";
import "./AnimationControls.css";

const SPEEDS = [0.5, 1, 2, 4];

/* Compute valid date/time label from current UTC + forecast hour offset */
function validZuluLabel(fhour, validTime) {
  /* If backend already gave a real date string (not just "Fxxx"), use it */
  if (validTime && !/^F\d+$/.test(validTime)) return validTime;
  /* Otherwise compute: assume latest model init = most recent 00/06/12/18Z */
  const now = new Date();
  const utcH = now.getUTCHours();
  const initH = utcH >= 18 ? 18 : utcH >= 12 ? 12 : utcH >= 6 ? 6 : 0;
  const init = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), initH));
  const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const valid = new Date(init.getTime() + fhour * 3600_000);
  const wday = DAYS[valid.getUTCDay()];
  const mon = String(valid.getUTCMonth() + 1).padStart(2, "0");
  const day = String(valid.getUTCDate()).padStart(2, "0");
  const hh = String(valid.getUTCHours()).padStart(2, "0");
  return `${wday} ${mon}/${day} ${hh}Z`;
}

export default function AnimationControls({
  fhour, setFhour, maxFhour, step,
  playing, setPlaying,
  speed, setSpeed,
  validTime, loading,
}) {
  const stepBack = () => setFhour(h => Math.max(0, h - step));
  const stepFwd  = () => setFhour(h => Math.min(maxFhour, h + step));
  const toStart  = () => { setPlaying(false); setFhour(0); };
  const toEnd    = () => { setPlaying(false); setFhour(maxFhour); };

  /* Global keyboard shortcuts */
  useEffect(() => {
    const onKey = (e) => {
      /* Ignore when user is typing in an input/textarea */
      const tag = e.target.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;

      switch (e.key) {
        case "ArrowLeft":
          e.preventDefault();
          setFhour(h => Math.max(0, h - step));
          break;
        case "ArrowRight":
          e.preventDefault();
          setFhour(h => Math.min(maxFhour, h + step));
          break;
        case " ":
          e.preventDefault();
          setPlaying(p => !p);
          break;
        case "Home":
          e.preventDefault();
          setPlaying(false);
          setFhour(0);
          break;
        case "End":
          e.preventDefault();
          setPlaying(false);
          setFhour(maxFhour);
          break;
        default:
          break;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step, maxFhour, setFhour, setPlaying]);

  const zuluLabel = useMemo(() => validZuluLabel(fhour, validTime), [fhour, validTime]);

  return (
    <div className="anim-controls">
      <div className="anim-row">
        <button className="btn-icon" onClick={toStart} title="Go to start"><SkipBack size={14} /></button>
        <button className="btn-icon" onClick={stepBack} title="Step backward"><ChevronLeft size={14} /></button>
        <button className={`btn-icon anim-play ${playing ? "active" : ""}`} onClick={() => setPlaying(p => !p)} title={playing ? "Pause" : "Play"}>
          {playing ? <Pause size={14} /> : <Play size={14} />}
        </button>
        <button className="btn-icon" onClick={stepFwd} title="Step forward"><ChevronRight size={14} /></button>
        <button className="btn-icon" onClick={toEnd} title="Go to end"><SkipForward size={14} /></button>

        <div className="anim-time">
          <span className="anim-fhour mono">F{String(fhour).padStart(3, "0")}</span>
          <span className="anim-valid mono">{zuluLabel}</span>
        </div>

        <div className="anim-speed">
          {SPEEDS.map(s => (
            <button
              key={s}
              className={`btn anim-speed-btn ${speed === s ? "active" : ""}`}
              onClick={() => setSpeed(s)}
            >
              {s}×
            </button>
          ))}
        </div>
      </div>

      <input
        type="range"
        className="anim-slider"
        min={0}
        max={maxFhour}
        step={step}
        value={fhour}
        onChange={e => setFhour(Number(e.target.value))}
      />
    </div>
  );
}
