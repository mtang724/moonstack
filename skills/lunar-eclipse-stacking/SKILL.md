---
name: lunar-eclipse-stacking
description: One-click post-processing of a lunar eclipse (or any moon) RAW sequence from any camera (Canon/Nikon/Sony/Fuji/Olympus/Panasonic/Pentax/DNG), tripod or tracked mount - reject blurred/clipped frames, auto-split the eclipse into phases, sub-pixel align, HDR-aware stack in linear radiance, denoise/deconvolve headlessly in PixInsight, tone-map the umbra, rebuild blown sunlit slivers, and deliver per-phase TIFF/JPG, layered Photoshop PSDs, phone-format composites and an HTML report. Use when someone has a folder of moon/eclipse RAWs (RAF/NEF/CR3/ARW/DNG) and asks to stack, align, denoise, HDR-merge, or "do the post-processing" for them, or asks whether RAW is worth it.
---

# Lunar eclipse stacking

You are driving `moonstack`, a Python pipeline that turns a night of eclipse RAWs into finished images. It works with any camera whose RAW LibRaw decodes (Canon, Nikon, Sony, Fuji, Olympus/OM, Panasonic, Pentax, DNG…), any lens or telescope, tracked or untracked. It was developed on 96 Fuji X-T30 frames (300 mm, untracked tripod, 96 % umbral coverage) and every rule below was learned from something that went wrong on that data. Read `references/pipeline-design.md` before changing any threshold.

## What the user gets

| Output | Where |
|---|---|
| One developed image per eclipse phase (16-bit sRGB TIFF + JPG) | `output/final/NN_stage_HHMM.*` |
| Whole-eclipse strip, phone-format composites (grid / arc / hero, 1080×2340) | `output/final/00_composite.jpg`, `phone_*.jpg` |
| Layered PSD per phase + layered sequence PSD (needs Photoshop) | `output/photoshop/` |
| Self-contained HTML report: every frame, its score, why it was kept or rejected | `output/report.html`; `python -m moonstack.share` makes a shareable page |
| Machine-readable state | `output/frames.json`, `output/groups.json` |

## Running it

```
python run.py                       # everything, ~7 min for 100 frames
python run.py --from stack          # resume from a stage
python run.py --stage finish        # rerun one stage while tuning the look
python run.py --no-pi --no-ps       # without PixInsight / Photoshop
```
Stages: `preflight → analyze → group → stack → pixinsight → finish → photoshop → report`. Each writes JSON to `output/`, so any stage reruns alone. Put overrides in `config.json` (see `config.example.json`); defaults live in `moonstack/config.py`.

## Step 0 — preflight: analyse the camera and adapt, before anything else

Run `python run.py --stage preflight` first (it also runs automatically at the start of a full run) and read its report back to the user before stacking. It decides, from the data on disk:

| it reports | decided from | you check |
|---|---|---|
| camera, RAW format, CFA (Bayer / X-Trans), bit depth, JPG sidecars | first RAW via LibRaw | nothing — informational |
| sensor width / pixel pitch | EXIF 35 mm-equivalent focal → crop factor; else a model table; else the default **with a WARNING** | on a warning, ask the user for the sensor size or pixel pitch and put `sensor_width_mm` / `pixel_pitch_um` in `config.json` |
| focal length, arcsec/px, moon diameter in px, crop size | EXIF (or `focal_mm`) | a telescope or manual lens gives no focal length → the stage stops and asks for `focal_mm`; a moon > ~700 px automatically enlarges `crop_size` |
| exposure regimes (2-stop clusters), bracket ladders, time span | EXIF of every frame | one regime only → no umbra or no sunlit data; no ladder → a blown sliver cannot be rebuilt from measurement (say so up front) |
| tracked vs untracked | moon drift between two frames a few seconds apart vs 14.5″/s sidereal | < 25 % of sidereal → treated as tracked (no drift model, no trailing rejection); the user can force `tracked` either way |
| PixInsight / Photoshop / exiftool / CPU count | filesystem | absent tools are switched off for this run; CR3 without sidecars needs exiftool |

It writes `output/preflight.json` and — only if none exists — a `config.json` with the adapted values, so the user's edits persist. Every `WARNING:` line is something to relay; the rest is context for interpreting later stages (e.g. a 200 px moon will never look like a 700 px one, and you should say that before the user compares with the README images).

Also confirm which Python has `rawpy`, `opencv-python`, `scikit-image`, `tifffile`, `exifread`, `Pillow` (`pip install -r requirements.txt`); rawpy decodes X-Trans and Bayer alike, ~2 s per 26 MP frame.

## Answering "should I use RAW or JPG?"

RAW, without hesitation: the umbra is 8–9 stops below the sunlit surface; in-camera JPG has already applied noise reduction, sharpening and an 8-bit curve, so stacking gains little. RAW gives linear 14-bit data, and only linear data can be normalised across exposures (the whole HDR-aware stack depends on it). JPG sidecars are still useful: the pipeline reads EXIF from them because it is faster than parsing the RAW.

## Reading the log — what good looks like

- `[analyze] moon radius calibrated: prior 362px -> fitted 354px` — within 15 % of the EXIF prior, else the focal length or sensor width is wrong.
- `[group]` lines: `sun=` is the sunlit fraction of the disk. Full moon ≈ 0.95, totality ≈ 0.00, a 96 % partial ≈ 0.04–0.08. Groups are split when `sun` moves > 0.05, when exposure regime changes > 4 stops, or when the window (4 min partial / 7 min deep) is exceeded. A bracket ladder (≥ 5 frames, ≤ 20 s apart, each step ≥ ½ stop) is its own `bracket` group and is the only true HDR data.
- `[stack] … max shift 5px rot 0.2deg … sigma-rejected 0.3%` — rejection above ~3 % means a misalignment or photometric mismatch. Rotation > 0.5° within a group means the window is too long for an untracked mount (field rotation).
- `photometric x0.9…1.1` — frames of nominally identical exposure differing by more than ±15 % means the sensor's saturation knee crept into the fit, or a frame is misregistered.
- `[rescue] … measured profile (limb/boundary x6–12)` — the sliver rebuild found a measured penumbral profile; `fallback rise` means no unclipped sliver existed anywhere in the session (tell the user to shoot a short-exposure bracket next time).

## Rules that must not be undone

1. **Radiance normalisation includes the aperture**: counts × N² / (t × ISO). f/6.3 vs f/13 is a 4.3× difference; omit it and phase grouping is wrong for every frame.
2. **Sharpness is compared only among frames within ±0.6 stop**. A 1/8 s frame is always crisper than a 0.4 s one, but the long one carries the umbra signal.
3. **Trailing is computed, not guessed**: 14.5″/s × t ÷ pixel scale. Above 6 px the frame is rejected regardless of its score.
4. **Sigma clipping normalises residuals by each frame's own noise** (√weight). Otherwise short, noisy exposures are rejected wholesale and the HDR merge collapses to the long frames.
5. **Thin slivers are positioned radially by their limb, never by ECC alone.** ECC slides along a smooth arc; use the drift model (constant-velocity fit over full-disk frames) for the along-arc position and the limb radius against the reference for the radial one.
6. **Saturated regions carry no colour.** Fade any pixel whose channel approaches its plateau to neutral with a soft weight; a binary "all clipped" mask leaves a blocky edge with a cyan/purple rim.
7. **The turquoise band under the sliver is real** (ozone absorption at the umbra edge, B/G ≈ 1.3–1.5 in the measured bracket). Do not "fix" it.

## When the user says the deep-phase sliver is blown out

First check whether any exposure in the session has an unclipped sliver: `clip_frac` in `frames.json` < 0.002 for a long-exposure-regime frame. For a 96 % eclipse that needs ≤ 1/30 s at ISO 1600 f/6.3; 1/8 s already clips 4–5 % of the disk. If only a bracket group has it, `rescue.py` rebuilds the other groups' slivers from the registered full-moon texture × the bracket's *measured* radial profile (stretched to each sliver's width). Say plainly that texture and profile shape are measured but their placement is inferred; keep those groups out of the "hero" slots of composites (the phone selector already prefers measured-only groups unless the user asks for two deep phases).

## Pitfalls that cost hours (see `references/headless-tools.md`)

- PixInsight: `PixInsight.exe --automation-mode -n --no-splash -r=<script.js> --force-exit`; poll for a file the script writes. A second `-r` while an instance runs is forwarded to it.
- Photoshop: `Photoshop.exe script.jsx`. An unlicensed/unactivated Photoshop opens the first file and then throws "open options are incorrect" on every later `app.open` — that is licensing, not your script. `DocumentFill` has no BLACK.
- `stack.run()` on a subset of groups rewrites `groups.json` with that subset; rerun `--stage group` afterwards.
- X-Trans demosaicing spits near-zero pixels next to saturated ones; multi-frame groups hide them, single-frame groups need the 5×5 cosmetic pass.
