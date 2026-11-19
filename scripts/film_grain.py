"""AV1 film grain synthesis (spec 8.8.5.3), ported from dav1d's C reference
(src/filmgrain_tmpl.c, src/fg_apply_tmpl.c) for 8-bit 4:2:0 content.

Deterministic given (film_grain_params, grain seed).
"""
import numpy as np

from gaussian_table import GAUSSIAN_SEQUENCE

GRAIN_WIDTH = 82
GRAIN_HEIGHT = 73
SUB_GRAIN_WIDTH = 44
SUB_GRAIN_HEIGHT = 38
FG_BLOCK_SIZE = 32
SCALING_SIZE = 256

_GAUSS = np.asarray(GAUSSIAN_SEQUENCE, dtype=np.int32)


def _round2(x, shift):
    return (x + (1 << (shift - 1))) >> shift


def get_random_number(bits, state):
    bit = ((state >> 0) ^ (state >> 1) ^ (state >> 3) ^ (state >> 12)) & 1
    state = (state >> 1) | (bit << 15)
    return (state >> (16 - bits)) & ((1 << bits) - 1), state


def generate_grain_y(data):
    seed = data["seed"]
    shift = 4 + data["grain_scale_shift"]
    buf = np.zeros((GRAIN_HEIGHT, GRAIN_WIDTH), dtype=np.int32)
    for y in range(GRAIN_HEIGHT):
        for x in range(GRAIN_WIDTH):
            v, seed = get_random_number(11, seed)
            buf[y, x] = _round2(int(_GAUSS[v]), shift)
    _ar_filter(buf, data["ar_coeffs_y"], data["ar_coeff_lag"],
               data["ar_coeff_shift"], GRAIN_HEIGHT, GRAIN_WIDTH)
    return buf


def generate_grain_uv(data, buf_y, uv, subx=1, suby=1):
    seed = data["seed"] ^ (0x49d8 if uv else 0xb524)
    shift = 4 + data["grain_scale_shift"]
    cw = SUB_GRAIN_WIDTH if subx else GRAIN_WIDTH
    ch = SUB_GRAIN_HEIGHT if suby else GRAIN_HEIGHT
    buf = np.zeros((ch, cw), dtype=np.int32)
    for y in range(ch):
        for x in range(cw):
            v, seed = get_random_number(11, seed)
            buf[y, x] = _round2(int(_GAUSS[v]), shift)
    _ar_filter(buf, data["ar_coeffs_uv"][uv], data["ar_coeff_lag"],
               data["ar_coeff_shift"], ch, cw,
               buf_y=buf_y, subx=subx, suby=suby,
               use_luma=data["num_y_points"] > 0)
    return buf


def _ar_filter(buf, coeffs, lag, coeff_shift, ch, cw,
               buf_y=None, subx=0, suby=0, use_luma=False):
    pad = 3
    for y in range(pad, ch):
        for x in range(pad, cw - pad):
            ci = 0
            s = 0
            for dy in range(-lag, 1):
                done = False
                for dx in range(-lag, lag + 1):
                    if dx == 0 and dy == 0:
                        if use_luma:
                            lx = ((x - pad) << subx) + pad
                            ly = ((y - pad) << suby) + pad
                            luma = 0
                            for i in range(suby + 1):
                                for j in range(subx + 1):
                                    luma += int(buf_y[ly + i, lx + j])
                            luma = _round2(luma, subx + suby)
                            s += luma * coeffs[ci]
                        done = True
                        break
                    s += coeffs[ci] * int(buf[y + dy, x + dx])
                    ci += 1
                if done:
                    break
            grain = int(buf[y, x]) + _round2(s, coeff_shift)
            buf[y, x] = max(-128, min(127, grain))


def generate_scaling(points):
    scaling = np.zeros(SCALING_SIZE, dtype=np.int32)
    if len(points) == 0:
        return scaling
    scaling[:points[0][0]] = points[0][1]
    for i in range(len(points) - 1):
        bx, by = points[i]
        ex, ey = points[i + 1]
        dx, dy = ex - bx, ey - by
        delta = dy * ((0x10000 + (dx >> 1)) // dx)
        d = 0x8000
        for x in range(dx):
            scaling[bx + x] = by + (d >> 16)
            d += delta
    scaling[points[-1][0]:] = points[-1][1]
    return scaling


def _sample_lut(grain_lut, offsets, subx, suby, bx, by, x, y):
    randval = offsets[bx][by]
    offx = 3 + (2 >> subx) * (3 + (randval >> 4))
    offy = 3 + (2 >> suby) * (3 + (randval & 0xF))
    return int(grain_lut[offy + y + (FG_BLOCK_SIZE >> suby) * by,
                         offx + x + (FG_BLOCK_SIZE >> subx) * bx])


_W_LUMA = ((27, 17), (17, 27))
_W_CHROMA = ((23, 22), (0, 0))


def apply_grain_y(dst, src, data, scaling, grain_lut, row_num, y0, bh):
    """Port of fgy_32x32xn_c for one 32-row band (8-bit)."""
    h, w = src.shape
    rows = 1 + (1 if (data["overlap_flag"] and row_num > 0) else 0)
    lo, hi = (16, 235) if data["clip_to_restricted_range"] else (0, 255)
    seeds = []
    for i in range(rows):
        s = data["seed"]
        s ^= (((row_num - i) * 37 + 178) & 0xFF) << 8
        s ^= (((row_num - i) * 173 + 105) & 0xFF)
        seeds.append(s)
    offsets = [[0, 0], [0, 0]]
    for bx in range(0, w, FG_BLOCK_SIZE):
        bw = min(FG_BLOCK_SIZE, w - bx)
        if data["overlap_flag"] and bx:
            for i in range(rows):
                offsets[1][i] = offsets[0][i]
        for i in range(rows):
            offsets[0][i], seeds[i] = get_random_number(8, seeds[i])
        ystart = min(2, bh) if (data["overlap_flag"] and row_num) else 0
        xstart = min(2, bw) if (data["overlap_flag"] and bx) else 0
        for yy in range(bh):
            for xx in range(bw):
                if yy < ystart and xx < xstart:
                    top = _sample_lut(grain_lut, offsets, 0, 0, 0, 1, xx, yy)
                    old = _sample_lut(grain_lut, offsets, 0, 0, 1, 1, xx, yy)
                    top = max(-128, min(127, _round2(
                        old * _W_LUMA[xx][0] + top * _W_LUMA[xx][1], 5)))
                    g = _sample_lut(grain_lut, offsets, 0, 0, 0, 0, xx, yy)
                    old = _sample_lut(grain_lut, offsets, 0, 0, 1, 0, xx, yy)
                    g = max(-128, min(127, _round2(
                        old * _W_LUMA[xx][0] + g * _W_LUMA[xx][1], 5)))
                    g = max(-128, min(127, _round2(
                        top * _W_LUMA[yy][0] + g * _W_LUMA[yy][1], 5)))
                elif yy < ystart:
                    g = _sample_lut(grain_lut, offsets, 0, 0, 0, 0, xx, yy)
                    old = _sample_lut(grain_lut, offsets, 0, 0, 0, 1, xx, yy)
                    g = max(-128, min(127, _round2(
                        old * _W_LUMA[yy][0] + g * _W_LUMA[yy][1], 5)))
                elif xx < xstart:
                    g = _sample_lut(grain_lut, offsets, 0, 0, 0, 0, xx, yy)
                    old = _sample_lut(grain_lut, offsets, 0, 0, 1, 0, xx, yy)
                    g = max(-128, min(127, _round2(
                        old * _W_LUMA[xx][0] + g * _W_LUMA[xx][1], 5)))
                else:
                    g = _sample_lut(grain_lut, offsets, 0, 0, 0, 0, xx, yy)
                sv = int(src[y0 + yy, bx + xx])
                noise = _round2(int(scaling[sv]) * g, data["scaling_shift"])
                dst[y0 + yy, bx + xx] = max(lo, min(hi, sv + noise))


def apply_grain_uv(dst, src, luma, data, scaling, grain_lut, row_num,
                   y0, bh, uv, sx=1, sy=1):
    """Port of fguv_32x32xn_c (4:2:0, 8-bit)."""
    h, w = src.shape
    rows = 1 + (1 if (data["overlap_flag"] and row_num > 0) else 0)
    lo, hi = (16, 240) if data["clip_to_restricted_range"] else (0, 255)
    seeds = []
    for i in range(rows):
        s = data["seed"]
        s ^= (((row_num - i) * 37 + 178) & 0xFF) << 8
        s ^= (((row_num - i) * 173 + 105) & 0xFF)
        seeds.append(s)
    offsets = [[0, 0], [0, 0]]
    wtab = _W_CHROMA if sx else _W_LUMA
    csfl = data["chroma_scaling_from_luma"]
    for bx in range(0, w, FG_BLOCK_SIZE >> sx):
        bw = min(FG_BLOCK_SIZE >> sx, w - bx)
        if data["overlap_flag"] and bx:
            for i in range(rows):
                offsets[1][i] = offsets[0][i]
        for i in range(rows):
            offsets[0][i], seeds[i] = get_random_number(8, seeds[i])
        ystart = min(2 >> sy, bh) if (data["overlap_flag"] and row_num) else 0
        xstart = min(2 >> sx, bw) if (data["overlap_flag"] and bx) else 0
        for yy in range(bh):
            for xx in range(bw):
                if yy < ystart and xx < xstart:
                    top = _sample_lut(grain_lut, offsets, sx, sy, 0, 1, xx, yy)
                    old = _sample_lut(grain_lut, offsets, sx, sy, 1, 1, xx, yy)
                    top = max(-128, min(127, _round2(
                        old * wtab[xx][0] + top * wtab[xx][1], 5)))
                    g = _sample_lut(grain_lut, offsets, sx, sy, 0, 0, xx, yy)
                    old = _sample_lut(grain_lut, offsets, sx, sy, 1, 0, xx, yy)
                    g = max(-128, min(127, _round2(
                        old * wtab[xx][0] + g * wtab[xx][1], 5)))
                    g = max(-128, min(127, _round2(
                        top * wtab[yy][0] + g * wtab[yy][1], 5)))
                elif yy < ystart:
                    g = _sample_lut(grain_lut, offsets, sx, sy, 0, 0, xx, yy)
                    old = _sample_lut(grain_lut, offsets, sx, sy, 0, 1, xx, yy)
                    g = max(-128, min(127, _round2(
                        old * wtab[yy][0] + g * wtab[yy][1], 5)))
                elif xx < xstart:
                    g = _sample_lut(grain_lut, offsets, sx, sy, 0, 0, xx, yy)
                    old = _sample_lut(grain_lut, offsets, sx, sy, 1, 0, xx, yy)
                    g = max(-128, min(127, _round2(
                        old * wtab[xx][0] + g * wtab[xx][1], 5)))
                else:
                    g = _sample_lut(grain_lut, offsets, sx, sy, 0, 0, xx, yy)
                ly = (y0 + yy) << sy
                lx = (bx + xx) << sx
                avg = int(luma[ly, lx])
                if sx:
                    avg = (avg + int(luma[ly, lx + 1]) + 1) >> 1
                val = avg
                if not csfl:
                    combined = (avg * data["uv_mult_luma"][uv]
                                + int(src[y0 + yy, bx + xx])
                                * data["uv_mult"][uv])
                    val = max(0, min(255, (combined >> 6)
                                     + data["uv_offset"][uv]))
                noise = _round2(int(scaling[val]) * g, data["scaling_shift"])
                sv = int(src[y0 + yy, bx + xx])
                dst[y0 + yy, bx + xx] = max(lo, min(hi, sv + noise))


def apply_grain_frame(frame, data):
    """Apply film grain to a 4:2:0 8-bit frame (tuple of Y, U, V arrays).

 Returns a new (Y, U, V) tuple with grain synthesized per `data`.
 """
    y, u, v = [np.ascontiguousarray(p, dtype=np.uint8) for p in frame]
    dy = y.copy()
    du = u.copy()
    dv = v.copy()
    lut_y = generate_grain_y(data)
    lut_u = generate_grain_uv(data, lut_y, 0)
    lut_v = generate_grain_uv(data, lut_y, 1)
    sc_y = generate_scaling(data["y_points"])
    sc_u = generate_scaling(data["uv_points"][0])
    sc_v = generate_scaling(data["uv_points"][1])
    h, w = y.shape
    row_num = 0
    for y0 in range(0, h, FG_BLOCK_SIZE):
        bh = min(FG_BLOCK_SIZE, h - y0)
        if data["num_y_points"]:
            apply_grain_y(dy, y, data, sc_y, lut_y, row_num, y0, bh)
        bh_uv = (bh + 1) >> 1
        if data["num_uv_points"][0]:
            apply_grain_uv(du, u, y, data, sc_u, lut_u, row_num,
                           y0 >> 1, bh_uv, 0)
        if data["num_uv_points"][1]:
            apply_grain_uv(dv, v, y, data, sc_v, lut_v, row_num,
                           y0 >> 1, bh_uv, 1)
        row_num += 1
    return dy, du, dv
