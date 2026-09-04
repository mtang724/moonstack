# Driving PixInsight and Photoshop without a GUI

## PixInsight (tested 1.9.3, Windows)

```
PixInsight.exe --automation-mode -n --no-splash -r=<absolute path, forward slashes>.js --force-exit
```
- Start it with `subprocess.Popen` and poll for a file the script writes; there is no readable console. Startup takes 15–60 s (licence check, GPU init — GLES warnings on stderr are harmless).
- `-n` skips startup scripts. If an instance is already running, a new `-r` is forwarded to it and your process returns immediately — kill stale instances before batch runs.
- PJSR essentials used here:
  ```js
  var w = ImageWindow.open(path)[0]; var v = w.mainView;
  var P = new BlurXTerminator; P.correct_only=false; P.sharpen_stars=0; P.adjust_halos=0;
  P.auto_nonstellar_psf=false; P.nonstellar_psf_diameter=2.5; P.sharpen_nonstellar=0.45; P.executeOn(v);
  var N = new NoiseXTerminator; N.denoise=0.85; N.detail=0.2; N.iterations=2; N.executeOn(v);
  w.saveAs(dst, false, false, false, false); w.forceClose();
  File.writeTextFile(donePath, "done");
  ```
  Dump any process's parameter names with `for (var k in new NoiseXTerminator) …` written to a file — do not guess them.
- Float32 TIFF in → float32 TIFF out; values must be in [0, 1].

## Photoshop (tested 2025 / 26.10, Windows)

```
Photoshop.exe <script.jsx>
```
- Runs the ExtendScript; the instance stays open afterwards and later invocations run in it.
- `app.displayDialogs = DialogModes.NO` first; write progress to a file with `File.open("w")`.
- **Licensing symptom**: with an inactive subscription the first `app.open` works and every later one throws *"Cannot open the file because the open options are incorrect"* for any file, including one that just opened. Nothing in the script fixes it; activation does. Keep an Action Manager fallback anyway:
  ```js
  var d = new ActionDescriptor(); d.putPath(charIDToTypeID("null"), new File(p));
  executeAction(charIDToTypeID("Opn "), d, DialogModes.NO); var doc = app.activeDocument; // verify doc.name
  ```
- `DocumentFill` has WHITE / BACKGROUNDCOLOR / TRANSPARENT only — set `app.backgroundColor` to black and use BACKGROUNDCOLOR.
- Adjustment layers via Action Manager: `Mk  ` with `adjustmentLayer` / `Type` = `curves` or `vibrance` and `presetKindDefault`.
- 16-bit PSDs cannot be read back by Pillow; use psd-tools or Photoshop.

## rawpy / LibRaw notes

- `raw_image_visible` has the same shape as `postprocess()` output for RAF; build the clip mask there.
- `daylight_whitebalance` is the stable choice across frames; `camera_whitebalance` follows auto-WB drift.
- X-Trans (6×6 pattern) decodes at ~2 s per 26 MP frame with AHD; demosaic artefacts: near-zero pixels adjacent to saturated ones, hot pixels smeared to 2–3 px blobs.
