function computeDiffValues(values, prevValues) {
  if (!Array.isArray(values) || !Array.isArray(prevValues)) {
    return null;
  }

  const is2D = Array.isArray(values[0]);
  if (is2D) {
    return values.map((row, i) =>
      row.map((value, j) => {
        const prevValue = prevValues[i]?.[j];
        if (!Number.isFinite(value) || !Number.isFinite(prevValue)) {
          return null;
        }
        return value - prevValue;
      })
    );
  }

  return values.map((value, i) => {
    const prevValue = prevValues[i];
    if (!Number.isFinite(value) || !Number.isFinite(prevValue)) {
      return null;
    }
    return value - prevValue;
  });
}

self.onmessage = (event) => {
  const payload = event.data || {};
  const diffValues = computeDiffValues(payload.values, payload.prevValues);
  self.postMessage({ id: payload.id, diffValues });
};
