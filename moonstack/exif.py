"""Read exposure metadata: JPG sidecar (fast, PIL) -> exifread on the RAW (TIFF-based formats:
NEF, CR2, ARW, RAF, DNG, ORF, RW2, PEF...) -> exiftool if on PATH (CR3 and other ISO-BMFF RAWs)."""
import os, datetime, shutil, subprocess, json
from PIL import Image
from PIL.ExifTags import TAGS


def _ratio(v):
    try:
        return float(v)
    except Exception:
        try:
            return v.num / v.den
        except Exception:
            return None


def read(raw_path):
    meta = {"file": os.path.basename(raw_path)}
    base = os.path.splitext(raw_path)[0]
    tags = None
    for ext in (".JPG", ".jpg", ".jpeg"):
        if os.path.exists(base + ext):
            ex = Image.open(base + ext)._getexif() or {}
            tags = {TAGS.get(k, k): v for k, v in ex.items()}
            break
    if tags is None:
        import exifread
        with open(raw_path, "rb") as f:
            t = exifread.process_file(f, details=False)
        tags = {
            "DateTimeOriginal": str(t.get("EXIF DateTimeOriginal", "")),
            "ExposureTime": t["EXIF ExposureTime"].values[0] if "EXIF ExposureTime" in t else None,
            "ISOSpeedRatings": t["EXIF ISOSpeedRatings"].values[0] if "EXIF ISOSpeedRatings" in t else None,
            "FNumber": t["EXIF FNumber"].values[0] if "EXIF FNumber" in t else None,
            "FocalLength": t["EXIF FocalLength"].values[0] if "EXIF FocalLength" in t else None,
            "FocalLengthIn35mmFilm": t["EXIF FocalLengthIn35mmFilm"].values[0] if "EXIF FocalLengthIn35mmFilm" in t else None,
            "Model": str(t.get("Image Model", "")),
        }
    if not tags.get("ExposureTime") and shutil.which("exiftool"):
        out = subprocess.run(["exiftool", "-j", "-n", "-DateTimeOriginal", "-ExposureTime", "-ISO", "-FNumber",
                              "-FocalLength", "-FocalLengthIn35mmFormat", "-Model", raw_path], capture_output=True, text=True)
        try:
            j = json.loads(out.stdout)[0]
            tags = {"DateTimeOriginal": j.get("DateTimeOriginal"), "ExposureTime": j.get("ExposureTime"),
                    "ISOSpeedRatings": j.get("ISO"), "FNumber": j.get("FNumber"),
                    "FocalLength": j.get("FocalLength"), "FocalLengthIn35mmFilm": j.get("FocalLengthIn35mmFormat"),
                    "Model": j.get("Model", "")}
        except Exception:
            pass
    if not tags.get("ExposureTime"):
        raise SystemExit(f"{meta['file']}: no exposure metadata found (no JPG sidecar, exifread failed, "
                         f"exiftool not on PATH). Install exiftool or keep the camera JPGs next to the RAWs.")
    dt = tags.get("DateTimeOriginal")
    meta["datetime"] = dt
    meta["t"] = datetime.datetime.strptime(str(dt)[:19], "%Y:%m:%d %H:%M:%S").timestamp() if dt else 0.0
    meta["exp"] = _ratio(tags.get("ExposureTime")) or 1.0
    iso = tags.get("ISOSpeedRatings")
    if isinstance(iso, (tuple, list)):
        iso = iso[0]
    meta["iso"] = float(iso or 100)
    meta["fnum"] = _ratio(tags.get("FNumber")) or 0.0
    meta["focal"] = _ratio(tags.get("FocalLength")) or 0.0
    meta["focal35"] = _ratio(tags.get("FocalLengthIn35mmFilm")) or 0.0
    meta["model"] = str(tags.get("Model", "")).strip()
    return meta
