"""Stage 5: turn each linear stack into a display image, plus the eclipse-sequence composite.

- white balance: the sunlit lunar surface is defined as neutral grey (measured on the most
  sunlit group) and the same multipliers are applied to every group, so the umbra's red is
  preserved relative to it.
- sunlit groups: linear scale to a white point + sRGB curve (natural look).
- umbra groups (deepest phase / bracket HDR): local tone mapping - log luminance is split into
  a smooth base (bilateral filter) and detail; the base is compressed to a few stops so the
  sunlit sliver and the umbra both fit, while detail keeps its contrast. Only the disk is tone
  mapped; the sky keeps a plain linear stretch so it stays dark (stars survive, noise is not lifted).
Outputs 16-bit sRGB TIFFs (for Photoshop) and JPG previews in output/final/.
"""
import os, json
import numpy as np
import cv2
import tifffile

from . import analyze

STAGE_LABEL = {
    "full": "Full moon / penumbral", "partial_in": "Partial (entering)", "deep": "Deepest phase",
    "bracket": "HDR bracket", "partial_out": "Partial (leaving)",
}


def srgb_encode(x):
    x = np.clip(x, 0, 1)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def lum(img):
    return 0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2]


def soft_disk(S, cx, cy, R, feather=8.0):
    yy, xx = np.mgrid[0:S, 0:S]
    r = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    return np.clip((R + feather / 2 - r) / feather, 0, 1).astype(np.float32)


def tonemap_disk(img, disk, look):
    """Durand-style local tone mapping of linear RGB, returns linear RGB scaled so the
    brightest smooth structure sits at look['deep_highlight']."""
    L = lum(img)
    floor = max(np.percentile(L[disk], 0.5), 1e-6 * float(np.percentile(L[disk], 99.99)))
    logL = np.log2(np.maximum(L, 0.5 * floor)).astype(np.float32)
    base = cv2.bilateralFilter(logL, d=0, sigmaColor=0.5, sigmaSpace=25)
    detail = logL - base
    hi, lo = np.percentile(base[disk], 99.9), np.percentile(base[disk], 1.0)
    c = min(1.0, look["deep_base_stops"] / max(hi - lo, 1e-3))
    out_lum = np.power(2.0, (base - hi) * c + detail * look["deep_detail_gain"]) * look["deep_highlight"]
    return img * (out_lum / np.maximum(L, 1e-12))[..., None], c


def load_linear(g):
    src = g.get("pi_out") or g["stack"]
    img = tifffile.imread(src).astype(np.float32)
    if img.ndim == 2:
        img = np.repeat(img[..., None], 3, -1)
    if g.get("pi_out") and g.get("pi_scale"):
        img = img * g["pi_scale"]
    return img


def ca_correct(img, disk, log, name):
    """Register R and B channels onto G with an affine ECC (lateral CA is mostly a per-channel
    radial scale, which an affine captures over a single disk). Returns corrected image."""
    S = img.shape[0]
    G = img[..., 1]
    hi = np.percentile(G[disk], 99.5) + 1e-9
    gp = np.arcsinh(np.clip(G / hi, 0, 1) * 20).astype(np.float32)
    mask = cv2.dilate(disk.astype(np.uint8), np.ones((15, 15), np.uint8))
    out = img.copy()
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-6)
    for ch, nm in ((0, "R"), (2, "B")):
        C = img[..., ch]
        cp = np.arcsinh(np.clip(C / (np.percentile(C[disk], 99.5) + 1e-9), 0, 1) * 20).astype(np.float32)
        M = np.array([[1, 0, 0], [0, 1, 0]], np.float32)
        try:
            _, M = cv2.findTransformECC(gp, cp, M, cv2.MOTION_AFFINE, crit, mask, 5)
        except cv2.error:
            log(f"[finish] {name}: CA fit failed for {nm}, left as is"); continue
        scale = float(np.sqrt(abs(np.linalg.det(M[:, :2]))))
        shift = float(np.hypot(M[0, 2], M[1, 2]))
        if abs(scale - 1) > 0.01 or shift > 8:
            log(f"[finish] {name}: CA fit for {nm} implausible (scale {scale:.4f}, shift {shift:.1f}px), skipped"); continue
        out[..., ch] = cv2.warpAffine(C, M, (S, S), flags=cv2.INTER_LANCZOS4 | cv2.WARP_INVERSE_MAP,
                                      borderMode=cv2.BORDER_REPLICATE)
        log(f"[finish] {name}: CA {nm}->G scale {scale:.4f} shift {shift:.2f}px")
    return out


def sunlit_wb(cfg, groups, log):
    ref = max((g for g in groups if g.get("stack")), key=lambda g: g["sun_frac"])
    img = load_linear(ref)
    S = img.shape[0]
    disk = analyze.disk_mask(S, ref["mcx"], ref["mcy"], ref["R"] * 0.95)
    L = lum(img)
    sel = disk & (L > 0.25 * np.percentile(L[disk], 99))
    mean = img[sel].mean(0)
    wb = mean.mean() / np.maximum(mean, 1e-9)
    log(f"[finish] sunlit-surface white balance from {ref['name']}: R x{wb[0]:.3f} G x{wb[1]:.3f} B x{wb[2]:.3f}")
    return wb.astype(np.float32)


def saturation_weight(img, disk, g):
    """0..1 where any channel approaches its plateau (clipped in every source frame)."""
    plateau = np.array([np.percentile(img[..., c][disk], 99.95) for c in range(3)], np.float32) + 1e-9
    satur = (img / plateau[None, None, :]).max(-1)
    w = np.clip((satur - 0.6) / 0.35, 0, 1)
    if g.get("clip_mask") and os.path.exists(g["clip_mask"]):
        cm = cv2.imread(g["clip_mask"], cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255.0
        w = np.maximum(w, cm)
    return w


def develop(cfg, g, wb, log=print, albedo=None):
    look = cfg["look"]
    img = load_linear(g)
    S = img.shape[0]
    disk = analyze.disk_mask(S, g["mcx"], g["mcy"], g["R"])
    if cfg.get("ca_correct"):
        img = ca_correct(img, disk, log, g["name"])
    w_sat = saturation_weight(img, disk, g)
    rescued = np.zeros((S, S), np.float32)
    if (cfg.get("rescue_highlights") and albedo is not None and g["long"]
            and g.get("unclipped_frac", 1.0) < 0.995 and g["kind"] != "bracket"):
        from . import rescue
        L = lum(img)
        M, rho = rescue.register(lum(albedo["img"]), L, w_sat < 0.05, g["mcx"], g["mcy"], g["R"], log, g["name"])
        if M is not None and rho > 0.25:
            # bring the full-moon stack into this stack's frame (its own disk center differs)
            T = np.array([[1, 0, g["mcx"] - albedo["mcx"]], [0, 1, g["mcy"] - albedo["mcy"]]], np.float32)
            A3 = np.vstack([M, [0, 0, 1]]) @ np.vstack([T, [0, 0, 1]])
            alb_w = cv2.warpAffine(albedo["img"], A3[:2].astype(np.float32), (S, S),
                                   flags=cv2.INTER_LANCZOS4 | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_CONSTANT)
            cm = None
            if g.get("clip_mask") and os.path.exists(g["clip_mask"]):
                cm = cv2.imread(g["clip_mask"], cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255.0
            img, rescued = rescue.rescue(img, alb_w, w_sat, disk, g["mcx"], g["mcy"], g["R"], log, g["name"], cm,
                                         albedo.get("profile"))
            g["rescued_px"] = int((rescued > 0.5).sum())
        else:
            log(f"[rescue] {g['name']}: registration too weak (rho {rho:.2f}), sliver left clipped")
    img = img * wb[None, None, :]
    outer = ~analyze.disk_mask(S, g["mcx"], g["mcy"], g["R"] + 40)
    L = lum(img)
    if outer.sum() > 1000:
        img = img - np.median(img[outer], axis=0)[None, None, :]
        L = lum(img)
    if not g["long"]:
        white = np.percentile(L[disk], look["full_white_pct"])
        y = srgb_encode(img / max(white, 1e-9) * look["full_white_target"])
        sat = look["saturation"]
    else:
        white = np.percentile(L[disk], 99.9)
        mapped, c = tonemap_disk(img, disk, look)
        plain = img / max(white, 1e-9) * look["deep_highlight"]
        w = soft_disk(S, g["mcx"], g["mcy"], g["R"] + 2)[..., None]
        y = srgb_encode(mapped * w + plain * (1 - w))
        g["tonemap_compression"] = float(c)
        sat = look["deep_saturation"]
    # saturation, then desaturate clipped highlights (a limb clipped in every frame would
    # otherwise carry the white-balance multipliers as a coloured fringe)
    yl = lum(y)[..., None]
    y = np.clip(yl + sat * (y - yl), 0, 1)
    # Saturated pixels carry no colour: where any channel approaches its plateau (clipped in
    # every source frame) fade smoothly to a neutral tone at the brightest channel's level.
    # A soft per-channel weight avoids the blocky edge and the cyan/purple rim that a binary
    # "all clipped" mask leaves where only some channels saturated.
    w = cv2.GaussianBlur(w_sat * (1 - rescued), (0, 0), 5.0)
    w = np.maximum(w, np.clip((lum(y) - 0.92) / 0.08, 0, 1))[..., None]
    yn = y.max(-1, keepdims=True)
    y = y * (1 - w) + yn * w
    # sky: neutral and smooth. Just outside the limb the glow sits above the black point, and
    # per-channel noise there shows up as coloured speckles after the sRGB curve.
    ws = (1 - soft_disk(S, g["mcx"], g["mcy"], g["R"] + 3, feather=6.0))[..., None]
    sky = cv2.GaussianBlur(lum(y), (0, 0), 1.2)[..., None]
    y = y * (1 - ws) + sky * ws
    # black point: sky should sit just above zero
    if outer.sum() > 1000:
        bp = np.percentile(lum(y)[outer], 50)
        y = np.clip((y - bp) / (1 - bp), 0, 1)
    return y.astype(np.float32)


def composite(cfg, groups, log):
    """Left-to-right sequence of one image per group, in time order."""
    T = cfg["look"]["composite_thumb"]
    items = [g for g in groups if g.get("final_jpg")]
    n = len(items)
    cols = n if n <= 8 else int(np.ceil(n / 2))
    rows = int(np.ceil(n / cols))
    pad = int(T * 0.06)
    W, H = cols * (T + pad) + pad, rows * (T + pad) + pad
    canvas = np.zeros((H, W, 3), np.uint8)
    layout = []
    for i, g in enumerate(items):
        im = cv2.imread(g["final_jpg"], cv2.IMREAD_COLOR)
        S = im.shape[0]
        c = int(g["R"] * 1.12)
        x0, y0 = int(round(g["mcx"] - c)), int(round(g["mcy"] - c))
        crop = np.zeros((2 * c, 2 * c, 3), np.uint8)
        xs, ys, xe, ye = max(x0, 0), max(y0, 0), min(x0 + 2 * c, S), min(y0 + 2 * c, S)
        crop[ys - y0:ye - y0, xs - x0:xe - x0] = im[ys:ye, xs:xe]
        th = cv2.resize(crop, (T, T), interpolation=cv2.INTER_AREA)
        r, col = divmod(i, cols)
        x, y = pad + col * (T + pad), pad + r * (T + pad)
        canvas[y:y + T, x:x + T] = th
        layout.append(dict(name=g["name"], jpg=g["final_jpg"], x=x, y=y, w=T, h=T,
                           crop_x=x0, crop_y=y0, crop_size=2 * c))
    path = os.path.join(cfg["output_dir"], "final", "00_composite.jpg")
    cv2.imwrite(path, canvas, [cv2.IMWRITE_JPEG_QUALITY, 94])
    log(f"[finish] composite {W}x{H} with {n} phases -> {path}")
    return dict(path=path, W=W, H=H, items=layout)


def run(cfg, groups, log=print):
    outdir = os.path.join(cfg["output_dir"], "final")
    os.makedirs(outdir, exist_ok=True)
    wb = sunlit_wb(cfg, groups, log)
    ref = max((g for g in groups if g.get("stack")), key=lambda g: g["sun_frac"])
    albedo = dict(img=load_linear(ref), mcx=ref["mcx"], mcy=ref["mcy"])
    # measured sliver profile from the best unclipped umbra group (bracket HDR), if any
    meas = [g for g in groups if g.get("stack") and g["long"] and g.get("unclipped_frac", 0) >= 0.995]
    if meas:
        from . import rescue
        b = max(meas, key=lambda g: (g["kind"] == "bracket", g["n_stacked"]))
        albedo["profile"] = rescue.sliver_profile(load_linear(b), b["mcx"], b["mcy"], b["R"])
        d, P = albedo["profile"]
        log(f"[finish] sliver profile from {b['name']}: limb/50px x{P[0]/P[50]:.0f}, limb/100px x{P[0]/P[100]:.0f}")
    for g in groups:
        if not g.get("stack"):
            continue
        y = develop(cfg, g, wb, log, albedo)
        tif = os.path.join(outdir, g["name"] + ".tif")
        jpg = os.path.join(outdir, g["name"] + ".jpg")
        tifffile.imwrite(tif, (y * 65535 + 0.5).astype(np.uint16), photometric="rgb")
        cv2.imwrite(jpg, (y[..., ::-1] * 255 + 0.5).astype(np.uint8), [cv2.IMWRITE_JPEG_QUALITY, 94])
        g["final_tif"], g["final_jpg"] = tif, jpg
        g["label"] = STAGE_LABEL.get(g["stage"], g["stage"])
        log(f"[finish] {g['name']:<24} " + (f"tone-mapped (base x{g['tonemap_compression']:.2f})"
                                             if g.get('tonemap_compression') else 'linear/sRGB'))
    comp = composite(cfg, groups, log)
    from . import phone
    phone.run(cfg, groups, log)
    with open(os.path.join(cfg["output_dir"], "composite.json"), "w", encoding="utf-8") as f:
        json.dump(comp, f, indent=1)
    with open(os.path.join(cfg["output_dir"], "groups.json"), "w", encoding="utf-8") as f:
        json.dump(groups, f, indent=1, ensure_ascii=False)
    return groups
