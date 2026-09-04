"""RAW decode to linear sRGB (daylight WB) plus a clipped-pixel mask."""
import numpy as np
import rawpy


def decode(path, clip_level=0.97):
    with rawpy.imread(path) as r:
        vis = r.raw_image_visible
        clip = vis >= clip_level * r.white_level
        wb = list(r.daylight_whitebalance)
        if not wb or wb[0] == 0:
            wb = list(r.camera_whitebalance)
        rgb = r.postprocess(
            output_bps=16, gamma=(1, 1), no_auto_bright=True,
            use_camera_wb=False, user_wb=wb,
            output_color=rawpy.ColorSpace.sRGB, user_flip=0,
            demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,
        )
    if clip.shape != rgb.shape[:2]:
        import cv2
        clip = cv2.resize(clip.astype(np.uint8), (rgb.shape[1], rgb.shape[0]),
                          interpolation=cv2.INTER_NEAREST).astype(bool)
    return rgb, clip


def luminance(rgb):
    rgb = rgb.astype(np.float32)
    return 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
