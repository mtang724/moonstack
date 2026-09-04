"""Rebuild the blown sunlit sliver of a deep-phase stack from physics rather than paint it white.

Every pixel of the sliver is lunar surface that a properly exposed full-moon frame recorded
earlier the same night, lit through the penumbra by a *smooth* illumination gradient. So:
    radiance(x) = albedo(x) * illumination(x)
albedo comes from the full-moon stack registered onto this stack (same disk size, only the
field rotation of a fixed tripod differs); illumination is measured as radiance/albedo on the
unclipped pixels around the sliver, extrapolated with a quadratic trend + normalized
convolution into the clipped region, per channel so the colour gradient continues too.
"""
import numpy as np
import cv2


def _stretch(lum, valid, disk):
    x = lum.copy()
    hi = np.percentile(x[valid & disk], 99.5) if (valid & disk).sum() > 100 else x.max()
    x[~valid] = hi
    k = 30.0
    return (np.arcsinh(np.clip(x / (hi + 1e-9), 0, 1) * k) / np.arcsinh(k)).astype(np.float32)


def register(albedo_lum, target_lum, target_valid, cx, cy, R, log, name):
    """Euclidean warp mapping the albedo (full moon) image onto the target stack. The disk
    is a circle so translation is known; the rotation is searched, then ECC refines."""
    S = target_lum.shape[0]
    yy, xx = np.mgrid[0:S, 0:S]
    disk = (xx - cx) ** 2 + (yy - cy) ** 2 <= (R - 6) ** 2
    ref = _stretch(target_lum, target_valid, disk)
    mov = _stretch(albedo_lum, np.ones_like(target_valid), disk)
    # high-pass both so the umbra gradient does not dominate the correlation
    ref = ref - cv2.GaussianBlur(ref, (0, 0), 25); mov = mov - cv2.GaussianBlur(mov, (0, 0), 25)
    mask = (disk & target_valid).astype(np.uint8)
    mask = cv2.erode(mask, np.ones((15, 15), np.uint8))
    s = 4
    r4 = cv2.resize(ref, None, fx=1 / s, fy=1 / s, interpolation=cv2.INTER_AREA)
    m4 = cv2.resize(mov, None, fx=1 / s, fy=1 / s, interpolation=cv2.INTER_AREA)
    k4 = cv2.resize(mask, None, fx=1 / s, fy=1 / s, interpolation=cv2.INTER_NEAREST)
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, 1e-4)
    best = (-1.0, None)
    for ang in range(-60, 61, 4):
        M = cv2.getRotationMatrix2D((cx / s, cy / s), ang, 1.0).astype(np.float32)
        try:
            rho, M2 = cv2.findTransformECC(r4, m4, M, cv2.MOTION_EUCLIDEAN, crit, k4, 3)
        except cv2.error:
            continue
        if rho > best[0]:
            best = (rho, M2)
    if best[1] is None:
        log(f"[rescue] {name}: registration failed"); return None, 0.0
    M = best[1].copy(); M[:, 2] *= s
    try:
        crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-6)
        rho, M = cv2.findTransformECC(ref, mov, M, cv2.MOTION_EUCLIDEAN, crit, mask, 5)
    except cv2.error:
        rho = best[0]
    ang = float(np.degrees(np.arctan2(M[1, 0], M[0, 0])))
    log(f"[rescue] {name}: full-moon texture registered, rotation {ang:+.1f} deg, correlation {rho:.3f}")
    return M, float(rho)


def _nc_fill(values, known, sigmas=(3, 6, 12, 24, 48, 96)):
    """Normalized-convolution fill of unknown pixels from known ones (no extrapolated trend)."""
    out = np.where(known, values, 0).astype(np.float32); have = known.copy()
    for sig in sigmas:
        num = cv2.GaussianBlur(out * have, (0, 0), sig); den = cv2.GaussianBlur(have.astype(np.float32), (0, 0), sig)
        est = num / np.maximum(den, 1e-6)
        new = ~have & (den > 0.02)
        out[new] = est[new]; have |= new
    return np.where(have, out, 0)


NB = 720   # angular bins for the per-ray illumination fit


def _circular_smooth(v, sigma):
    """Gaussian smoothing on a circular array with NaN gaps filled from neighbours."""
    n = len(v); ok = ~np.isnan(v)
    if ok.sum() == 0:
        return v
    idx = np.arange(n)
    src = idx[ok]
    # nearest known bin (circular)
    d = np.abs(idx[:, None] - src[None, :]); d = np.minimum(d, n - d)
    filled = v[src[np.argmin(d, axis=1)]]
    k = int(3 * sigma)
    ker = np.exp(-0.5 * (np.arange(-k, k + 1) / sigma) ** 2); ker /= ker.sum()
    return np.array([np.sum(np.roll(filled, -o)[0] * ker[k + o] for o in range(-k, k + 1)) for _ in [0]] * 0 + [
        np.sum(filled[(i + np.arange(-k, k + 1)) % n] * ker) for i in range(n)])


def _illumination_polar(logR, known, fill, cx, cy, near=60, gap=13):
    """log illumination inside `fill`, extrapolated along radial rays. On each ray the known
    profile just below the sliver (gap..near px inside its boundary) is fitted with a quadratic
    in radius and continued outward to the limb; coefficients are smoothed across rays.
    Capped at 0 (full sunlight) and kept monotonic."""
    S = logR.shape[0]
    yy, xx = np.mgrid[0:S, 0:S]
    r = np.hypot(xx - cx, yy - cy).astype(np.float32)
    b = (((np.arctan2(yy - cy, xx - cx) + np.pi) / (2 * np.pi)) * NB).astype(int) % NB
    r0 = np.full(NB, np.inf, np.float32)
    np.minimum.at(r0, b[fill], r[fill])
    A = np.full(NB, np.nan); B = np.full(NB, np.nan); C = np.full(NB, np.nan)
    sel = known & np.isfinite(r0[b]) & (r >= r0[b] - near) & (r <= r0[b] - gap)
    bins = b[sel]; xs = (r - r0[b])[sel]; ys = logR[sel]
    order = np.argsort(bins); bins, xs, ys = bins[order], xs[order], ys[order]
    starts = np.searchsorted(bins, np.arange(NB)); ends = np.searchsorted(bins, np.arange(NB), side="right")
    for k in range(NB):
        x, y = xs[starts[k]:ends[k]], ys[starts[k]:ends[k]]
        if len(x) < 10:
            continue
        w = np.exp(-(np.abs(x) - gap) / 25.0)
        if len(x) >= 25:
            M = np.c_[np.ones_like(x), x, x ** 2]
        else:
            M = np.c_[np.ones_like(x), x]
        coef, *_ = np.linalg.lstsq(M * w[:, None], y * w, rcond=None)
        A[k], B[k] = coef[0], coef[1]; C[k] = coef[2] if len(coef) > 2 else 0.0
    if np.isnan(A).all():
        return None
    A, B, C = (_circular_smooth(v, 6.0) for v in (A, B, C))
    x = (r - r0[b]); x = np.where(fill, x, 0)
    Ab, Bb, Cb = A[b], B[b], C[b]
    # keep the extrapolation monotonic: beyond a parabola's vertex hold the vertex value
    xv = np.where(np.abs(Cb) > 1e-9, -Bb / (2 * Cb), np.inf)
    xe = np.where((Cb < 0) & (x > xv), xv, x)
    hat = Ab + Bb * xe + Cb * xe ** 2
    return np.minimum(hat, 0.0).astype(np.float32)


LIMB_RISE = 3.0   # fallback brightness rise at the limb when no measured profile is available


def sliver_profile(img, cx, cy, R, depth=240):
    """Radial brightness profile of a *measured* (unclipped) sliver, e.g. the bracket HDR
    group: mean luminance vs depth below the limb (px), averaged over +-8 deg around the
    brightest limb direction. Absolute radiance units, so it can be matched to other stacks."""
    lum = 0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2]
    th = np.linspace(0, 2 * np.pi, 720, endpoint=False).astype(np.float32)
    rim = cv2.remap(lum.astype(np.float32), (cx + (R - 12) * np.cos(th))[None, :],
                    (cy + (R - 12) * np.sin(th))[None, :], cv2.INTER_LINEAR)[0]
    k = int(np.argmax(cv2.blur(rim[None, :], (15, 1))[0]))
    angs = th[k] + np.deg2rad(np.arange(-8, 9, 1.0)).astype(np.float32)
    d = np.arange(0, depth, 1.0).astype(np.float32)
    mx = cx + (R - d)[None, :] * np.cos(angs)[:, None]; my = cy + (R - d)[None, :] * np.sin(angs)[:, None]
    P = cv2.remap(lum.astype(np.float32), mx, my, cv2.INTER_LINEAR).mean(0)
    P = np.maximum(np.minimum.accumulate(np.maximum(P, 1e-9)), 1e-9)      # enforce monotonic falloff
    return d, P


def _fill_depths(fill, cx, cy, R, nb=720):
    """Per-pixel: depth of this pixel below the limb, and depth of the fill boundary along
    the same ray (smoothed across rays)."""
    S = fill.shape[0]
    yy, xx = np.mgrid[0:S, 0:S]
    r = np.hypot(xx - cx, yy - cy).astype(np.float32)
    b = (((np.arctan2(yy - cy, xx - cx) + np.pi) / (2 * np.pi)) * nb).astype(int) % nb
    rmin = np.full(nb, np.inf, np.float32); np.minimum.at(rmin, b[fill], r[fill])
    D = np.where(np.isfinite(rmin), R - rmin, np.nan).astype(np.float64)
    D = _circular_smooth(D, 4.0)
    return R - r, np.maximum(D[b], 1.0).astype(np.float32)




def rescue(img, albedo, w_sat, disk, cx, cy, R, log, name, clip_mask=None, profile=None):
    """img, albedo: linear RGB stacks in the same radiance units and coordinates.
    w_sat: 0..1 saturation weight of img. Returns (rebuilt img, blend mask).

    Rendering, not extrapolation: the measured brightness at the edge of the blown region is
    continued smoothly inside, modulated by the full-moon crater texture (albedo / its local
    mean) and lifted gently toward the limb; colour eases from the boundary colour to the
    sunlit (albedo) colour. Extrapolating the true penumbral gradient was tried and is too
    unstable (spokes, blue bloom) - this stays bright and textured without inventing a slope."""
    S = img.shape[0]
    fill = (w_sat > 0.1) & cv2.dilate(disk.astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool)
    if fill.sum() < 50:
        return img, np.zeros((S, S), np.float32)
    lum = lambda a: 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]
    L_img, L_alb = np.maximum(lum(img), 1e-9), np.maximum(lum(albedo), 1e-9)
    # demosaicing next to saturated photosites leaves near-black holes along the sliver's
    # edge in single-frame stacks: rebuild those too
    ring = cv2.dilate(fill.astype(np.uint8), np.ones((11, 11), np.uint8)).astype(bool) & disk & ~fill
    if ring.sum() > 100:
        holes = ring & (L_img < 0.1 * np.median(L_img[ring]))
        fill |= cv2.dilate(holes.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool) & disk
    alb_ok = L_alb > 0.05 * np.percentile(L_alb[disk], 99)
    known = (disk & alb_ok & (w_sat < 0.02) & (L_img > 0.02 * np.median(L_img[disk]))
             & ~cv2.dilate(fill.astype(np.uint8), np.ones((13, 13), np.uint8)).astype(bool))
    # boundary brightness continued inward (log domain, smooth), texture from the albedo map
    base = np.exp(_nc_fill(np.where(known, np.log(L_img), 0), known))
    texture = L_alb / np.maximum(cv2.GaussianBlur(L_alb, (0, 0), 14), 1e-9)
    texture = np.clip(texture, 0.5, 1.6)
    dist = cv2.distanceTransform(fill.astype(np.uint8), cv2.DIST_L2, 5)
    dmax = max(float(dist.max()), 1.0)
    plateau = float(np.median(L_img[fill]))
    ring = cv2.dilate(fill.astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool) & known
    boundary_level = float(np.median(L_img[ring])) if ring.sum() > 50 else 0.65 * plateau
    how, rise = "fallback rise", None
    if profile is not None:
        d_ax, P = profile
        below = np.nonzero(P <= boundary_level)[0]
        if len(below) and below[0] >= 8 and P[0] > 2 * boundary_level:
            # measured penumbral shape, stretched to this sliver's width on every ray:
            # log-rise = [ln P(u*Db) - ln P(Db)] * (D/Dmax), u = depth/D in [0,1]
            Db = int(below[0])
            depth, D = _fill_depths(fill, cx, cy, R)
            u = np.clip(depth / D, 0, 1)
            lnP = np.log(P)
            lnP_u = np.interp(u * Db, d_ax, lnP)
            rise = np.exp(np.clip((lnP_u - lnP[Db]) * np.clip(D / D.max(), 0, 1), 0, None))
            how = "measured profile (limb/boundary x%.0f, width %dpx)" % (np.exp(lnP[0] - lnP[Db]), Db)
    if rise is None:
        rise = np.exp(np.log(LIMB_RISE) * np.clip(dist / dmax, 0, 1))
    ramp = np.clip(dist / 8.0, 0, 1)
    L_new = np.maximum(base, base * (1 - ramp) + plateau * ramp) * texture * rise
    L_new = np.minimum(L_new, L_alb)          # never brighter than full sunlight
    beta = np.clip(dist / 40.0, 0, 1)
    out = img.copy()
    for c in range(3):
        col_known = _nc_fill(np.where(known, np.clip(img[..., c] / L_img, 0.05, 3.0), 0), known)
        col_alb = np.clip(albedo[..., c] / L_alb, 0.05, 3.0)
        col = col_known * (1 - beta) + col_alb * beta
        out[..., c] = np.where(fill, L_new * col, img[..., c])
    m = np.clip(cv2.GaussianBlur(fill.astype(np.float32), (0, 0), 2.0) * 1.5, 0, 1)
    out = img * (1 - m[..., None]) + out * m[..., None]
    gain = float(np.median(lum(out)[fill] / np.maximum(L_img[fill], 1e-9)))
    log(f"[rescue] {name}: rebuilt {int(fill.sum())} px of blown sliver with full-moon texture, {how} (x{gain:.2f} vs clipped level)")
    return out.astype(np.float32), m.astype(np.float32)
