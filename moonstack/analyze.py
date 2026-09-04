"""Stage 1: decode every RAW once, find the moon, cache a crop, and compute per-frame features."""
import os, json, glob
import numpy as np
import cv2
from multiprocessing import Pool

from . import exif, raw, detect


def list_raws(cfg):
    files = []
    for ext in cfg["raw_ext"]:
        files += glob.glob(os.path.join(cfg["input_dir"], "*" + ext))
    return sorted(set(files))


def cache_path(cfg, rec):
    return os.path.join(cfg["cache_dir"], os.path.splitext(rec["file"])[0] + ".npz")


def load_crop(cfg, rec):
    z = np.load(cache_path(cfg, rec))
    return z["rgb"], z["clip"]


def stretch_preview(lum_c, size=200):
    p = np.percentile(lum_c, 99.7) + 1e-6
    s = np.sqrt(np.clip(lum_c / p, 0, 1))
    s = cv2.resize(s, (size, size), interpolation=cv2.INTER_AREA)
    return (s * 255).astype(np.uint8)


def rad_scale(rec):
    """counts -> radiance-like units: N^2 / (t * ISO). Aperture matters: f/6.3 vs f/13 is 4.3x."""
    n2 = rec["fnum"] ** 2 if rec.get("fnum") else 1.0
    return n2 / (rec["exp"] * rec["iso"])


def trail_px(rec, sensor_width_mm):
    """Expected motion blur (px) of an untracked moon: ~14.5 arcsec/s drift."""
    if not rec.get("focal"):
        return 0.0
    pitch_mm = sensor_width_mm / rec["W"]
    arcsec_per_px = 206265.0 * pitch_mm / rec["focal"]
    return 14.5 * rec["exp"] / arcsec_per_px


def disk_mask(S, mcx, mcy, R):
    yy, xx = np.mgrid[0:S, 0:S]
    return (xx - mcx) ** 2 + (yy - mcy) ** 2 <= R * R


def _sharpness(lum_c, disk, lit, clip):
    """Laplacian-of-Gaussian energy inside the lit, unclipped disk, on a linearly scaled image,
    minus the same measure on sky background so noise does not masquerade as detail."""
    p = np.percentile(lum_c[lit], 99.5) if lit.sum() > 100 else np.percentile(lum_c, 99.5)
    s = np.clip(lum_c / (p + 1e-6), 0, 1).astype(np.float32)
    g = cv2.GaussianBlur(s, (0, 0), 1.5)
    lap = cv2.Laplacian(g, cv2.CV_32F, ksize=3)
    inner = cv2.erode((lit & ~clip).astype(np.uint8), np.ones((15, 15), np.uint8)).astype(bool)
    outer = ~cv2.dilate(disk.astype(np.uint8), np.ones((61, 61), np.uint8)).astype(bool)
    v_in = float(np.mean(lap[inner] ** 2)) if inner.sum() > 500 else 0.0
    v_out = float(np.mean(lap[outer] ** 2)) if outer.sum() > 500 else 0.0
    return max(v_in - v_out, 0.0), v_in, v_out


def features(crop, cclip, rec):
    """Per-frame features from the cached crop. 'rad' units are counts * N^2 / (exposure * ISO)."""
    S = crop.shape[0]
    R = rec["R"]
    lum_c = raw.luminance(crop) - rec["bg"]
    c, Rf, conf = detect.refine_center(lum_c, R)
    rec["center_conf"], rec["R_limb"] = conf, Rf
    if c is not None and abs(c[0] - rec["mcx"]) < 24 and abs(c[1] - rec["mcy"]) < 24:
        rec["mcx"], rec["mcy"] = float(c[0]), float(c[1])
    disk = disk_mask(S, rec["mcx"], rec["mcy"], R)
    p995 = float(np.percentile(lum_c[disk], 99.5))
    lit = disk & (lum_c > 0.08 * p995)
    scale = rad_scale(rec)
    sharp, v_in, v_out = _sharpness(lum_c, disk, lit, cclip)
    rgbf = crop.astype(np.float32)
    sel = lit & ~cclip
    if sel.sum() < 100:
        sel = lit
    rr = float(rgbf[..., 0][sel].mean()) if sel.any() else 0.0
    bb = float(rgbf[..., 2][sel].mean()) if sel.any() else 1.0
    unclipped = disk & ~cclip
    rad = lum_c * scale
    return dict(
        p99=float(np.percentile(rad[unclipped], 99)) if unclipped.sum() > 100 else 0.0,
        med=float(np.median(rad[disk])),
        lit_frac=float(lit.mean() / disk.mean()),
        clip_frac=float(cclip[disk].mean()),
        rb=rr / max(bb, 1e-6),
        sharp=sharp, lap_in=v_in, lap_out=v_out,
    )


def analyze_one(args):
    path, cfg = args
    S = cfg["crop_size"]
    meta = exif.read(path)
    rgb, clip = raw.decode(path, cfg["raw_clip_level"])
    H, W = rgb.shape[:2]
    lum = raw.luminance(rgb)
    R = detect.radius_prior_px(meta, W, cfg["sensor_width_mm"], cfg["moon_diameter_deg"])
    center, area, bg, mad, R_fit = detect.find_moon(lum, R)
    rec = dict(meta, path=path, W=W, H=H, R=float(R), R_prior=float(R), R_fit=R_fit,
               area=area, bg=bg, noise=mad)
    if center is None:
        rec["ok"] = False
        return rec
    cx, cy = center
    x0, y0 = int(round(cx - S / 2)), int(round(cy - S / 2))
    crop = np.zeros((S, S, 3), np.uint16)
    cclip = np.ones((S, S), bool)          # outside-image pixels count as invalid
    xs, ys = max(x0, 0), max(y0, 0)
    xe, ye = min(x0 + S, W), min(y0 + S, H)
    crop[ys - y0:ye - y0, xs - x0:xe - x0] = rgb[ys:ye, xs:xe]
    cclip[ys - y0:ye - y0, xs - x0:xe - x0] = clip[ys:ye, xs:xe]
    cclip = cv2.dilate(cclip.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    rec.update(ok=True, cx=float(cx), cy=float(cy), x0=x0, y0=y0, mcx=float(cx - x0), mcy=float(cy - y0))
    rec.update(features(crop, cclip, rec))
    os.makedirs(os.path.join(cfg["cache_dir"], "thumbs"), exist_ok=True)
    np.savez(cache_path(cfg, rec), rgb=crop, clip=cclip)
    stem = os.path.splitext(rec["file"])[0]
    cv2.imwrite(os.path.join(cfg["cache_dir"], "thumbs", stem + ".png"),
                stretch_preview(raw.luminance(crop) - bg))
    return rec


def _fmt(r):
    return (f"  {r['file']}  exp={r['exp']:.4g}s ISO{r['iso']:.0f}  lit={r['lit_frac']:.2f} "
            f"clip={r['clip_frac']:.3f} sharp={r['sharp']:.2e} rb={r['rb']:.2f}")


def calibrate_radius(recs, log):
    """Full-disk frames give a direct radius; replace the EXIF prior if they agree roughly."""
    ok = [r for r in recs if r.get("ok")]
    if not ok:
        return
    amax = max(r["area"] for r in ok)
    full = [r for r in ok if r["R_fit"] and r["area"] >= 0.95 * amax]
    if not full:
        return
    R = float(np.median([r["R_fit"] for r in full]))
    if abs(R - recs[0]["R_prior"]) / recs[0]["R_prior"] < 0.15:
        log(f"[analyze] moon radius calibrated: prior {recs[0]['R_prior']:.1f}px -> fitted {R:.1f}px")
        for r in recs:
            r["R"] = R


def save(cfg, recs):
    with open(os.path.join(cfg["output_dir"], "frames.json"), "w", encoding="utf-8") as f:
        json.dump(recs, f, indent=1, ensure_ascii=False)


def run(cfg, files=None, log=print):
    files = files or list_raws(cfg)
    os.makedirs(cfg["cache_dir"], exist_ok=True)
    log(f"[analyze] {len(files)} RAW files, {cfg['workers']} workers")
    with Pool(cfg["workers"]) as pool:
        recs = []
        for r in pool.imap(analyze_one, [(f, cfg) for f in files]):
            recs.append(r)
            log(_fmt(r) if r.get("ok") else f"  {r['file']}  MOON NOT FOUND")
    calibrate_radius(recs, log)
    refeature(cfg, recs, log=lambda *_: None)
    save(cfg, recs)
    return recs


def refeature(cfg, recs, log=print):
    """Recompute features from the cache (fast) - used after radius calibration or when tuning."""
    for r in recs:
        if r.get("ok"):
            crop, cclip = load_crop(cfg, r)
            r.update(features(crop, cclip, r))
            log(_fmt(r))
    save(cfg, recs)
    return recs
