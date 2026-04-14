"""
Helpers for trimming oversized gridded map payloads before they are cached
or serialized back to the frontend.
"""

import math

import numpy as np

MAX_MAP_CELLS = 75_000
MAX_MAP_SIDE = 400


def _normalize_bbox(bbox):
    if not bbox:
        return None
    lat_min = min(float(bbox["lat_min"]), float(bbox["lat_max"]))
    lat_max = max(float(bbox["lat_min"]), float(bbox["lat_max"]))
    lon_min = min(float(bbox["lon_min"]), float(bbox["lon_max"]))
    lon_max = max(float(bbox["lon_min"]), float(bbox["lon_max"]))
    return {
        "lat_min": lat_min,
        "lat_max": lat_max,
        "lon_min": lon_min,
        "lon_max": lon_max,
    }


def _coord_indices(coords, lower, upper):
    if coords.size == 0:
        return np.asarray([], dtype=int)
    idx = np.flatnonzero((coords >= lower) & (coords <= upper))
    if idx.size:
        return idx.astype(int, copy=False)
    nearest = int(np.argmin(np.abs(coords - ((lower + upper) / 2.0))))
    return np.asarray([nearest], dtype=int)


def _thin_indices(indices, step):
    if indices.size <= 1 or step <= 1:
        return indices
    sampled = indices[::step]
    if sampled[-1] != indices[-1]:
        sampled = np.concatenate([sampled, indices[-1:]])
    return sampled.astype(int, copy=False)


def _take_2d(grid, row_idx, col_idx):
    if grid is None:
        return None
    if isinstance(grid, np.ndarray):
        return grid[np.ix_(row_idx, col_idx)]
    return [[grid[int(i)][int(j)] for j in col_idx] for i in row_idx]


def _to_float_list(coords):
    if isinstance(coords, np.ndarray):
        return [round(float(x), 4) for x in coords.tolist()]
    return [round(float(x), 4) for x in coords]


def _to_nested_list(grid):
    if grid is None:
        return None
    if isinstance(grid, np.ndarray):
        return grid.tolist()
    return grid


def crop_and_thin_grid(
    lats,
    lons,
    values,
    bbox=None,
    u_component=None,
    v_component=None,
    max_cells=MAX_MAP_CELLS,
    max_side=MAX_MAP_SIDE,
):
    lat_arr = np.asarray(lats, dtype=float)
    lon_arr = np.asarray(lons, dtype=float)

    row_idx = np.arange(lat_arr.size, dtype=int)
    col_idx = np.arange(lon_arr.size, dtype=int)

    bounds = _normalize_bbox(bbox)
    if bounds is not None:
        row_idx = _coord_indices(lat_arr, bounds["lat_min"], bounds["lat_max"])
        col_idx = _coord_indices(lon_arr, bounds["lon_min"], bounds["lon_max"])

    total_cells = max(1, row_idx.size * col_idx.size)
    stride = 1
    if total_cells > max_cells:
        stride = max(stride, math.ceil(math.sqrt(total_cells / max_cells)))
    if row_idx.size > max_side:
        stride = max(stride, math.ceil(row_idx.size / max_side))
    if col_idx.size > max_side:
        stride = max(stride, math.ceil(col_idx.size / max_side))

    row_idx = _thin_indices(row_idx, stride)
    col_idx = _thin_indices(col_idx, stride)

    return (
        lat_arr[row_idx],
        lon_arr[col_idx],
        _take_2d(values, row_idx, col_idx),
        _take_2d(u_component, row_idx, col_idx),
        _take_2d(v_component, row_idx, col_idx),
    )


def thin_grid_result(result, bbox=None, max_cells=MAX_MAP_CELLS, max_side=MAX_MAP_SIDE):
    if not isinstance(result, dict):
        return result

    lats = result.get("lats")
    lons = result.get("lons")
    values = result.get("values")
    if lats is None or lons is None or values is None or len(lats) == 0 or len(lons) == 0:
        return result

    first_row = values[0] if len(values) > 0 else None
    if first_row is None or not isinstance(first_row, (list, tuple, np.ndarray)):
        return result

    trimmed_lats, trimmed_lons, trimmed_values, trimmed_u, trimmed_v = crop_and_thin_grid(
        lats,
        lons,
        values,
        bbox=bbox,
        u_component=result.get("u_component"),
        v_component=result.get("v_component"),
        max_cells=max_cells,
        max_side=max_side,
    )

    updated = {**result}
    updated["lats"] = _to_float_list(trimmed_lats)
    updated["lons"] = _to_float_list(trimmed_lons)
    updated["values"] = _to_nested_list(trimmed_values)

    if trimmed_u is not None and trimmed_v is not None:
        updated["u_component"] = _to_nested_list(trimmed_u)
        updated["v_component"] = _to_nested_list(trimmed_v)

    return updated
