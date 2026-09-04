"""Phone-format composites (portrait) from the developed phase images.

Picks a small, visually distinct subset of phases: the first (fullest) group, the last group,
the best umbra group (most frames; a bracket HDR group wins), then fills up by maximising the
spread in sunlit fraction, preferring groups whose sliver was measured rather than rebuilt. Two layouts:
  phone_arc.jpg   the chosen phases along an arc, top to bottom in time order
  phone_hero.jpg  the best umbra image large, the sequence as a small row underneath
"""
import os, json
import numpy as np
import cv2
import tifffile


def select(groups, n):
    cands = [g for g in groups if g.get("final_jpg")]
    cands.sort(key=lambda g: g["t_start"])
    if len(cands) <= n:
        return cands
    umbra = [g for g in cands if g["long"]]
    best = max(umbra, key=lambda g: (g["kind"] == "bracket", g["n_stacked"])) if umbra else None
    chosen = [cands[0], cands[-1]] + ([best] if best and best not in (cands[0], cands[-1]) else [])
    # prefer phases made purely of measured data; groups whose blown sliver was rebuilt from
    # the full-moon texture only fill in when nothing else is left
    real = [g for g in cands if not g.get("rescued_px")]
    while len(chosen) < n:
        rest = [g for g in real if g not in chosen] or [g for g in cands if g not in chosen]
        if not rest:
            break
        def dist(g):
            return min(abs(g["sun_frac"] - c["sun_frac"]) + 0.02 * (g["long"] != c["long"]) for c in chosen)
        chosen.append(max(rest, key=dist))
    chosen.sort(key=lambda g: g["t_start"])
    return chosen


def moon_tile(g, size):
    """Square crop around the disk, resized, with a soft circular fade so it composites on black."""
    # 16-bit TIFF when available (lossless chain), JPG otherwise; work in float 0..1, BGR
    if g.get("final_tif") and os.path.exists(g["final_tif"]):
        im = tifffile.imread(g["final_tif"]).astype(np.float32)[..., ::-1] / 65535.0
    else:
        im = cv2.imread(g["final_jpg"], cv2.IMREAD_COLOR).astype(np.float32) / 255.0
    S = im.shape[0]
    c = int(g["R"] * 1.10)
    x0, y0 = int(round(g["mcx"] - c)), int(round(g["mcy"] - c))
    crop = np.zeros((2 * c, 2 * c, 3), np.float32)
    xs, ys, xe, ye = max(x0, 0), max(y0, 0), min(x0 + 2 * c, S), min(y0 + 2 * c, S)
    crop[ys - y0:ye - y0, xs - x0:xe - x0] = im[ys:ye, xs:xe]
    t = cv2.resize(crop, (size, size), interpolation=cv2.INTER_AREA).astype(np.float32)
    yy, xx = np.mgrid[0:size, 0:size]
    r = np.hypot(xx - size / 2 + 0.5, yy - size / 2 + 0.5) / (size / 2)
    fade = np.clip((1.0 - r) / 0.06, 0, 1)[..., None]      # fade only in the outer 6%
    return t * fade


def paste(canvas, tile, x, y):
    h, w = tile.shape[:2]
    H, W = canvas.shape[:2]
    xs, ys, xe, ye = max(x, 0), max(y, 0), min(x + w, W), min(y + h, H)
    canvas[ys:ye, xs:xe] = np.maximum(canvas[ys:ye, xs:xe], tile[ys - y:ye - y, xs - x:xe - x])


def caption(canvas, text, y, size=0.9, color=(150, 150, 160)):
    """draw anti-aliased text onto a float canvas via an 8-bit mask"""
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, size, 1)
    m = np.zeros(canvas.shape[:2], np.uint8)
    cv2.putText(m, text, ((canvas.shape[1] - tw) // 2, y), cv2.FONT_HERSHEY_DUPLEX, size, 255, 1, cv2.LINE_AA)
    a = (m.astype(np.float32) / 255.0)[..., None]
    col = np.array(color, np.float32)[None, None, :] / 255.0
    canvas[:] = canvas * (1 - a) + col * a


def save(path_base, canvas):
    """canvas float 0..1 BGR -> 16-bit PNG (lossless) + 8-bit JPG"""
    c = np.clip(canvas, 0, 1)
    cv2.imwrite(path_base + ".png", (c * 65535 + 0.5).astype(np.uint16))
    cv2.imwrite(path_base + ".jpg", (c * 255 + 0.5).astype(np.uint8), [cv2.IMWRITE_JPEG_QUALITY, 95])


def arc_layout(chosen, W, H, label):
    n = len(chosen)
    d = int(min(W * 0.24, (H * 0.80) / n * 1.0))
    canvas = np.zeros((H, W, 3), np.float32)
    top, bottom = int(H * 0.09), int(H * 0.89)
    amp = W * 0.24
    for i, g in enumerate(chosen):
        t = i / max(n - 1, 1)
        cy = int(top + t * (bottom - top))
        cx = int(W / 2 - amp * np.cos(np.pi * t))                # sweep left -> right
        paste(canvas, moon_tile(g, d), cx - d // 2, cy - d // 2)
    caption(canvas, label, int(H * 0.955))
    return canvas


def grid_layout(chosen, W, H, label, cols=2):
    """Rows of `cols` moons, as large as the width allows, time order left-to-right, top-to-bottom."""
    n = len(chosen)
    rows = int(np.ceil(n / cols))
    gap = int(W * 0.04)
    d = int((W - gap * (cols + 1)) / cols)
    block = rows * d + (rows - 1) * gap
    top = max((H - block) // 2 - int(H * 0.02), gap)
    d = min(d, int((H - 2 * gap - int(H * 0.06) - (rows - 1) * gap) / rows))
    block = rows * d + (rows - 1) * gap
    top = (H - int(H * 0.05) - block) // 2
    canvas = np.zeros((H, W, 3), np.float32)
    x0 = (W - (cols * d + (cols - 1) * gap)) // 2
    for i, g in enumerate(chosen):
        r, c = divmod(i, cols)
        paste(canvas, moon_tile(g, d), x0 + c * (d + gap), top + r * (d + gap))
    caption(canvas, label, top + block + int(H * 0.035), 0.85)
    return canvas


def hero_layout(chosen, hero, W, H, label):
    canvas = np.zeros((H, W, 3), np.float32)
    D = int(W * 0.78)
    paste(canvas, moon_tile(hero, D), (W - D) // 2, int(H * 0.30) - D // 2 + int(H * 0.06))
    row = [g for g in chosen if g is not hero] or chosen
    n = len(row)
    gap = int(W * 0.02)
    d = min(int((W - gap * (n + 1)) / n), int(W * 0.14))
    x = (W - (n * d + (n - 1) * gap)) // 2
    y = int(H * 0.72)
    for g in row:
        paste(canvas, moon_tile(g, d), x, y)
        x += d + gap
    caption(canvas, label, int(H * 0.80) + d // 2 + 10, 0.8)
    return canvas


def run(cfg, groups, log=print):
    pc = cfg.get("phone", {})
    W, H = pc.get("width", 1080), pc.get("height", 2340)
    n = pc.get("moons", 8)
    chosen = select(groups, n)
    umbra = [g for g in chosen if g["long"]]
    hero = max(umbra, key=lambda g: (g["kind"] == "bracket", g["n_stacked"])) if umbra else chosen[-1]
    date = chosen[0]["t_start"][:10].replace(":", ".")
    label = pc.get("caption", f"{date}   LUNAR ECLIPSE")
    outdir = os.path.join(cfg["output_dir"], "final")
    p0 = os.path.join(outdir, "phone_grid"); p1 = os.path.join(outdir, "phone_arc"); p2 = os.path.join(outdir, "phone_hero")
    save(p0, grid_layout(chosen, W, H, label, pc.get("cols", 2)))
    save(p1, arc_layout(chosen, W, H, label))
    save(p2, hero_layout(chosen, hero, W, H, label))
    log(f"[phone] {W}x{H}: {len(chosen)} phases {[g['name'] for g in chosen]} -> {p0}.png/.jpg, {p1}, {p2}")
    return chosen


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from . import config
    cfg = config.load()
    groups = json.load(open(os.path.join(cfg["output_dir"], "groups.json"), encoding="utf-8"))
    run(cfg, groups)
