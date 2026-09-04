# Pipeline design and why each choice was made

Stages map 1:1 to modules in `moonstack/`. Everything below the line "why" was learned on real data (Fuji X-T30 + 18-300 @ 300 mm, untracked, 96 RAF, 2026-08-27 eclipse, deepest phase 96 %).

## 1. analyze (`analyze.py`, `raw.py`, `exif.py`, `detect.py`)

- Decode with rawpy: linear (`gamma=(1,1)`, `no_auto_bright`), **daylight** white balance (not as-shot — auto WB drifts between frames), sRGB primaries, 16-bit. Keep a **clip mask** from the raw mosaic (≥ 0.97 × white level, dilated 5 px) — after demosaicing you cannot tell a saturated pixel from a bright one.
- Moon centre: fixed-radius Hough on the lit-region boundary (a crescent's outer edge still votes for the true centre; the terminator has a different curvature so it does not), then a **radial-ray limb fit**: 720 rays, find the steepest outward drop near radius R, least-squares circle (centre + radius). Confidence = fraction of rays with a real limb crossing (full disk ≈ 1, half-lit ≈ 0.6, sliver < 0.4). Canny + Hough was tried and rejected: sky noise votes everywhere and gives a fake 1.0 confidence.
- Cache a 1024² crop per frame (uint16 + clip mask) so later stages never touch the RAW again.
- Sharpness: Laplacian-of-Gaussian energy inside the lit, unclipped disk minus the same on sky, on a *linearly* scaled image. A sqrt/asinh stretch before measuring amplifies near-zero noise and makes noisy frames look sharp.
- Radius prior from EXIF (focal, sensor width, 0.52° moon) then calibrated from full-disk frames.

## 2. group (`phases.py`)

- Features per frame in radiance units: `sun_frac` = disk fraction with radiance > 0.25 × sunlit reference *or clipped*; `long` = exposure regime ≥ 6 stops beyond the sunlit reference (records the umbra).
- Walk frames in time; new group when phase progressed (Δsun_frac > 0.05), exposure regime changed > 4 stops, or window exceeded (full 10 min, partial 4 min, deep 7 min). Bracket ladders detected first.
- Selection: reject if clipped > 25 % of disk, trailed > 6 px, sharpness < 0.7 × best *of similar exposure* in the group, or < 0.45 × the best of similar exposure *in the whole session* (catches a shaken frame that is alone in its group). Always keep ≥ 3 if available. Groups with nothing left are dropped.
- Labels: `full`, `partial_in/out` (relative to the deepest groups), `deep` (sun_frac ≤ 0.15 in a long exposure — named *deep* rather than *total* because the shoot never reached totality), `bracket`.

## 3. stack (`stack.py`, `align.py`)

- Radiance = (counts − bg) × N² / (t × ISO). Weight ∝ photon SNR² ∝ 1/rad_scale × quality.
- **Chained alignment**: each frame registers to the already-aligned frame closest in exposure (then time). Phase correlation is trusted only within 4 px of the prior (limb-centre difference); ECC (Euclidean, coarse-to-fine 4×/2×/1×, mask = pixels with signal) refines and is accepted only within tolerance.
- **Sliver frames** (confidence < 0.5): prior from a constant-velocity drift model fitted over full-disk frames (untracked moon drifts ~5.6 px/s at this scale), ECC allowed ±12 px, then a **radial limb correction**: measure the warped sliver's limb radius vs. the reference's fitted radius and shift along the arc normal. This is the only thing that made bracket ladders register (ECC alone slid 10–25 px along the arc).
- **Photometric scale** per frame = weighted median of ratios against *all* previously scaled frames it overlaps, using only pixels below 0.7 × full scale (the sensor's saturation knee) and above noise in both. Chaining single ratios wobbled ±10 % and left visible seams.
- Sigma clip: residual × √weight vs. pooled std, 2.5 σ, 2 iterations. Pixels clipped in every frame take the shortest exposure's value and are flagged in a mask PNG.
- < 3 frames: 5×5 median cosmetic pass (X-Trans smears a hot photosite over 2–3 px).

## 4. pixinsight (`pi_bridge.py`)

- Normalise the linear stack to [0, 1], write float32 TIFF, generate one PJSR script: BlurXTerminator (nonstellar sharpen 0.45, PSF 2.5 px, auto PSF off — no stars to measure) then NoiseXTerminator (0.6, 0.85 for umbra groups / few frames, detail 0.2). Progress via files the script writes.

## 5. finish (`finish.py`, `rescue.py`, `phone.py`)

- White balance: sunlit surface = neutral grey, measured on the most sunlit group, same multipliers everywhere, so umbra colour is relative to it.
- Lateral CA: affine ECC of R and B onto G inside the disk; skipped when the fit is implausible (scale off by > 3 % or shift > 6 px) — B usually fails on the red umbra.
- Sunlit groups: linear scale so the 99.8th percentile = 0.92, sRGB curve.
- Umbra groups: log-luminance split into bilateral base + detail; base compressed to 4.5 stops, detail × 1.2, brightest base at 0.85. Only inside the disk; the sky keeps a plain linear stretch (stars survive, noise is not lifted). Global asinh was tried: umbra went grey-pink.
- Saturation handling: soft per-channel weight `(max_c(img/plateau_c) − 0.6)/0.35`, blurred σ 5, fades to neutral at the brightest channel's level. Sky outside R+3 px is desaturated and lightly smoothed (coloured speckles otherwise).
- Sliver rescue (`rescue.py`): register the full-moon stack (rotation search −60…60° + ECC, high-passed, correlation ≥ 0.25) → inside the blown region, radiance = boundary level continued inward × (albedo / local mean) × measured penumbral profile from the bracket group stretched to this sliver's width and scaled by each ray's depth, capped at full sunlight; colour eases from boundary colour to albedo colour over 40 px. Failed alternatives: quadratic-plane extrapolation (never exceeded the clip level), per-ray quadratic (spokes, blue bloom), flat plateau × gentle rise (a pale blob the user spotted at once).
- Composites: strip (all phases), phone grid 2 × 3 (default), arc, hero. Phone selection: fullest + best umbra (bracket preferred) + last, then maximise spread in sun_frac, preferring measured-only groups.

## 6. photoshop (`ps_bridge.py`)

- One JSX: per phase open TIFF → `Stack` layer, duplicate + unsharp mask, neutral Curves and Vibrance adjustment layers (Action Manager), save PSD, flatten → JPG; composite doc with one layer per phase. Opens files through DOM first, Action Manager `Opn ` as fallback, and verifies the opened document's name.

## 7. report (`report.py`, `share.py`)

- Base64-embedded single file; per group: final image, every frame's thumbnail with score and keep/reject reason, alignment/photometric stats. `share.py` is the same content styled as a shareable page.
