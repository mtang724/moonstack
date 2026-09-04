"""Shareable single-file page (Artifact-ready: no <html>/<head>/<body> wrapper) built from output/.
    python -m moonstack.share   -> output/share.html
"""
import os, sys, json, html
from . import config, report

LABEL = {"full": "满月 · 半影", "partial_in": "偏食 · 初亏后", "deep": "食甚附近 · 深偏食",
         "bracket": "包围曝光 HDR", "partial_out": "偏食 · 生光后"}

CSS = """
<style>
:root{--bg:#0a0c11;--sur:#11141c;--sur2:#171b25;--rule:#242a37;--ink:#ece7dd;--ink2:#a9adb8;--mute:#6d7280;
      --copper:#d47d3c;--copper2:#8a4a20;--moon:#cfd3dc;--ok:#7fb069;--bad:#c25b4e;--ref:#e0b45a}
body{background:var(--bg);color:var(--ink);font-family:"Source Sans 3","Segoe UI",system-ui,sans-serif;font-size:15px;line-height:1.5;margin:0}
.wrap{max-width:1180px;margin:0 auto;padding:36px 28px 80px}
header{display:grid;grid-template-columns:1fr auto;gap:24px;align-items:end;margin-bottom:20px}
h1{font-family:"Cormorant Garamond",Georgia,serif;font-weight:600;font-size:46px;line-height:1.05;margin:0;letter-spacing:.01em;text-wrap:balance}
h1 em{font-style:italic;color:var(--copper)}
.lede{color:var(--ink2);max-width:62ch;margin:10px 0 0}
.facts{display:grid;grid-template-columns:repeat(4,auto);gap:0 28px;text-align:right;font-variant-numeric:tabular-nums}
.facts b{display:block;font-family:"Cormorant Garamond",Georgia,serif;font-size:30px;font-weight:600;line-height:1;color:var(--moon)}
.facts span{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--mute)}
.strip{background:#000;border-radius:6px;overflow-x:auto;margin:18px 0 8px}
.strip img{display:block;width:100%;min-width:900px}
.cap{font-size:12px;color:var(--mute);letter-spacing:.04em;margin-bottom:40px}
.tl{position:relative;padding-left:96px}
.tl:before{content:"";position:absolute;left:70px;top:0;bottom:0;width:1px;background:var(--rule)}
.ph{display:grid;grid-template-columns:340px 1fr;gap:22px;position:relative;padding:18px 0 34px}
.ph .t{position:absolute;left:-96px;top:26px;width:60px;text-align:right;font-family:"JetBrains Mono",Consolas,monospace;font-size:12px;color:var(--ink2);font-variant-numeric:tabular-nums}
.ph .t:after{content:"";position:absolute;right:-30px;top:7px;width:7px;height:7px;border-radius:50%;background:var(--moon);box-shadow:0 0 0 3px var(--bg)}
.ph.deep .t:after,.ph.bracket .t:after{background:var(--copper)}
.ph img.main{width:340px;height:340px;border-radius:6px;background:#000;display:block}
.ph h2{font-family:"Cormorant Garamond",Georgia,serif;font-weight:600;font-size:26px;margin:0 0 2px;line-height:1.1}
.ph h2 small{font-family:"JetBrains Mono",Consolas,monospace;font-size:12px;color:var(--mute);font-weight:400;margin-left:10px}
.kv{display:grid;grid-template-columns:auto 1fr;gap:3px 14px;font-size:13px;color:var(--ink2);margin:10px 0 12px;max-width:62ch}
.kv dt{color:var(--mute);letter-spacing:.06em;text-transform:uppercase;font-size:11px;padding-top:2px}
.kv dd{margin:0;font-family:"JetBrains Mono",Consolas,monospace;font-size:12.5px;font-variant-numeric:tabular-nums}
.frames{display:flex;flex-wrap:wrap;gap:6px}
.fr{width:78px;font-family:"JetBrains Mono",Consolas,monospace;font-size:10px;color:var(--ink2);text-align:center;line-height:1.35}
.fr img{width:78px;height:78px;border-radius:4px;display:block;background:#000;box-shadow:inset 0 0 0 2px var(--ok)}
.fr.rej img{box-shadow:inset 0 0 0 2px var(--bad);opacity:.55}
.fr.ref img{box-shadow:inset 0 0 0 2px var(--ref)}
.fr .why{color:var(--bad)} .fr .refl{color:var(--ref)}
.legend{font-size:12px;color:var(--mute);margin-top:8px;display:flex;gap:18px;flex-wrap:wrap}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}
.ph-h{font-family:"Cormorant Garamond",Georgia,serif;font-size:24px;font-weight:600;margin:28px 0 12px}
.phones{display:flex;gap:24px;flex-wrap:wrap}.phones img{width:270px;border-radius:14px;background:#000}
.method{border-top:1px solid var(--rule);margin-top:30px;padding-top:22px;columns:2;column-gap:40px;font-size:14px;color:var(--ink2)}
.method h3{font-family:"Cormorant Garamond",Georgia,serif;font-size:22px;font-weight:600;color:var(--ink);margin:0 0 8px;column-span:all}
.method p{margin:0 0 10px;break-inside:avoid}
.method b{color:var(--ink);font-weight:600}
@media (max-width:820px){.ph{grid-template-columns:1fr}.ph img.main{width:100%;height:auto}.tl{padding-left:0}.tl:before{display:none}.ph .t{position:static;width:auto;text-align:left}.ph .t:after{display:none}.facts{grid-template-columns:repeat(2,auto);text-align:left}header{grid-template-columns:1fr}.method{columns:1}}
</style>
"""


def build(cfg):
    recs = json.load(open(os.path.join(cfg["output_dir"], "frames.json"), encoding="utf-8"))
    groups = json.load(open(os.path.join(cfg["output_dir"], "groups.json"), encoding="utf-8"))
    byname = {r["file"]: r for r in recs}
    thumbs = os.path.join(cfg["cache_dir"], "thumbs")
    comp = os.path.join(cfg["output_dir"], "final", "00_composite.jpg")
    n_frames = sum(len(g["files"]) for g in groups)
    n_keep = sum(len(g["keep"]) for g in groups)
    deepest = max((1 - g["sun_frac"]) for g in groups if g["stage"] in ("deep", "bracket"))
    date = recs[0]["datetime"][:10].replace(":", "-")
    model = recs[0].get("model", "")
    out = ["<title>Blood Moon Ledger</title>",
           '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,600;1,600&family=Source+Sans+3:wght@400;600&family=JetBrains+Mono:wght@400&display=swap">',
           CSS, '<div class="wrap">',
           f"<header><div><h1>{date} 月食<br><em>{len(groups)} 个阶段，{n_frames} 张 RAW</em></h1>"
           f"<p class='lede'>{html.escape(model)} · 300 mm · 固定三脚架。每个阶段由同一时间窗内的原片对齐、按辐射度加权堆栈；"
           f"深偏食与包围曝光组在线性域完成 HDR 合成后做局部色调映射。日照月面定义为中性灰，本影的红是相对它的真实色。</p></div>",
           f"<div class='facts'><div><b>{n_keep}</b><span>参与堆栈</span></div><div><b>{n_frames-n_keep}</b><span>剔除</span></div>"
           f"<div><b>{deepest*100:.0f}%</b><span>最深本影覆盖</span></div><div><b>{recs[0]['R']*2:.0f}px</b><span>月盘直径</span></div></div></header>"]
    if os.path.exists(comp):
        out.append(f"<div class='strip'><img src='{report.b64_jpg(comp, 1800, 88)}' alt='eclipse sequence'></div>"
                   "<div class='cap'>全过程 · 时间从左到右，上排短曝光（日照面），下排长曝光（本影）</div>")
    out.append("<div class='tl'>")
    for g in groups:
        final = g.get("final_jpg")
        img = f"<img class='main' src='{report.b64_jpg(final, 680, 86)}' alt='{g['name']}'>" if final and os.path.exists(final) else "<div></div>"
        t0, t1 = g["t_start"][11:16], g["t_end"][11:16]
        exps = " · ".join(g.get("exposures", []))
        method = ("局部色调映射 (base ×%.2f)" % g["tonemap_compression"]) if g.get("tonemap_compression") else "线性 · sRGB"
        if g.get("pi_out"):
            method += " · BXT + NXT"
        frs = []
        for f in g["files"]:
            r = byname[f]; stem = os.path.splitext(f)[0]
            t = os.path.join(thumbs, stem + ".png")
            cls = "fr" + (" rej" if f in g["reject"] else "") + (" ref" if f == g.get("ref") else "")
            note = (f"<div class='why'>{html.escape(g['reject'][f].split(' (')[0])}</div>" if f in g["reject"]
                    else ("<div class='refl'>参考帧</div>" if f == g.get("ref") else ""))
            frs.append(f"<div class='{cls}'><img src='{report.b64_jpg(t, 78, 80)}' alt='{stem}'>{stem[4:]}<br>{r['exp']:.4g}s{note}</div>")
        out.append(f"<section class='ph {g['stage']}'><div class='t'>{t0}</div>{img}<div>"
                   f"<h2>{LABEL.get(g['stage'], g['stage'])}<small>{t0}–{t1}</small></h2>"
                   f"<dl class='kv'><dt>本影覆盖</dt><dd>{(1-g['sun_frac'])*100:.0f}%</dd>"
                   f"<dt>原片</dt><dd>{len(g['files'])} 张，用 {len(g['keep'])} 张</dd>"
                   f"<dt>曝光</dt><dd>{html.escape(exps)}</dd><dt>处理</dt><dd>{method}</dd></dl>"
                   f"<div class='frames'>{''.join(frs)}</div></div></section>")
    out.append("</div>")
    ph = [os.path.join(cfg["output_dir"], "final", f) for f in ("phone_grid.jpg", "phone_arc.jpg", "phone_hero.jpg")]
    if all(os.path.exists(p) for p in ph):
        out.append("<h3 class='ph-h'>手机版</h3><div class='phones'>"
                   + "".join(f"<img src='{report.b64_jpg(p, 540, 86)}' alt='phone composite'>" for p in ph)
                   + "</div><div class='cap'>1080 × 2340 · 自动挑选差异最大的阶段 · 可直接设为壁纸或发朋友圈</div>")
    out.append("<div class='legend'><span><i style='background:var(--ok)'></i>参与堆栈</span><span><i style='background:var(--bad)'></i>剔除（软 / 拖影 / 溢出）</span><span><i style='background:var(--ref)'></i>对齐参考帧</span></div>")
    out.append("""<div class='method'><h3>方法</h3>
<p><b>辐射度归一。</b>每帧换算成 counts × N² / (t × ISO)，不同曝光、不同光圈才能放在同一尺度上比较和相加。</p>
<p><b>找月盘。</b>固定半径 Hough：残月的亮边也能反推真实圆心；细亮边帧的圆心沿弧线不确定，用固定机位下月亮的匀速漂移模型预测。</p>
<p><b>选片。</b>拉普拉斯能量减去天空噪声得到清晰度，只在相近曝光之间比较；按 14.5″/s × t 估算拖影，超过 6 px 直接剔除。</p>
<p><b>对齐。</b>相位相关给初值、ECC 精调平移加微小旋转；曝光相差大的帧链式对齐到曝光最接近的邻居。</p>
<p><b>堆栈。</b>溢出像素剔除，权重按光子信噪比；sigma-clip 的残差按各帧自身噪声归一，短曝光不会被整帧误杀。</p>
<p><b>后期。</b>PixInsight BlurXTerminator 反卷积 + NoiseXTerminator 去噪；深偏食组用对数底层/细节分离的局部色调映射，只作用于月盘。</p>
</div></div>""")
    path = os.path.join(cfg["output_dir"], "share.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print(path, "%.1f MB" % (os.path.getsize(path) / 1e6))
    return path


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    build(config.load())
