const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export function formatZulu(dt) {
  const wday = DAYS[dt.getUTCDay()];
  const mon = String(dt.getUTCMonth() + 1).padStart(2, "0");
  const day = String(dt.getUTCDate()).padStart(2, "0");
  const hh = String(dt.getUTCHours()).padStart(2, "0");
  return `${wday} ${mon}/${day} ${hh}Z`;
}

export function parseValidTime(validTime) {
  if (!validTime || /^F\d+$/i.test(validTime)) return null;
  const str = String(validTime).trim();

  const displayMatch = str.match(/^(Sun|Mon|Tue|Wed|Thu|Fri|Sat)\s+(\d{2})\/(\d{2})\s+(\d{2})Z$/i);
  if (displayMatch) {
    const now = new Date();
    const year = now.getUTCFullYear();
    const month = Number(displayMatch[2]);
    const day = Number(displayMatch[3]);
    const hour = Number(displayMatch[4]);
    return new Date(Date.UTC(year, month - 1, day, hour));
  }

  const normalized = str.endsWith(" UTC")
    ? str.replace(" UTC", "Z").replace(" ", "T")
    : str;
  const dt = new Date(normalized);
  return Number.isNaN(dt.getTime()) ? null : dt;
}

export function parseRunInit(run) {
  if (!run) return null;
  const m = String(run).match(/^(\d{4})(\d{2})(\d{2})\/(\d{2})z$/i);
  if (!m) return null;
  return new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4])));
}

export function validZuluLabel(fhour, validTime, run) {
  const parsedValid = parseValidTime(validTime);
  if (parsedValid) return formatZulu(parsedValid);

  const initFromRun = parseRunInit(run);
  if (initFromRun) {
    const valid = new Date(initFromRun.getTime() + Number(fhour || 0) * 3600_000);
    return formatZulu(valid);
  }

  // Final fallback when backend does not provide valid_time/run metadata.
  const now = new Date();
  const utcH = now.getUTCHours();
  const initH = utcH >= 18 ? 18 : utcH >= 12 ? 12 : utcH >= 6 ? 6 : 0;
  const init = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), initH));
  const valid = new Date(init.getTime() + Number(fhour || 0) * 3600_000);
  return formatZulu(valid);
}
