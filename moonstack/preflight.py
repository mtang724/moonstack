"""Stage 0: look at the camera, optics, exposures and installed tools, then adapt the config.

    python run.py --stage preflight      (also runs first in a full `python run.py`)

What it decides, and from what:
  sensor width / pixel pitch   EXIF FocalLengthIn35mmFilm (crop factor) -> camera table -> default + warning
  focal length                 EXIF, or config focal_mm for telescopes / manual lenses
  moon diameter in pixels      focal, pitch, 0.52 deg  -> crop_size grown for big moons (telescopes)
  tracked or not               moon drift measured between two close frames vs. 14.5"/s sidereal
  exposure regimes             clusters of log2(N^2 / (t ISO)); bracket ladders
  tools                        PixInsight, Photoshop, exiftool, CPU count
Writes output/preflight.json and, if no config.json exists yet, a config.json with the adapted values.
"""
import os, json, shutil
import numpy as np

from . import exif, raw, detect, analyze

# sensor width (mm) by model keyword, used only when EXIF has no 35 mm-equivalent focal length
CAMERA_TABLE = [
    (("X-T", "X-H", "X-S", "X-E", "X-PRO", "X100", "GFX50", "GFX100"), 23.5),
    (("GFX",), 43.8),
    (("EOS R5", "EOS R6", "EOS R3", "EOS R8", "EOS R", "EOS 5D", "EOS 6D", "EOS-1D", "EOS RP"), 36.0),
    (("EOS R7", "EOS R10", "EOS R50", "EOS 90D", "EOS 80D", "EOS 77D", "EOS 850", "EOS 250", "EOS M", "Rebel"), 22.3),
    (("Z 5", "Z 6", "Z 7", "Z 8", "Z 9", "Z f", "D850", "D780", "D750", "D810", "D6", "D5", "D4"), 35.9),
    (("Z 50", "Z fc", "Z 30", "D7500", "D7200", "D5600", "D3500", "D500"), 23.5),
    (("ILCE-7", "ILCE-9", "ILCE-1", "ZV-E1"), 35.8),
    (("ILCE-6", "ZV-E10", "ILCE-5"), 23.5),
    (("OM-1", "OM-5", "E-M1", "E-M5", "E-M10", "DC-G9", "DC-GH", "DC-G100", "DMC-G"), 17.3),
    (("K-1",), 35.9), (("K-3", "K-70", "KP", "K-S"), 23.5),
    (("DC-S1", "DC-S5", "SIGMA fp", "LEICA SL", "LEICA M", "LEICA Q"), 36.0),
]


def sensor_width_from(meta, W):
    if meta.get("focal") and meta.get("focal35"):
        crop = meta["focal35"] / meta["focal"]
        if 0.9 < crop < 8:
            return 36.0 / crop, f"EXIF 35mm-equivalent focal ({meta['focal35']:.0f} mm -> crop {crop:.2f}x)"
    model = (meta.get("model") or "").upper()
    for keys, w in CAMERA_TABLE:
        if any(k.upper() in model for k in keys):
            return w, f"camera table ({meta.get('model')})"
    return None, None


def measure_drift(files, metas, cfg, log):
    """Moon centre motion between two frames of the same exposure regime shot 3-90 s apart,
    in px/s. Untracked at this scale is ~5 px/s; tracked is ~0."""
    order = np.argsort([m["t"] for m in metas])
    for i in range(len(order) - 1):
        a, b = metas[order[i]], metas[order[i + 1]]
        dt = b["t"] - a["t"]
        if 3 <= dt <= 90 and abs(np.log2(analyze.rad_scale(a) / analyze.rad_scale(b))) < 0.6:
            cs = []
            for m in (a, b):
                rgb, _ = raw.decode(files[metas.index(m)], cfg["raw_clip_level"])
                R = detect.radius_prior_px(m, analyze.pixel_pitch_mm(cfg, rgb.shape[1]), cfg["moon_diameter_deg"])
                c, *_ = detect.find_moon(raw.luminance(rgb), R)
                if c is None:
                    break
                cs.append(c)
            if len(cs) == 2:
                d = float(np.hypot(cs[1][0] - cs[0][0], cs[1][1] - cs[0][1]) / dt)
                return d, a["file"], b["file"], dt
    return None, None, None, None


def run(cfg, log=print, write_config=True):
    files = analyze.list_raws(cfg)
    if not files:
        raise SystemExit(f"no RAW files in {cfg['input_dir']} (extensions: {', '.join(cfg['raw_ext'])})")
    metas = [exif.read(f) for f in files]
    m0 = metas[0]
    if cfg.get("focal_mm"):
        for m in metas:
            m["focal"] = float(cfg["focal_mm"])
    rep = {"frames": len(files), "model": m0.get("model"), "format": os.path.splitext(files[0])[1].upper(),
           "sidecar_jpg": os.path.exists(os.path.splitext(files[0])[0] + ".JPG") or os.path.exists(os.path.splitext(files[0])[0] + ".jpg")}
    warn = []
    import rawpy
    with rawpy.imread(files[0]) as r:
        W, H = r.sizes.width, r.sizes.height
        pat = r.raw_pattern.shape
        rep.update(width=W, height=H, cfa=("X-Trans 6x6" if pat[0] == 6 else f"Bayer {pat[0]}x{pat[1]}"),
                   bits=int(np.ceil(np.log2(r.white_level + 1))))
    # sensor geometry
    if cfg.get("pixel_pitch_um"):
        pitch_mm, src = cfg["pixel_pitch_um"] / 1000.0, "config pixel_pitch_um"
    else:
        sw, src = sensor_width_from(m0, W)
        if sw is None:
            sw, src = cfg["sensor_width_mm"], "config default - CHECK THIS"
            warn.append(f"sensor width unknown for '{m0.get('model')}': using {sw} mm; set sensor_width_mm or pixel_pitch_um")
        cfg["sensor_width_mm"] = float(sw)
        pitch_mm = sw / W
    rep.update(sensor_width_mm=round(pitch_mm * W, 2), pixel_pitch_um=round(pitch_mm * 1000, 3), sensor_source=src)
    # optics
    focal = m0.get("focal") or 0.0
    if not focal:
        raise SystemExit("no focal length in EXIF (telescope / manual lens?): set \"focal_mm\" in config.json")
    scale = 206265.0 * pitch_mm / focal
    D = 2 * detect.radius_prior_px(m0, pitch_mm, cfg["moon_diameter_deg"])
    rep.update(focal_mm=focal, arcsec_per_px=round(scale, 3), moon_diameter_px=round(D))
    if D < 150:
        warn.append(f"moon is only ~{D:.0f} px across: stacking works but limb fits get coarse; results will be soft")
    if D * 1.4 > cfg["crop_size"]:
        cfg["crop_size"] = int(np.ceil(D * 1.5 / 256) * 256)
        rep["crop_size"] = cfg["crop_size"]
    if D * 1.2 > min(W, H):
        warn.append("moon nearly fills the frame - alignment margins are thin; expect edge effects")
    # exposures
    stops = np.array([np.log2(1.0 / analyze.rad_scale(m)) for m in metas])
    regimes = []
    for s in sorted(set(np.round(stops / 2) * 2)):
        sel = np.abs(stops - s) <= 1.0
        exps = sorted({f"{m['exp']:.4g}s/ISO{m['iso']:.0f}/f{m['fnum']:.3g}" for m, ok in zip(metas, sel) if ok})
        regimes.append({"stops": float(s), "frames": int(sel.sum()), "examples": exps[:4]})
    from .phases import detect_brackets
    srt = sorted(metas, key=lambda m: m["t"])
    br = detect_brackets(srt, cfg["bracket_gap_s"], cfg["bracket_min_frames"])
    t0, t1 = min(m["t"] for m in metas), max(m["t"] for m in metas)
    rep.update(exposure_regimes=regimes, bracket_ladders=[len(b) for b in br],
               span_min=round((t1 - t0) / 60, 1), first=srt[0]["datetime"], last=srt[-1]["datetime"],
               shortest_exposure=min(m["exp"] for m in metas), longest_exposure=max(m["exp"] for m in metas))
    if len(regimes) == 1:
        warn.append("only one exposure regime: either no umbra data (all short) or no sunlit-limb data (all long)")
    if not br:
        warn.append("no exposure-bracket ladder found: a blown sunlit sliver in the deep phase cannot be rebuilt from measured data")
    # tracking
    if cfg.get("tracked") is None or not cfg.get("tracked"):
        drift, fa, fb, dt = measure_drift(files, metas, cfg, log)
        expected = 14.5 / scale
        if drift is not None:
            rep.update(drift_px_per_s=round(drift, 2), untracked_drift_px_per_s=round(expected, 2), drift_pair=[fa, fb, dt])
            if drift < 0.25 * expected:
                cfg["tracked"] = True
                rep["tracked"] = True
                warn.append(f"moon drift {drift:.2f} px/s vs {expected:.2f} expected untracked -> treating as TRACKED (set tracked=false to override)")
            else:
                rep["tracked"] = False
    # tools
    cfg["workers"] = max(2, min(cfg.get("workers", 6), (os.cpu_count() or 4) - 1))
    rep.update(workers=cfg["workers"], pixinsight=os.path.exists(cfg["pixinsight_exe"]),
               photoshop=os.path.exists(cfg["photoshop_exe"]), exiftool=bool(shutil.which("exiftool")))
    cfg["use_pixinsight"] = cfg["use_pixinsight"] and rep["pixinsight"]
    cfg["use_photoshop"] = cfg["use_photoshop"] and rep["photoshop"]
    if not rep["pixinsight"]:
        warn.append("PixInsight not found: no BXT/NXT denoise - umbra will be noisier")
    if not rep["sidecar_jpg"] and not rep["exiftool"] and rep["format"] in (".CR3",):
        warn.append("CR3 without JPG sidecars needs exiftool on PATH")
    rep["warnings"] = warn

    log(f"[preflight] {rep['frames']} x {rep['format']} from {rep['model']}  {W}x{H} {rep['cfa']} {rep['bits']}-bit"
        f"{'  (JPG sidecars)' if rep['sidecar_jpg'] else ''}")
    log(f"[preflight] sensor {rep['sensor_width_mm']} mm / {rep['pixel_pitch_um']} um px  <- {src}")
    log(f"[preflight] focal {focal:.0f} mm -> {scale:.2f}\"/px, moon ~{D:.0f} px, crop {cfg['crop_size']} px")
    log(f"[preflight] {rep['span_min']} min, {rep['first'][11:]}..{rep['last'][11:]}, exposures {rep['shortest_exposure']:.4g}-{rep['longest_exposure']:.4g}s")
    for rg in regimes:
        log(f"[preflight]   regime {rg['stops']:+.0f} stops: {rg['frames']} frames  e.g. {', '.join(rg['examples'])}")
    log(f"[preflight] bracket ladders: {rep['bracket_ladders'] or 'none'}")
    if "drift_px_per_s" in rep:
        log(f"[preflight] drift {rep['drift_px_per_s']} px/s (untracked would be {rep['untracked_drift_px_per_s']}) -> {'tracked' if rep.get('tracked') else 'untracked'}")
    log(f"[preflight] tools: PixInsight={rep['pixinsight']} Photoshop={rep['photoshop']} exiftool={rep['exiftool']} workers={cfg['workers']}")
    for w in warn:
        log(f"[preflight] WARNING: {w}")
    os.makedirs(cfg["output_dir"], exist_ok=True)
    with open(os.path.join(cfg["output_dir"], "preflight.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, ensure_ascii=False)
    if write_config and not os.path.exists("config.json"):
        adapted = {k: cfg[k] for k in ("input_dir", "sensor_width_mm", "pixel_pitch_um", "focal_mm", "tracked",
                                       "crop_size", "workers", "use_pixinsight", "use_photoshop")}
        with open("config.json", "w", encoding="utf-8") as f:
            json.dump(adapted, f, indent=2)
        log("[preflight] wrote config.json with the adapted values (edit it to override)")
    return rep
