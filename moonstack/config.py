import json, os

DEFAULTS = {
    "input_dir": "Eclipse",
    "output_dir": "output",
    "cache_dir": "output/cache",
    "raw_ext": [".RAF", ".raf", ".NEF", ".CR2", ".CR3", ".ARW", ".DNG"],
    "crop_size": 1024,
    "workers": 6,
    # sensor geometry (Fuji X-T30 APS-C). Used only as a prior for moon radius.
    "sensor_width_mm": 23.5,
    "moon_diameter_deg": 0.52,
    # frame quality
    "keep_ratio": 0.70,        # keep frames with sharpness >= keep_ratio * best in group
    "min_keep": 3,             # never drop below this many frames per group (if available)
    "max_clip_frac": 0.25,     # reject frame if more than this fraction of the disk is clipped
    "max_trail_px": 6.0,       # reject untracked frames whose expected motion blur exceeds this
    "raw_clip_level": 0.97,    # fraction of white level considered clipped
    # phase grouping
    "full_lit_frac": 0.90,
    "deep_lit_frac": 0.15,      # sunlit fraction below this (in a long exposure) = deepest phase
    "partial_window_s": 240,
    "partial_lit_delta": 0.05,
    "deep_window_s": 420,
    "full_window_s": 600,
    "bracket_gap_s": 20,
    "bracket_min_frames": 5,
    # stacking
    "sigma_clip": 2.5,
    "sigma_iters": 2,
    # PixInsight
    "pixinsight_exe": "C:/Program Files/PixInsight/bin/PixInsight.exe",
    "use_pixinsight": True,
    "bxt": {"enabled": True, "sharpen_nonstellar": 0.45, "nonstellar_psf_diameter": 2.5},
    "nxt": {"enabled": True, "denoise": 0.6, "denoise_total": 0.85, "detail": 0.2},
    # Photoshop
    "photoshop_exe": "C:/Program Files/Adobe/Adobe Photoshop 2025/Photoshop.exe",
    "use_photoshop": True,
    "ps_unsharp": {"amount": 60, "radius": 1.2, "threshold": 1},
    # lateral chromatic aberration: register R and B onto G (affine, so radial scale is handled)
    "ca_correct": True,
    # rebuild blown sunlit slivers of deep-phase stacks from the registered full-moon texture
    "rescue_highlights": True,
    # phone-format composites (portrait)
    "phone": {"width": 1080, "height": 2340, "moons": 6, "cols": 2},
    # stretch / look
    "look": {
        "full_white_pct": 99.8, "full_white_target": 0.92, "saturation": 1.1,
        # deep phase / HDR: local tone mapping (log base/detail split)
        "deep_base_stops": 4.5,      # dynamic range the smooth base layer is compressed to
        "deep_detail_gain": 1.2,     # >1 boosts local contrast (maria, crater rays)
        "deep_saturation": 1.15,
        "deep_highlight": 0.85,      # where the brightest base lands (1.0 = white)
        "composite_thumb": 600,
    },
}


def load(path="config.json"):
    cfg = json.loads(json.dumps(DEFAULTS))
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            user = json.load(f)
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg
