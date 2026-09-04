"""Stage 7: self-contained HTML report - composite, one card per phase group with the final
image, every source frame's thumbnail, quality score and keep/reject reason."""
import os, base64, html
import numpy as np
import cv2

LABEL_ZH = {"full": "满月 / 半影", "partial_in": "偏食（初亏→食甚）", "deep": "食甚附近（深偏食）",
            "bracket": "包围曝光 HDR", "partial_out": "偏食（生光→复圆）"}


def b64_jpg(path, size=None, q=85):
    im = cv2.imread(path, cv2.IMREAD_COLOR)
    if im is None:
        return ""
    if size:
        im = cv2.resize(im, (size, int(im.shape[0] * size / im.shape[1])), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, q])
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()


def run(cfg, recs, groups, log=print):
    byname = {r["file"]: r for r in recs}
    thumbs = os.path.join(cfg["cache_dir"], "thumbs")
    comp = os.path.join(cfg["output_dir"], "final", "00_composite.jpg")
    n_frames = sum(len(g["files"]) for g in groups)
    n_keep = sum(len(g["keep"]) for g in groups)
    css = """
    body{font-family:system-ui,Segoe UI,sans-serif;background:#0d0f14;color:#e6e6e6;margin:0;padding:24px}
    h1{font-weight:600;margin:0 0 4px} .sub{color:#9aa;margin-bottom:20px}
    .card{background:#161a22;border-radius:12px;padding:16px;margin:16px 0;display:grid;grid-template-columns:420px 1fr;gap:18px}
    .card img.final{width:420px;border-radius:8px;background:#000}
    .card h2{margin:0 0 6px;font-size:18px} .meta{color:#9aa;font-size:13px;margin-bottom:10px}
    .frames{display:flex;flex-wrap:wrap;gap:6px}
    .fr{width:104px;font-size:11px;text-align:center;color:#bbb}
    .fr img{width:100px;height:100px;border-radius:4px;border:2px solid #2d4;background:#000}
    .fr.rej img{border-color:#c33;opacity:.6} .fr.ref img{border-color:#fc3}
    .fr .why{color:#e77} .fr .ref{color:#fc3}
    .comp{width:100%;max-width:1400px;border-radius:10px}
    code{color:#9cf}
    """
    out = [f"<!doctype html><html><head><meta charset='utf-8'><title>MoonStack report</title><style>{css}</style></head><body>",
           f"<h1>MoonStack · 月食堆栈报告</h1>",
           f"<div class='sub'>{len(groups)} 个阶段 · {n_frames} 张原片，堆栈使用 {n_keep} 张，剔除 {n_frames-n_keep} 张 · 输出目录 <code>{html.escape(os.path.abspath(cfg['output_dir']))}</code></div>"]
    if os.path.exists(comp):
        out.append(f"<img class='comp' src='{b64_jpg(comp, 1400)}'>")
    for g in groups:
        final = g.get("final_jpg")
        img = f"<img class='final' src='{b64_jpg(final, 420)}'>" if final and os.path.exists(final) else "<div></div>"
        exps = ", ".join(g.get("exposures", []))
        meta = (f"{g['t_start'][11:]} – {g['t_end'][11:]} · {len(g['files'])} 张 / 用 {len(g['keep'])} 张 · "
                f"本影覆盖约 {(1-g['sun_frac'])*100:.0f}% · 曝光 {html.escape(exps)}")
        if g.get("shifts"):
            mx = max(max(abs(s["dx"]), abs(s["dy"])) for s in g["shifts"].values())
            meta += f" · 最大对齐位移 {mx:.1f}px"
        if g.get("pi_out"):
            meta += " · PixInsight BXT+NXT ✓"
        if g.get("psd"):
            meta += " · PSD ✓"
        frs = []
        for f in g["files"]:
            r = byname[f]
            stem = os.path.splitext(f)[0]
            t = os.path.join(thumbs, stem + ".png")
            cls = "fr" + (" rej" if f in g["reject"] else "") + (" ref" if f == g.get("ref") else "")
            why = f"<div class='why'>{html.escape(g['reject'][f])}</div>" if f in g["reject"] else ""
            refm = "<div class='ref'>参考帧</div>" if f == g.get("ref") else ""
            frs.append(f"<div class='{cls}'><img src='{b64_jpg(t, 100)}'>{stem}<br>"
                       f"{r['exp']:.4g}s ISO{r['iso']:.0f}<br>清晰度 {r['sharp']*1e4:.2f}{why}{refm}</div>")
        out.append(f"<div class='card'>{img}<div><h2>{g['name']} · {LABEL_ZH.get(g['stage'], g['stage'])}</h2>"
                   f"<div class='meta'>{meta}</div><div class='frames'>{''.join(frs)}</div></div></div>")
    out.append("<div class='sub' style='margin-top:24px'>绿框 = 参与堆栈，红框 = 剔除，黄框 = 对齐参考帧。清晰度为去噪后的拉普拉斯能量（×1e4），只在相近曝光之间比较。</div>")
    out.append("</body></html>")
    path = os.path.join(cfg["output_dir"], "report.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    log(f"[report] -> {path} ({os.path.getsize(path)/1e6:.1f} MB)")
    return path
