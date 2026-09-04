"""Stage 3: per group, align the kept frames and combine them into one linear HDR-aware stack.

Every frame is converted to radiance units (counts * N^2 / (exposure*ISO)), clipped pixels are
masked out, and a weighted sigma-clipped mean is taken with weight ~ photon SNR * quality.
Long exposures therefore dominate the dark umbra while only short exposures fill the sunlit
limb - which is what makes the bracket group and the mixed-exposure totality groups work
without a separate HDR step.

Alignment is chained: each frame is registered to the already-aligned frame whose exposure
is closest to its own (a 1/1000 s sliver cannot be matched to a 1/2 s red disk directly,
but every rung of a bracket ladder matches its neighbour). Photometric scale factors are
chained the same way so exposure rounding (f/10 vs f/13 are 1/3-stop nominal values)
does not leave brightness steps at the HDR seams.
"""
import os, json
import numpy as np
import cv2
import tifffile

from . import analyze, align, raw, detect


def _load(cfg, rec):
    crop, cclip = analyze.load_crop(cfg, rec)
    rad = (crop.astype(np.float32) - rec["bg"]) * analyze.rad_scale(rec)
    return rad, ~cclip


def _stops(rec):
    return np.log2(1.0 / analyze.rad_scale(rec))


def _compose(M_outer, M_inner):
    """affine 2x3: outer(inner(x))."""
    A = np.vstack([M_outer, [0, 0, 1]]); B = np.vstack([M_inner, [0, 0, 1]])
    return (A @ B)[:2].astype(np.float32)


LINEAR_MAX = 0.70 * 65535   # postprocessed counts above this sit on the sensor's saturation knee


def photometric_ratio(ref_lum, ref_valid, ref_counts, mov_lum, mov_valid, mov_counts):
    """median(ref/mov) over pixels valid, well above noise and inside the linear range in both."""
    hi = np.percentile(ref_lum[ref_valid], 99.5) if ref_valid.sum() > 100 else ref_lum.max()
    mhi = np.percentile(mov_lum[mov_valid], 99.5) if mov_valid.sum() > 100 else mov_lum.max()
    sel = (ref_valid & mov_valid & (ref_lum > 0.05 * hi) & (mov_lum > 0.05 * mhi)
           & (ref_counts < LINEAR_MAX) & (mov_counts < LINEAR_MAX))
    if sel.sum() < 2000:
        return None, int(sel.sum())
    r = float(np.median(ref_lum[sel] / mov_lum[sel]))
    return float(np.clip(r, 0.5, 2.0)), int(sel.sum())


def cosmetic(img, k=5.0):
    """Replace isolated outliers (hot/dead pixels) by the 5x5 median; only for stacks with
    fewer than 3 frames, where sigma clipping cannot catch them. 5x5 because X-Trans
    demosaicing smears a hot photosite into a 2-3 px blob that a 3x3 median keeps."""
    out = img.copy()
    for c in range(img.shape[2]):
        ch = img[..., c].astype(np.float32)
        lo, hi = float(ch.min()), float(ch.max())
        q = ((ch - lo) / max(hi - lo, 1e-9) * 65535).astype(np.uint16)   # medianBlur 5x5 needs 8/16-bit
        med = cv2.medianBlur(q, 5).astype(np.float32) / 65535 * (hi - lo) + lo
        resid = ch - med
        noise = np.median(np.abs(resid)) * 1.4826 + 1e-9
        bad = np.abs(resid) > k * noise
        out[..., c][bad] = med[bad]
    return out


def sigma_clipped_mean(frames, w, valid, kappa, iters):
    """frames: N,H,W,3  w: N (per-frame weight ~ 1/noise^2)  valid: N,H,W bool.
    Residuals are scaled by sqrt(w) so a short, noisy exposure is judged against its own
    noise rather than against the low-noise frames that dominate the mean."""
    W = (w[:, None, None] * valid).astype(np.float32)[..., None]  # N,H,W,1
    sq = np.sqrt(w)[:, None, None, None].astype(np.float32)
    keep = np.ones(valid.shape, bool)
    for _ in range(iters):
        Wk = W * keep[..., None]
        sw = Wk.sum(0) + 1e-12
        mean = (Wk * frames).sum(0) / sw
        res = (frames - mean).mean(-1) * sq[..., 0]                 # noise-normalized residual
        ok = valid & keep
        n_eff = ok.sum(0)
        pooled = np.sqrt((res ** 2 * ok).sum(0) / np.maximum(n_eff, 1)) + 1e-9
        keep = (np.abs(res) <= kappa * pooled) | (n_eff[None] < 3)
    Wk = W * keep[..., None]
    sw = Wk.sum(0)
    out = (Wk * frames).sum(0) / np.maximum(sw, 1e-12)
    return out.astype(np.float32), sw[..., 0], keep


def align_group(cfg, frs, ref_rec, log):
    """Returns dict file -> (M mov->ref, photometric scale, anchor file).
    Geometry is chained through the nearest-exposure anchor; the photometric scale of each
    frame is then the weighted median of its ratios against *every* already-scaled frame it
    overlaps with (in reference coordinates), which keeps the HDR seams from wobbling."""
    data = {}
    for r in frs:
        rad, v = _load(cfg, r)
        lum = raw.luminance(rad)
        data[r["file"]] = dict(rec=r, rad=rad, valid=v, lum=lum, img=align.prep(lum, v))
    # Centers of thin slivers (short bracket rungs) are ambiguous along the arc. The camera is
    # fixed and the moon drifts at a constant rate, so predict those centers from a linear
    # drift model fitted to the frames whose whole disk was seen, and allow ECC only a small
    # correction around the prediction.
    conf = {r["file"]: r.get("center_conf", 1.0) for r in frs}
    center = {r["file"]: (r["mcx"] + r["x0"], r["mcy"] + r["y0"]) for r in frs}   # full-frame coords
    tight = set()
    good = [r for r in frs if conf[r["file"]] >= 0.5]
    weak = [r for r in frs if conf[r["file"]] < 0.5]
    if weak and len(good) >= 2 and (max(r["t"] for r in good) - min(r["t"] for r in good)) > 1:
        t = np.array([r["t"] for r in good]); A = np.c_[np.ones_like(t), t - t.mean()]
        bx = np.linalg.lstsq(A, np.array([center[r["file"]][0] for r in good]), rcond=None)[0]
        by = np.linalg.lstsq(A, np.array([center[r["file"]][1] for r in good]), rcond=None)[0]
        for r in weak:
            dt = r["t"] - t.mean()
            center[r["file"]] = (bx[0] + bx[1] * dt, by[0] + by[1] * dt)
            tight.add(r["file"])
        log(f"      drift model from {len(good)} full-disk frames: ({bx[1]:+.2f},{by[1]:+.2f}) px/s; "
            f"predicted centers for {len(weak)} sliver frames")
    elif weak:
        log(f"      {len(weak)} low-confidence centers but no drift model possible; using measured")
    I = np.array([[1, 0, 0], [0, 1, 0]], np.float32)
    done = {ref_rec["file"]: (I, 1.0, None)}
    order = [ref_rec["file"]]
    todo = [r for r in frs if r is not ref_rec]
    while todo:
        # next frame: the one closest in exposure (then time) to something already aligned
        best = None
        for r in todo:
            for a in done:
                ar = data[a]["rec"]
                cost = abs(_stops(r) - _stops(ar)) + abs(r["t"] - ar["t"]) / 600.0
                if best is None or cost < best[0]:
                    best = (cost, r, a)
        _, r, a = best
        todo.remove(r)
        A, F = data[a], data[r["file"]]
        ca, cf = center[a], center[r["file"]]
        # prior in crop coordinates: crop origins differ per frame
        prior = ((ca[0] - A["rec"]["x0"]) - (cf[0] - r["x0"]), (ca[1] - A["rec"]["y0"]) - (cf[1] - r["y0"]))
        # drift-model priors can be ~10 px off (long exposures measure the soft umbra edge
        # further out than short ones measure the sunlit limb), so give ECC room there
        tol = 12.0 if (a in tight or r["file"] in tight) else 4.0
        M_ra = align.estimate(A["img"], F["img"], F["valid"], allow_rotation=True, prior=prior, tol=tol)
        M = _compose(done[a][0], M_ra)
        done[r["file"]] = (M, 1.0, a)
        order.append(r["file"])
        log(f"      {r['file']} -> {a}  shift ({M_ra[0,2]:+.1f},{M_ra[1,2]:+.1f}) rot {align.rotation_deg(M_ra):+.2f}")
    # sliver frames: ECC cannot fix their position along the limb, but the limb itself fixes
    # the radial position exactly. Measure the warped sliver's limb radius against the
    # reference's fitted radius and slide it along the arc normal.
    S = data[ref_rec["file"]]["lum"].shape[0]
    R_ref = ref_rec.get("R_limb") or ref_rec["R"]
    for f in tight:
        M = done[f][0]
        for _ in range(2):
            lw = align.warp(data[f]["lum"], M, S)
            d, n, nv = detect.limb_radial_offset(lw, ref_rec["mcx"], ref_rec["mcy"], R_ref)
            if nv < 8 or abs(d) > 20:
                break
            M = M.copy(); M[0, 2] -= d * n[0]; M[1, 2] -= d * n[1]
        done[f] = (M, done[f][1], done[f][2])
        log(f"      {f} limb radial correction {d:+.2f}px along ({n[0]:+.2f},{n[1]:+.2f}) from {nv} rays")
    # photometric scales, in reference coordinates, against all previously scaled frames
    warped = {}
    for f in order:
        M = done[f][0]
        rec = data[f]["rec"]
        lum_w = align.warp(data[f]["lum"], M, S)
        val_w = align.warp(data[f]["valid"].astype(np.float32), M, S) > 0.999
        counts_w = lum_w / analyze.rad_scale(rec) + rec["bg"]
        warped[f] = (lum_w, val_w, counts_w)
    for f in order[1:]:
        lum_w, val_w, counts_w = warped[f]
        ratios, weights = [], []
        for a in order:
            if a == f or done[a][1] is None:
                continue
            if order.index(a) >= order.index(f):
                continue
            al, av, ac = warped[a]
            ratio, npx = photometric_ratio(al * done[a][1], av, ac, lum_w, val_w, counts_w)
            if ratio is not None:
                ratios.append(ratio); weights.append(npx)
        if ratios:
            idx = np.argsort(ratios); cw = np.cumsum(np.array(weights)[idx]); 
            scale = float(np.array(ratios)[idx][np.searchsorted(cw, cw[-1] / 2)])
        else:
            scale = done[done[f][2]][1]   # no linear overlap: inherit the anchor's scale
        done[f] = (done[f][0], scale, done[f][2])
        log(f"      {f} photometric x{scale:.3f} from {len(ratios)} frames")
    return done, data


def run(cfg, recs, groups, log=print):
    byname = {r["file"]: r for r in recs}
    S = cfg["crop_size"]
    outdir = os.path.join(cfg["output_dir"], "stacks")
    os.makedirs(outdir, exist_ok=True)
    for g in groups:
        files = g["keep"]
        if not files:
            log(f"[stack] {g['name']}: nothing to stack"); continue
        frs = [byname[f] for f in files]
        ref_rec = max(frs, key=lambda r: r["sharp"] * (1 if r["clip_frac"] < 0.05 else 0.5)
                      * (1 if r.get("center_conf", 1.0) >= 0.5 else 0.05))
        log(f"[stack] {g['name']}  ref {ref_rec['file']}")
        done, data = align_group(cfg, frs, ref_rec, log)
        R = ref_rec["R"]
        disk = analyze.disk_mask(S, ref_rec["mcx"], ref_rec["mcy"], R)
        frames, valid, weights, shifts = [], [], [], {}
        for r in frs:
            M, scale, anchor = done[r["file"]]
            D = data[r["file"]]
            frames.append(align.warp(D["rad"] * scale, M, S))
            valid.append(align.warp(D["valid"].astype(np.float32), M, S) > 0.999)
            q = g["weights"].get(r["file"], 1.0)
            weights.append(max(q, 0.2) / analyze.rad_scale(r))   # ~ photon SNR^2 per unit radiance
            shifts[r["file"]] = dict(dx=float(M[0, 2]), dy=float(M[1, 2]), rot=align.rotation_deg(M),
                                     scale=float(scale), anchor=anchor)
        frames = np.stack(frames); valid = np.stack(valid); weights = np.array(weights, np.float32)
        out, sw, keep = sigma_clipped_mean(frames, weights, valid, cfg["sigma_clip"], cfg["sigma_iters"])
        if len(frs) < 3:
            out = cosmetic(out)
        # pixels clipped in every frame: take the shortest exposure's (least wrong) value
        none = sw <= 0
        if none.any():
            shortest = int(np.argmax([analyze.rad_scale(r) for r in frs]))
            out[none] = frames[shortest][none]
        # background: subtract the residual sky level measured outside the disk
        outer = ~cv2.dilate(disk.astype(np.uint8), np.ones((81, 81), np.uint8)).astype(bool)
        bgv = np.median(out[outer], axis=0) if outer.sum() > 1000 else np.zeros(3, np.float32)
        out = out - bgv[None, None, :]
        path = os.path.join(outdir, g["name"] + ".tif")
        tifffile.imwrite(path, out, photometric="rgb")
        maskp = os.path.join(outdir, g["name"] + "_clipped.png")
        cv2.imwrite(maskp, (none * 255).astype(np.uint8))
        g["clip_mask"] = maskp
        g.update(stack=path, ref=ref_rec["file"], shifts=shifts, n_stacked=len(files),
                 mcx=ref_rec["mcx"], mcy=ref_rec["mcy"], R=R,
                 unclipped_frac=float(((sw > 0) & disk).sum() / disk.sum()),
                 rejected_px=float((~keep & valid).sum() / max(valid.sum(), 1)))
        maxrot = max(abs(s["rot"]) for s in shifts.values())
        maxsh = max(max(abs(s["dx"]), abs(s["dy"])) for s in shifts.values())
        log(f"[stack] {g['name']:<24} {len(files):>2} frames  max shift {maxsh:5.1f}px rot {maxrot:.2f}deg  "
            f"limb coverage {g['unclipped_frac']:.3f}  sigma-rejected {g['rejected_px']*100:.2f}%")
    with open(os.path.join(cfg["output_dir"], "groups.json"), "w", encoding="utf-8") as f:
        json.dump(groups, f, indent=1, ensure_ascii=False)
    return groups
