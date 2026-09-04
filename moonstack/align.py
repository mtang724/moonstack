"""Sub-pixel registration of one crop onto a reference: phase correlation for the coarse
translation, then ECC (Euclidean: translation + small rotation) for the refinement."""
import numpy as np
import cv2
from skimage.registration import phase_cross_correlation


def prep(rad, valid):
    """asinh-stretched luminance in [0,1], invalid (clipped) pixels filled with the local max
    so that a blown limb in a long exposure still lines up with the short exposure's limb."""
    x = rad.copy()
    hi = np.percentile(x[valid], 99.5) if valid.sum() > 100 else np.percentile(x, 99.5)
    x[~valid] = hi
    k = 20.0
    y = np.arcsinh(np.clip(x / (hi + 1e-9), 0, 1) * k) / np.arcsinh(k)
    return y.astype(np.float32)


def _ecc(ref, mov, M, mask, mode, iters=80, eps=1e-5):
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, iters, eps)
    _, M2 = cv2.findTransformECC(ref, mov, M.copy(), mode, crit, mask, 5)
    return M2


def estimate(ref_img, mov_img, mov_valid, allow_rotation=True, prior=None, tol=4.0):
    """Returns 2x3 affine warp mapping mov -> ref coordinates (for cv2.warpAffine).
    prior: (dx, dy) from the limb-circle centers / drift model; phase correlation is only
    trusted when it agrees with the prior (thin crescents give it spurious peaks). ECC then
    refines coarse-to-fine (4x, 2x, 1x) on the pixels that carry signal, so it converges
    even when the prior is several pixels off, and is accepted only within tol of the start."""
    shift, _, _ = phase_cross_correlation(ref_img, mov_img, upsample_factor=10)
    dy, dx = float(shift[0]), float(shift[1])
    if prior is not None and (abs(dx - prior[0]) > tol or abs(dy - prior[1]) > tol):
        dx, dy = float(prior[0]), float(prior[1])
    M0 = np.array([[1, 0, dx], [0, 1, dy]], np.float32)
    # signal mask: valid pixels with real brightness (stretched > 0.15 ~ 3% of the frame's peak)
    sig = (mov_img > 0.15).astype(np.uint8)
    sig = cv2.dilate(sig, np.ones((21, 21), np.uint8)) & mov_valid.astype(np.uint8)
    if sig.sum() < 2000:
        sig = mov_valid.astype(np.uint8)
    mode = cv2.MOTION_EUCLIDEAN if allow_rotation else cv2.MOTION_TRANSLATION
    M = M0.copy()
    try:
        for s in (4, 2, 1):
            if s > 1:
                r = cv2.resize(ref_img, None, fx=1 / s, fy=1 / s, interpolation=cv2.INTER_AREA)
                m = cv2.resize(mov_img, None, fx=1 / s, fy=1 / s, interpolation=cv2.INTER_AREA)
                k = cv2.resize(sig, None, fx=1 / s, fy=1 / s, interpolation=cv2.INTER_NEAREST)
                Ms = M.copy(); Ms[:, 2] /= s
                Ms = _ecc(r, m, Ms, k, cv2.MOTION_TRANSLATION if s == 4 else mode, iters=60)
                Ms[:, 2] *= s; M = Ms
            else:
                M = _ecc(ref_img, mov_img, M, sig, mode, iters=100, eps=1e-6)
        if abs(M[0, 2] - dx) > tol or abs(M[1, 2] - dy) > tol:
            M = M0
    except cv2.error:
        M = M0
    return M


def warp(img, M, S):
    flags = cv2.INTER_LANCZOS4 if img.ndim == 3 else cv2.INTER_LINEAR
    return cv2.warpAffine(img, M, (S, S), flags=flags, borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def rotation_deg(M):
    return float(np.degrees(np.arctan2(M[1, 0], M[0, 0])))
