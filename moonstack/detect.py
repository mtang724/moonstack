"""Locate the moon disk. Fixed-radius Hough on the lit-region boundary, so a crescent
still yields the true disk center (the umbra edge has a very different curvature)."""
import numpy as np
import cv2


def radius_prior_px(meta, pitch_mm, moon_diameter_deg):
    return meta["focal"] * np.tan(np.deg2rad(moon_diameter_deg / 2)) / pitch_mm


def lit_mask(lum, thr_frac=0.08):
    bg = np.median(lum)
    mad = np.median(np.abs(lum - bg)) * 1.4826 + 1e-6
    p999 = np.percentile(lum, 99.9)
    thr = max(bg + 8 * mad, bg + thr_frac * (p999 - bg))
    m = (lum > thr).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        return np.zeros_like(m, bool), bg, mad
    big = 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])
    return lab == big, bg, mad


def _circle_offsets(R):
    th = np.linspace(0, 2 * np.pi, int(2 * np.pi * R * 1.5), endpoint=False)
    return np.round(R * np.cos(th)).astype(int), np.round(R * np.sin(th)).astype(int)


def boundary(mask):
    return mask & ~cv2.erode(mask.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)


def hough_center(mask, R):
    ys, xs = np.nonzero(boundary(mask))
    if len(xs) < 10:
        return None, 0.0
    H, W = mask.shape
    acc = np.zeros((H, W), np.float32)
    dx, dy = _circle_offsets(R)
    cx = (xs[:, None] + dx[None, :]).ravel()
    cy = (ys[:, None] + dy[None, :]).ravel()
    ok = (cx >= 0) & (cx < W) & (cy >= 0) & (cy < H)
    np.add.at(acc, (cy[ok], cx[ok]), 1.0)
    acc = cv2.GaussianBlur(acc, (0, 0), 1.5)
    idx = int(np.argmax(acc))
    cx, cy = idx % W, idx // W
    # sub-pixel: intensity centroid of the accumulator around the peak
    x0, x1, y0, y1 = max(cx - 3, 0), min(cx + 4, W), max(cy - 3, 0), min(cy + 4, H)
    win = acc[y0:y1, x0:x1]
    if win.sum() > 0:
        yy, xx = np.mgrid[y0:y1, x0:x1]
        cx, cy = float((win * xx).sum() / win.sum()), float((win * yy).sum() / win.sum())
    return (cx, cy), float(acc.flat[idx])


def limb_rays(lum, cx, cy, R, span=30.0, step=0.5, nang=720):
    """Sample rays from (cx, cy); return (theta, limb radius per ray, valid mask)."""
    th = np.linspace(0, 2 * np.pi, nang, endpoint=False).astype(np.float32)
    rr = np.arange(R - span, R + span + step, step).astype(np.float32)
    mx = cx + rr[None, :] * np.cos(th)[:, None]
    my = cy + rr[None, :] * np.sin(th)[:, None]
    P = cv2.remap(lum.astype(np.float32), mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    P = cv2.blur(P, (3, 5))
    sky = P[:, -10:]
    noise = np.median(np.abs(sky - np.median(sky))) * 1.4826 + 1e-6
    g = P[:, 2:] - P[:, :-2]
    jstar = np.argmin(g, axis=1) + 1
    k = np.arange(nang)
    inner = P[k, np.clip(jstar - 8, 0, P.shape[1] - 1)]
    outer = P[k, np.clip(jstar + 8, 0, P.shape[1] - 1)]
    valid = ((inner - outer) > 8 * noise) & (outer < 0.35 * inner) & (jstar > 3) & (jstar < P.shape[1] - 4)
    j0 = np.clip(jstar - 1, 1, g.shape[1] - 2)
    a, b, c = g[k, j0 - 1], g[k, j0], g[k, j0 + 1]
    den = a - 2 * b + c
    off = np.where(np.abs(den) > 1e-9, 0.5 * (a - c) / np.where(np.abs(den) > 1e-9, den, 1), 0)
    r_k = R - span + (jstar + np.clip(off, -1, 1)) * step
    return th, r_k, valid


def limb_radial_offset(lum, cx, cy, R):
    """For a thin sliver whose center is ambiguous along the arc: how far (px) its limb sits
    outside radius R, and the unit normal of the arc. Shift the frame by -offset*normal to
    put its limb on the circle. Returns (offset, (nx, ny), n_valid_rays)."""
    th, r_k, valid = limb_rays(lum, cx, cy, R)
    if valid.sum() < 8:
        return 0.0, (0.0, 0.0), int(valid.sum())
    res = r_k[valid] - R
    med = np.median(res)
    use = np.abs(res - med) < 3.0
    d = float(np.median(res[use])) if use.sum() >= 5 else float(med)
    nx, ny = np.cos(th[valid][use]).mean(), np.sin(th[valid][use]).mean()
    nrm = np.hypot(nx, ny) + 1e-9
    return d, (float(nx / nrm), float(ny / nrm)), int(use.sum())


def limb_fit(lum, cx, cy, R, span=30.0, step=0.5, nang=720):
    """Radial-ray limb fit. Samples the image along rays from (cx, cy), finds the outward
    brightness drop nearest to radius R on each ray, and least-squares fits
    r(theta) = R' + dx cos(theta) + dy sin(theta). Works for the sunlit limb, the faint
    umbra/sky limb of a long exposure, and (with few valid rays) a thin sliver.
    Returns (cx, cy), R', confidence (= fraction of rays with a usable limb crossing)."""
    th = np.linspace(0, 2 * np.pi, nang, endpoint=False).astype(np.float32)
    rr = np.arange(R - span, R + span + step, step).astype(np.float32)
    mx = cx + rr[None, :] * np.cos(th)[:, None]
    my = cy + rr[None, :] * np.sin(th)[:, None]
    P = cv2.remap(lum.astype(np.float32), mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    P = cv2.blur(P, (3, 5))                                    # 3 radial samples x 5 angular bins
    sky = P[:, -10:]
    noise = np.median(np.abs(sky - np.median(sky))) * 1.4826 + 1e-6
    g = P[:, 2:] - P[:, :-2]                                   # outward gradient
    jstar = np.argmin(g, axis=1) + 1
    k = np.arange(nang)
    inner = P[k, np.clip(jstar - 8, 0, P.shape[1] - 1)]
    outer = P[k, np.clip(jstar + 8, 0, P.shape[1] - 1)]
    amp = inner - outer
    valid = (amp > 8 * noise) & (outer < 0.35 * inner) & (jstar > 3) & (jstar < P.shape[1] - 4)
    # sub-sample refinement of the gradient minimum
    gm = g[k, np.clip(jstar - 1, 0, g.shape[1] - 1)], g[k, np.clip(jstar - 1, 0, g.shape[1] - 1)]
    j0 = np.clip(jstar - 1, 1, g.shape[1] - 2)
    a, b, c = g[k, j0 - 1], g[k, j0], g[k, j0 + 1]
    den = a - 2 * b + c
    off = np.where(np.abs(den) > 1e-9, 0.5 * (a - c) / np.where(np.abs(den) > 1e-9, den, 1), 0)
    r_k = R - span + (jstar + np.clip(off, -1, 1)) * step
    if valid.sum() < 12:
        return (cx, cy), R, float(valid.mean())
    A = np.c_[np.ones(nang), np.cos(th), np.sin(th)]
    use = valid.copy()
    for _ in range(3):
        sol, *_ = np.linalg.lstsq(A[use], r_k[use], rcond=None)
        res = r_k - A @ sol
        s = np.median(np.abs(res[use])) * 1.4826 + 0.3
        use = valid & (np.abs(res) < 2.5 * s)
        if use.sum() < 12:
            break
    Rf, dx, dy = sol
    return (float(cx + dx), float(cy + dy)), float(Rf), float(use.mean())


def refine_center(lum_crop, R):
    """Coarse fixed-radius Hough on the lit region, then two rounds of radial limb fitting.
    Returns (center, fitted radius, confidence). Confidence ~1 for a whole disk (sunlit, or
    umbra in a long exposure), ~0.5 for a half-lit moon in a short exposure, <0.3 for a sliver
    whose center is ambiguous along the arc."""
    mask, _, _ = lit_mask(lum_crop)
    c, votes = hough_center(mask, R)
    if c is None:
        return None, R, 0.0
    Rf, conf = R, 0.0
    for _ in range(2):
        c, Rf, conf = limb_fit(lum_crop, c[0], c[1], R)
    return c, Rf, conf


def fit_circle(mask):
    """Least-squares (Kasa) circle fit on the boundary. Only meaningful for a full disk."""
    ys, xs = np.nonzero(boundary(mask))
    if len(xs) < 20:
        return None
    A = np.c_[2 * xs, 2 * ys, np.ones_like(xs)].astype(np.float64)
    b = (xs ** 2 + ys ** 2).astype(np.float64)
    (a, c, d), *_ = np.linalg.lstsq(A, b, rcond=None)
    return float(a), float(c), float(np.sqrt(d + a * a + c * c))


def find_moon(lum_full, R_px, scale=4):
    """Returns (center_xy in full-res coords or None, lit area in full-res px^2, bg, noise, fitted R or None)."""
    small = cv2.resize(lum_full, (lum_full.shape[1] // scale, lum_full.shape[0] // scale),
                       interpolation=cv2.INTER_AREA)
    mask, bg, mad = lit_mask(small)
    area = float(mask.sum()) * scale * scale
    c, votes = hough_center(mask, R_px / scale)
    if c is None:
        return None, area, bg, mad, None
    # Full-disk frames also give a direct radius measurement, used to calibrate R.
    fit = fit_circle(mask)
    R_fit = fit[2] * scale if fit else None
    return (float(c[0] * scale), float(c[1] * scale)), area, float(bg), float(mad), R_fit
