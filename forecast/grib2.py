"""
Minimal GRIB2 decoder for NOMADS filter subsets.

Supports:
  - Grid template 3.0 (regular lat/lon)
  - Packing template 5.0 (simple packing)
  - Packing template 5.40 (JPEG2000 via Pillow)

Only what's needed for NOAA GFS/NAM/HRRR/RAP GRIB filter output.
No C library dependencies.  Optionally accelerated by Numba.
"""

import struct
import numpy as np

# Numba JIT — optional; graceful fallback to plain Python if unavailable.
try:
    from numba import njit
except ImportError:  # pragma: no cover
    def njit(*args, **kwargs):
        """No-op decorator when numba is not installed."""
        def _passthrough(fn):
            return fn
        if args and callable(args[0]):
            return args[0]
        return _passthrough


# ─── Numba-accelerated spatial differencing ────────────────

@njit(cache=True)
def _spatial_diff_order1(result, val_idx, ival1):
    """Reverse first-order spatial differencing (cumulative sum)."""
    result[0] = ival1
    for i in range(1, val_idx):
        result[i] = result[i] + result[i - 1]


@njit(cache=True)
def _spatial_diff_order2(result, val_idx, ival1, ival2):
    """Reverse second-order spatial differencing."""
    result[0] = ival1
    result[1] = ival2
    for i in range(2, val_idx):
        result[i] = result[i] + 2 * result[i - 1] - result[i - 2]


def _signed16(raw):
    """Decode a 2-byte GRIB2 sign-magnitude integer."""
    val = struct.unpack(">H", raw)[0]
    if val & 0x8000:
        return -(val & 0x7FFF)
    return val


def _signed32(raw):
    """Decode a 4-byte GRIB2 sign-magnitude integer."""
    val = struct.unpack(">I", raw)[0]
    if val & 0x80000000:
        return -(val & 0x7FFFFFFF)
    return val


def decode_grib2(data):
    """Decode all GRIB2 messages in a byte buffer.

    Returns a list of dicts, each with:
      category, parameter, lats (1D), lons (1D), values (2D: Nj×Ni)
    """
    messages = []
    pos = 0
    while pos < len(data) - 4:
        if data[pos : pos + 4] != b"GRIB":
            pos += 1
            continue
        msg_len = struct.unpack(">Q", data[pos + 8 : pos + 16])[0]
        msg = _decode_message(data[pos : pos + msg_len])
        if msg is not None:
            messages.append(msg)
        pos += msg_len
    return messages


def _decode_message(data):
    """Decode a single GRIB2 message."""
    grid = None
    packing = None
    product = None
    bitmap_indices = None
    packed_data = None

    pos = 16  # skip section 0 (indicator)
    while pos < len(data) - 4:
        if data[pos : pos + 4] == b"7777":
            break
        sec_len = struct.unpack(">I", data[pos : pos + 4])[0]
        if sec_len < 5:
            break
        sec_num = data[pos + 4]

        if sec_num == 3:
            grid = _parse_grid(data, pos)
        elif sec_num == 4:
            product = _parse_product(data, pos)
        elif sec_num == 5:
            packing = _parse_packing(data, pos)
        elif sec_num == 6:
            bitmap_indices = _parse_bitmap(data, pos, grid["npts"] if grid else 0)
        elif sec_num == 7:
            packed_data = data[pos + 5 : pos + sec_len]

        pos += sec_len

    if grid is None or packing is None or packed_data is None:
        return None

    # Decode packed values
    raw_values = _unpack_data(packed_data, packing)
    values = _apply_scaling(raw_values, packing)

    # Handle bitmap (missing data)
    npts = grid["npts"]
    if bitmap_indices is not None:
        full = np.full(npts, np.nan)
        full[bitmap_indices[: len(values)]] = values[: len(bitmap_indices)]
        values = full

    # Reshape to 2D
    Nj = grid["Nj"]
    Ni = grid["Ni"]
    if len(values) == npts:
        values_2d = values.reshape(Nj, Ni)
    else:
        values_2d = values.reshape(-1, Ni) if Ni > 0 else values.reshape(1, -1)

    # Build coordinate arrays
    if grid.get("projection") == "lambert":
        lats, lons, values_2d = _lambert_to_regular(grid, values_2d)
    else:
        lats = np.linspace(grid["la1"], grid["la2"], Nj)
        lo1, lo2 = grid["lo1"], grid["lo2"]
        # Handle longitude wrap-around (e.g. ECMWF: lo1=180, lo2=179.75)
        if lo2 < lo1:
            lo2 += 360.0
        lons = np.linspace(lo1, lo2, Ni)

    return {
        "category": product.get("category", -1) if product else -1,
        "parameter": product.get("parameter", -1) if product else -1,
        "lats": lats,
        "lons": lons,
        "values": values_2d,
    }


# ─── Section parsers ──────────────────────────────────────


def _parse_grid(data, pos):
    """Section 3 — grid definition. Dispatches by template number."""
    tmpl = struct.unpack(">H", data[pos + 12 : pos + 14])[0]
    if tmpl == 0:
        return _parse_grid_latlon(data, pos)
    elif tmpl == 30:
        return _parse_grid_lambert(data, pos)
    else:
        raise ValueError(f"Unsupported grid template 3.{tmpl}")


def _parse_grid_latlon(data, pos):
    """Template 3.0 — regular lat/lon grid."""
    npts = struct.unpack(">I", data[pos + 6 : pos + 10])[0]
    Ni = struct.unpack(">I", data[pos + 30 : pos + 34])[0]
    Nj = struct.unpack(">I", data[pos + 34 : pos + 38])[0]
    la1 = _signed32(data[pos + 46 : pos + 50]) / 1e6
    lo1 = struct.unpack(">I", data[pos + 50 : pos + 54])[0] / 1e6
    la2 = _signed32(data[pos + 55 : pos + 59]) / 1e6
    lo2 = struct.unpack(">I", data[pos + 59 : pos + 63])[0] / 1e6

    return {
        "npts": npts,
        "Ni": Ni,
        "Nj": Nj,
        "la1": la1,
        "la2": la2,
        "lo1": lo1,
        "lo2": lo2,
        "projection": "latlon",
    }


def _parse_grid_lambert(data, pos):
    """Template 3.30 — Lambert Conformal Conic projection."""
    npts = struct.unpack(">I", data[pos + 6 : pos + 10])[0]
    Nx = struct.unpack(">I", data[pos + 30 : pos + 34])[0]
    Ny = struct.unpack(">I", data[pos + 34 : pos + 38])[0]
    La1 = _signed32(data[pos + 38 : pos + 42]) / 1e6
    Lo1 = _signed32(data[pos + 42 : pos + 46]) / 1e6
    LaD = _signed32(data[pos + 47 : pos + 51]) / 1e6
    LoV = _signed32(data[pos + 51 : pos + 55]) / 1e6
    Dx = struct.unpack(">I", data[pos + 55 : pos + 59])[0] / 1e3  # mm → m
    Dy = struct.unpack(">I", data[pos + 59 : pos + 63])[0] / 1e3  # mm → m
    scan_mode = data[pos + 64]
    Latin1 = _signed32(data[pos + 65 : pos + 69]) / 1e6
    Latin2 = _signed32(data[pos + 69 : pos + 73]) / 1e6

    # Ensure Lo1 is in [0, 360] for consistent projection math
    if Lo1 < 0:
        Lo1 += 360.0
    if LoV < 0:
        LoV += 360.0

    return {
        "npts": npts,
        "Ni": Nx,
        "Nj": Ny,
        "la1": La1,
        "lo1": Lo1,
        "LaD": LaD,
        "LoV": LoV,
        "Dx": Dx,
        "Dy": Dy,
        "Latin1": Latin1,
        "Latin2": Latin2,
        "scan_mode": scan_mode,
        "projection": "lambert",
    }


def _lambert_to_regular(grid, values_2d):
    """Regrid Lambert Conformal data to a regular lat/lon grid.

    Uses forward/inverse projection math to compute lat/lon for each
    projected grid point, then bilinear-interpolates onto a regular grid.
    Returns (lats_1d, lons_1d, values_2d) with 1D coordinate arrays.
    """
    R = 6371229.0  # Earth radius (m) — GRIB2 earth shape 6

    phi1 = np.radians(grid["Latin1"])
    phi2 = np.radians(grid["Latin2"])
    LoV_deg = grid["LoV"]
    LoV_rad = np.radians(LoV_deg)

    # Cone constant
    if abs(grid["Latin1"] - grid["Latin2"]) < 1e-10:
        n = np.sin(phi1)
    else:
        n = (
            (np.log(np.cos(phi1)) - np.log(np.cos(phi2)))
            / (
                np.log(np.tan(np.pi / 4 + phi2 / 2))
                - np.log(np.tan(np.pi / 4 + phi1 / 2))
            )
        )

    F = np.cos(phi1) * np.tan(np.pi / 4 + phi1 / 2) ** n / n
    rho0 = R * F / np.tan(np.pi / 4 + np.radians(grid["LaD"]) / 2) ** n

    # Projection coordinates of first grid point
    La1_rad = np.radians(grid["la1"])
    Lo1_rad = np.radians(grid["lo1"])
    rho_1 = R * F / np.tan(np.pi / 4 + La1_rad / 2) ** n
    theta_1 = n * (Lo1_rad - LoV_rad)
    x0 = rho_1 * np.sin(theta_1)
    y0 = rho0 - rho_1 * np.cos(theta_1)

    Nx, Ny = grid["Ni"], grid["Nj"]
    Dx, Dy = grid["Dx"], grid["Dy"]

    # 2D projection coordinates
    x_arr = x0 + np.arange(Nx) * Dx
    y_arr = y0 + np.arange(Ny) * Dy
    xx, yy = np.meshgrid(x_arr, y_arr)

    # Inverse projection → lat/lon
    rho = np.sign(n) * np.sqrt(xx**2 + (rho0 - yy) ** 2)
    theta = np.arctan2(xx * np.sign(n), (rho0 - yy) * np.sign(n))
    lats_2d = np.degrees(2 * np.arctan((R * F / rho) ** (1 / n)) - np.pi / 2)
    lons_2d = np.degrees(theta / n) + LoV_deg
    lons_2d = ((lons_2d + 180) % 360) - 180  # normalise to [-180, 180]

    # Regular output grid at ~same resolution
    res = max(0.05, min(0.25, Dx / 111_000.0))
    lat_min, lat_max = float(np.nanmin(lats_2d)), float(np.nanmax(lats_2d))
    lon_min, lon_max = float(np.nanmin(lons_2d)), float(np.nanmax(lons_2d))
    reg_lats = np.arange(lat_min, lat_max + res / 2, res)
    reg_lons = np.arange(lon_min, lon_max + res / 2, res)

    # Forward-project every regular-grid point to fractional (i, j)
    reg_lon_2d, reg_lat_2d = np.meshgrid(reg_lons, reg_lats)
    lat_rad = np.radians(reg_lat_2d)
    # Use [0, 360] longitudes for consistency with LoV
    lon_360 = np.where(reg_lon_2d < 0, reg_lon_2d + 360, reg_lon_2d)
    lon_rad = np.radians(lon_360)

    rho_r = R * F / np.tan(np.pi / 4 + lat_rad / 2) ** n
    theta_r = n * (lon_rad - LoV_rad)
    x_r = rho_r * np.sin(theta_r)
    y_r = rho0 - rho_r * np.cos(theta_r)

    fi = (x_r - x0) / Dx
    fj = (y_r - y0) / Dy

    # Bilinear interpolation
    i0 = np.floor(fi).astype(int)
    j0 = np.floor(fj).astype(int)
    di = fi - i0
    dj = fj - j0
    valid = (i0 >= 0) & (i0 < Nx - 1) & (j0 >= 0) & (j0 < Ny - 1)
    i0c = np.clip(i0, 0, Nx - 2)
    j0c = np.clip(j0, 0, Ny - 2)

    result = (
        values_2d[j0c, i0c] * (1 - di) * (1 - dj)
        + values_2d[j0c, i0c + 1] * di * (1 - dj)
        + values_2d[j0c + 1, i0c] * (1 - di) * dj
        + values_2d[j0c + 1, i0c + 1] * di * dj
    )
    result[~valid] = np.nan

    return reg_lats, reg_lons, result


def _parse_product(data, pos):
    """Section 4 — product definition (discipline 0 assumed)."""
    return {"category": data[pos + 9], "parameter": data[pos + 10]}


def _parse_packing(data, pos):
    """Section 5 — data representation."""
    nvals = struct.unpack(">I", data[pos + 5 : pos + 9])[0]
    tmpl = struct.unpack(">H", data[pos + 9 : pos + 11])[0]
    ref = struct.unpack(">f", data[pos + 11 : pos + 15])[0]
    E = _signed16(data[pos + 15 : pos + 17])
    D = _signed16(data[pos + 17 : pos + 19])
    nbits = data[pos + 19]
    info = {
        "nvals": nvals,
        "template": tmpl,
        "ref": ref,
        "E": E,
        "D": D,
        "nbits": nbits,
    }
    # Complex packing (5.2) and complex packing with spatial differencing (5.3)
    if tmpl in (2, 3):
        # Octet 21: type of original field values (skip)
        info["group_split_method"] = data[pos + 21]
        info["missing_mgmt"] = data[pos + 22]
        info["primary_missing"] = struct.unpack(">f", data[pos + 23 : pos + 27])[0]
        info["secondary_missing"] = struct.unpack(">f", data[pos + 27 : pos + 31])[0]
        info["NG"] = struct.unpack(">I", data[pos + 31 : pos + 35])[0]
        info["ref_group_widths"] = data[pos + 35]
        info["nbits_group_widths"] = data[pos + 36]
        info["ref_group_lengths"] = struct.unpack(">I", data[pos + 37 : pos + 41])[0]
        info["length_increment"] = data[pos + 41]
        info["last_group_length"] = struct.unpack(">I", data[pos + 42 : pos + 46])[0]
        info["nbits_group_lengths"] = data[pos + 46]
        if tmpl == 3:
            info["spatial_order"] = data[pos + 47]
            info["extra_octets"] = data[pos + 48]
    # CCSDS/AEC packing (5.42)
    if tmpl == 42:
        # Octet 21: type of original field values (0=float, 1=int)
        info["ccsds_orig_type"] = data[pos + 20]
        # Octet 22: CCSDS compression options mask (maps to AEC flags)
        info["ccsds_options_mask"] = data[pos + 21]
        # Octet 23: block size (J)
        info["ccsds_block_size"] = data[pos + 22]
        # Octets 24-25: reference sample interval (RSI)
        info["ccsds_rsi"] = struct.unpack(">H", data[pos + 23 : pos + 25])[0]
    return info


def _parse_bitmap(data, pos, npts):
    """Section 6 — bitmap.  Returns array of valid-point indices or None."""
    indicator = data[pos + 5]
    if indicator == 255:
        return None  # all values present
    if indicator == 0 and npts > 0:
        nbytes = (npts + 7) // 8
        bitmap_bytes = data[pos + 6 : pos + 6 + nbytes]
        bits = np.unpackbits(np.frombuffer(bitmap_bytes, dtype=np.uint8))[:npts]
        return np.where(bits == 1)[0]
    return None


# ─── Unpacking ─────────────────────────────────────────────


def _unpack_data(packed_data, packing):
    """Decode packed data based on the packing template."""
    nbits = packing["nbits"]
    nvals = packing["nvals"]

    if nbits == 0:
        return np.zeros(nvals, dtype=np.float64)

    tmpl = packing["template"]

    if tmpl == 0:
        return _unpack_simple(packed_data, nvals, nbits)
    elif tmpl in (2, 3):
        return _unpack_complex(packed_data, packing)
    elif tmpl == 40:
        return _unpack_jpeg2000(packed_data, nvals)
    elif tmpl == 42:
        return _unpack_ccsds(packed_data, nvals, nbits, packing)
    else:
        raise ValueError(f"Unsupported packing template 5.{tmpl}")


def _unpack_simple(packed_data, nvals, nbits):
    """Template 5.0 — simple packing (bit extraction via numpy)."""
    all_bits = np.unpackbits(np.frombuffer(packed_data, dtype=np.uint8))
    total = nvals * nbits
    if total > len(all_bits):
        # pad with zeros if truncated
        all_bits = np.pad(all_bits, (0, total - len(all_bits)))
    bits = all_bits[:total].reshape(nvals, nbits)
    powers = np.int64(1) << np.arange(nbits - 1, -1, -1, dtype=np.int64)
    return (bits.astype(np.int64) * powers).sum(axis=1)


def _unpack_jpeg2000(packed_data, nvals):
    """Template 5.40 — JPEG2000 (decoded via Pillow)."""
    import io
    from PIL import Image

    img = Image.open(io.BytesIO(packed_data))
    arr = np.array(img).flatten().astype(np.int64)
    return arr[:nvals]


def _unpack_ccsds(packed_data, nvals, nbits, packing):
    """Template 5.42 — CCSDS/AEC (Adaptive Entropy Coding) decompression.

    Uses imagecodecs.aec_decode for decompression.
    """
    from imagecodecs import aec_decode

    block_size = packing.get("ccsds_block_size", 32)
    rsi = packing.get("ccsds_rsi", 128)
    # The options mask from the GRIB2 file maps directly to libaec AEC flags
    flags = packing.get("ccsds_options_mask", 14)

    decoded = aec_decode(
        packed_data,
        bitspersample=nbits,
        blocksize=block_size,
        rsi=rsi,
        flags=flags,
    )
    # Determine dtype from bits per sample
    if nbits <= 8:
        dtype = ">u1"
    elif nbits <= 16:
        dtype = ">u2"
    else:
        dtype = ">u4"
    arr = np.frombuffer(decoded, dtype=dtype)
    return arr[:nvals].astype(np.int64)


def _extract_n_bits(all_bits, bit_offset, n_values, n_bits):
    """Extract n_values integers of n_bits each from a bit array starting at bit_offset."""
    if n_bits == 0 or n_values == 0:
        return np.zeros(n_values, dtype=np.int64), bit_offset
    end = bit_offset + n_values * n_bits
    segment = all_bits[bit_offset:end]
    if len(segment) < n_values * n_bits:
        segment = np.pad(segment, (0, n_values * n_bits - len(segment)))
    bits = segment.reshape(n_values, n_bits)
    powers = np.int64(1) << np.arange(n_bits - 1, -1, -1, dtype=np.int64)
    values = (bits.astype(np.int64) * powers).sum(axis=1)
    return values, end


def _unpack_complex(packed_data, packing):
    """Template 5.2/5.3 — complex packing (with optional spatial differencing).

    Implements the GRIB2 complex packing scheme used by GFS/HRRR on AWS S3.
    """
    nvals = packing["nvals"]
    NG = packing["NG"]
    nbits_ref = packing["nbits"]
    ref_widths = packing["ref_group_widths"]
    nbits_widths = packing["nbits_group_widths"]
    ref_lengths = packing["ref_group_lengths"]
    length_incr = packing["length_increment"]
    last_group_len = packing["last_group_length"]
    nbits_lengths = packing["nbits_group_lengths"]

    spatial_order = packing.get("spatial_order", 0)
    extra_octets = packing.get("extra_octets", 0)

    data = packed_data
    offset = 0  # byte offset into data

    # ── 1. Read spatial differencing descriptors (template 5.3) ──
    ival1 = ival2 = 0
    overall_min = 0
    if spatial_order > 0 and extra_octets > 0:
        if spatial_order >= 1:
            ival1 = int.from_bytes(data[offset:offset + extra_octets], "big")
            offset += extra_octets
        if spatial_order >= 2:
            ival2 = int.from_bytes(data[offset:offset + extra_octets], "big")
            offset += extra_octets
        # Overall minimum: sign-magnitude (high bit = sign)
        raw_min = int.from_bytes(data[offset:offset + extra_octets], "big")
        offset += extra_octets
        sign_bit = 1 << (extra_octets * 8 - 1)
        if raw_min & sign_bit:
            overall_min = -(raw_min & (sign_bit - 1))
        else:
            overall_min = raw_min

    # ── 2. Unpack group reference values, widths, lengths from bit stream ──
    all_bits = np.unpackbits(np.frombuffer(data[offset:], dtype=np.uint8))
    bit_pos = 0

    # Group reference values (NG values, nbits_ref bits each)
    group_refs, bit_pos = _extract_n_bits(all_bits, bit_pos, NG, nbits_ref)
    bit_pos = ((bit_pos + 7) // 8) * 8  # byte-align

    # Group bit widths (NG values, nbits_widths bits each)
    group_widths, bit_pos = _extract_n_bits(all_bits, bit_pos, NG, nbits_widths)
    bit_pos = ((bit_pos + 7) // 8) * 8  # byte-align
    group_widths = group_widths + ref_widths

    # Group scaled lengths (NG values, nbits_lengths bits each)
    group_lengths, bit_pos = _extract_n_bits(all_bits, bit_pos, NG, nbits_lengths)
    bit_pos = ((bit_pos + 7) // 8) * 8  # byte-align
    group_lengths = group_lengths * length_incr + ref_lengths
    group_lengths[-1] = last_group_len

    # ── 3. Decode each group's values ──
    result = np.zeros(nvals, dtype=np.int64)
    val_idx = 0
    for g in range(NG):
        gw = int(group_widths[g])
        gl = int(group_lengths[g])
        if gl == 0:
            continue
        if gw == 0:
            # All values in this group equal the group reference
            result[val_idx:val_idx + gl] = group_refs[g]
        else:
            vals, bit_pos = _extract_n_bits(all_bits, bit_pos, gl, gw)
            result[val_idx:val_idx + gl] = vals + group_refs[g]
        val_idx += gl

    # ── 4. Add overall minimum ──
    result[:val_idx] += overall_min

    # ── 5. Reverse spatial differencing (template 5.3) ──
    if spatial_order == 1 and val_idx > 0:
        _spatial_diff_order1(result, val_idx, ival1)
    elif spatial_order == 2 and val_idx > 1:
        _spatial_diff_order2(result, val_idx, ival1, ival2)

    return result[:nvals]


def _apply_scaling(raw_values, packing):
    """Y = (R + X · 2^E) · 10^(−D)"""
    R = packing["ref"]
    E = packing["E"]
    D = packing["D"]
    return (R + raw_values.astype(np.float64) * (2.0**E)) * (10.0 ** (-D))
