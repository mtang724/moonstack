"""Stage 2: split frames into eclipse-phase groups and pick which frames to stack in each.

Physics-based features per frame (all in radiance units so exposures are comparable):
  ref       sunlit-surface radiance = brightest p99 over all frames
  sun_frac  fraction of the disk that is sunlit (radiance > 0.3 ref, or clipped in a long exposure)
  long      exposure*ISO is in the 'umbra visible' regime
Grouping walks the frames in time order and starts a new group when the phase has visibly
progressed, the exposure regime changed by more than 4 stops, or the time window is exceeded.
Exposure brackets (rapid monotonic exposure ladder) become their own HDR group.
"deep" = the deepest phase captured (totality, or a >90%% partial with a bright sliver).
"""
import os, json
import numpy as np

from . import analyze


def _stops(r):
    return np.log2(1.0 / analyze.rad_scale(r))


def detect_brackets(recs, gap_s, min_n):
    """Return list of index-lists that form an exposure bracket ladder."""
    out, i = [], 0
    while i < len(recs):
        j = i
        while (j + 1 < len(recs) and recs[j + 1]["t"] - recs[j]["t"] <= gap_s
               and abs(_stops(recs[j + 1]) - _stops(recs[j])) >= 0.45):
            j += 1
        if j - i + 1 >= min_n:
            out.append(list(range(i, j + 1)))
            i = j + 1
        else:
            i += 1
    return out


def classify(r, cfg):
    if r["sun_frac"] >= cfg["full_lit_frac"]:
        return "full"
    if r["sun_frac"] <= cfg["deep_lit_frac"] and r["long"]:
        return "deep"
    return "partial"


def sun_fraction(cfg, recs, ref, ref_scale):
    for r in recs:
        if not r.get("ok"):
            continue
        crop, cclip = analyze.load_crop(cfg, r)
        S = crop.shape[0]
        lum = (analyze.raw.luminance(crop) - r["bg"]) * analyze.rad_scale(r)
        disk = analyze.disk_mask(S, r["mcx"], r["mcy"], r["R"])
        sun = disk & ((lum > 0.25 * ref) | cclip)
        r["sun_frac"] = float(sun.sum() / disk.sum())
        # 'long' = exposure regime that records the umbra (>= 6 stops more than a sunlit-moon exposure)
        r["long"] = bool(_stops(r) - np.log2(1.0 / ref_scale) >= 6)
        r["trail_px"] = analyze.trail_px(r, cfg)


def run(cfg, recs, log=print):
    ok = sorted([r for r in recs if r.get("ok")], key=lambda r: r["t"])
    ref_rec = max(ok, key=lambda r: r["p99"])
    ref = ref_rec["p99"]
    sun_fraction(cfg, ok, ref, analyze.rad_scale(ref_rec))
    log(f"[group] sunlit reference radiance = {ref:.4g}")

    used = set()
    groups = []
    for idx in detect_brackets(ok, cfg["bracket_gap_s"], cfg["bracket_min_frames"]):
        groups.append({"kind": "bracket", "idx": idx})
        used.update(idx)

    cur = None
    for i, r in enumerate(ok):
        if i in used:
            continue
        kind = classify(r, cfg)
        window = {"full": cfg["full_window_s"], "deep": cfg["deep_window_s"],
                  "partial": cfg["partial_window_s"]}[kind]
        if cur is not None:
            first = ok[cur["idx"][0]]
            same = (kind == cur["kind"]
                    and r["t"] - first["t"] <= window
                    and abs(_stops(r) - np.median([_stops(ok[k]) for k in cur["idx"]])) <= 4
                    and abs(r["sun_frac"] - first["sun_frac"]) <= cfg["partial_lit_delta"])
            if not same:
                groups.append(cur); cur = None
        if cur is None:
            cur = {"kind": kind, "idx": [i]}
        else:
            cur["idx"].append(i)
    if cur:
        groups.append(cur)
    groups.sort(key=lambda g: ok[g["idx"][0]]["t"])

    # Frame selection inside each group.
    def session_best(f):
        # best sharpness among all frames of similar exposure in the whole session: catches a
        # shaken frame that happens to be alone in its group
        same = [x["sharp"] for x in ok if abs(_stops(x) - _stops(f)) <= 0.6]
        return max(same) if same else f["sharp"]

    t_deep = [ok[g["idx"][0]]["t"] for g in groups if g["kind"] == "deep"]
    for n, g in enumerate(groups, 1):
        frames = [ok[k] for k in g["idx"]]
        t0 = frames[0]["t"]
        stage = g["kind"]
        if stage == "partial" and t_deep:
            stage = "partial_in" if t0 < min(t_deep) else "partial_out"
        g["stage"] = stage
        g["name"] = f"{n:02d}_{stage}_{frames[0]['datetime'][11:16].replace(':', '')}"
        g["files"] = [f["file"] for f in frames]
        g["t_start"], g["t_end"] = frames[0]["datetime"], frames[-1]["datetime"]
        g["sun_frac"] = float(np.mean([f["sun_frac"] for f in frames]))
        g["long"] = bool(np.median([f["long"] for f in frames]) > 0.5)
        g["exposures"] = sorted(set(f"{f['exp']:.4g}s/ISO{f['iso']:.0f}/f{f['fnum']:.3g}" for f in frames))
        del g["idx"]
        best = max(f["sharp"] for f in frames)

        def best_similar(f):
            # compare sharpness only against frames of similar exposure: a 1/8 s frame is
            # always crisper than a 0.4 s one, but the long one carries the umbra signal
            same = [x["sharp"] for x in frames if abs(_stops(x) - _stops(f)) <= 0.6]
            return max(same) if same else best

        keep, reject = [], {}
        ranked = sorted(frames, key=lambda f: -f["sharp"])
        for f in ranked:
            why = []
            if f["clip_frac"] > cfg["max_clip_frac"]:
                why.append(f"clipped {f['clip_frac']*100:.0f}%")
            if f["trail_px"] > cfg["max_trail_px"]:
                why.append(f"trailed ~{f['trail_px']:.1f}px")
            sb = session_best(f)
            hard = f["clip_frac"] > cfg["max_clip_frac"] or f["trail_px"] > cfg["max_trail_px"]
            if sb > 0 and f["sharp"] < 0.45 * sb:
                why.append(f"shaken ({f['sharp']/sb:.2f} of session best)"); hard = True
            b = best_similar(f)
            if g["kind"] != "bracket" and b > 0 and f["sharp"] < cfg["keep_ratio"] * b:
                why.append(f"soft ({f['sharp']/b:.2f} of best)")
            if why and len(keep) >= cfg["min_keep"]:
                reject[f["file"]] = ", ".join(why)
            elif why and hard:
                reject[f["file"]] = ", ".join(why)
            else:
                keep.append(f["file"])
                if why:
                    f["note"] = "kept (min_keep): " + ", ".join(why)
        g["keep"] = sorted(keep)
        g["reject"] = reject
        g["weights"] = {f["file"]: (f["sharp"] / best_similar(f) if best_similar(f) > 0 else 1.0) for f in frames}
        log(f"[group] {g['name']:<24} {len(frames):>2} frames, keep {len(keep):>2}  "
            f"sun={g['sun_frac']:.2f}  {g['t_start'][11:]}..{g['t_end'][11:]}"
            + (f"  rejected: {list(reject)}" if reject else ""))

    empty = [g for g in groups if not g["keep"]]
    for g in empty:
        log(f"[group] {g['name']} dropped: every frame rejected ({g['reject']})")
    groups = [g for g in groups if g["keep"]]
    for n, g in enumerate(groups, 1):
        g["name"] = f"{n:02d}_" + g["name"].split("_", 1)[1]
    with open(os.path.join(cfg["output_dir"], "groups.json"), "w", encoding="utf-8") as f:
        json.dump(groups, f, indent=1, ensure_ascii=False)
    analyze.save(cfg, recs)
    return groups
