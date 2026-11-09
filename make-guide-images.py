#!/usr/bin/env python3
"""Make the build guide's web images from the original photographs.

Originals in "build images" are never changed. For each photo this:

  1. Fits a smooth surface to the light background, in each colour channel.
  2. Divides by it, which removes colour casts and uneven lighting and brings
     every background to the same light neutral. The correction is smooth and
     applied to the whole photo: nothing is cut out or painted in.
  3. Trims spare background around the subject, leaving a margin.
  4. Resizes and strips metadata, saving a full-size copy and a smaller copy
     for the page in guide-images/thumbs.

Photos with little visible background are close-ups, and only get their colour
balance from what background there is, with no change to the lighting shape.

Usage: python3 make-guide-images.py   (needs pillow and numpy)
"""

import os

import numpy as np
from PIL import Image, ImageOps

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'build images')
OUT = os.path.join(HERE, 'guide-images')

COUNT = 32
LONG_EDGE = 1600
THUMB_EDGE = 420                           # the copies shown in BUILD.md, linking to the full size
JPEG_Q = 85

TARGET = np.array([226, 225, 222]) / 255   # light neutral, a touch warm
WORK_EDGE = 400                            # size used to estimate the background
MIN_BG_FOR_SHAPE = 0.25                    # below this, colour balance only
MAX_GAIN = 1.7                             # never lift any point further than this
KNEE = 0.80                                # linear level where highlights start to roll off
MARGIN = 0.07                              # of the subject's long edge
ROTATE = {21: Image.ROTATE_90}             # photo number: extra turn, ROTATE_90 is counter-clockwise


def to_linear(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def to_srgb(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def terms(x, y):
    return np.stack([np.ones_like(x), x, y, x * x, x * y, y * y], axis=-1)


def background_mask(small):
    """Light, low-colour pixels. Printed grey parts are removed by the fit's outlier rejection."""
    top = small.max(axis=2)
    chroma = top - small.min(axis=2)
    # Relative to the photo's own brightness, or sheen on the black plate counts as background.
    return (top > max(0.40, 0.75 * np.percentile(top, 90))) & (chroma < 0.16)


def fit_surface(small, mask):
    """Quadratic fit per channel over the background, rejecting outliers twice."""
    h, w = mask.shape
    yy, xx = np.mgrid[0:h, 0:w]
    x, y = xx / w - 0.5, yy / h - 0.5
    keep = mask.copy()
    coeffs = None
    for _ in range(3):
        a = terms(x[keep], y[keep])
        coeffs = np.linalg.lstsq(a, small[keep], rcond=None)[0]
        residual = np.abs(terms(x, y) @ coeffs - small).max(axis=2)
        keep = mask & (residual < 0.05)
        if keep.sum() < 500:
            break
    return coeffs


def corrected(im):
    arr = np.asarray(im, dtype=np.float32) / 255
    h, w = arr.shape[:2]
    scale = WORK_EDGE / max(h, w)
    small_im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.BOX)
    small = np.asarray(small_im, dtype=np.float32) / 255

    mask = background_mask(small)
    fraction = mask.mean()
    target = to_linear(TARGET)
    linear = to_linear(arr)

    if fraction >= MIN_BG_FOR_SHAPE:
        coeffs = fit_surface(to_linear(small), mask)
        step = 8
        yy, xx = np.mgrid[0:h:step, 0:w:step]
        surface = terms(xx / w - 0.5, yy / h - 0.5) @ coeffs
        gain = np.clip(target / np.maximum(surface, 1e-3), 1 / MAX_GAIN, MAX_GAIN)
        gain_im = [Image.fromarray(gain[..., i].astype(np.float32)).resize((w, h), Image.BILINEAR)
                   for i in range(3)]
        gain = np.stack([np.asarray(g) for g in gain_im], axis=-1)
    else:
        # Balance colour from the background that exists, keeping brightness.
        pixels = to_linear(small)[mask] if mask.sum() > 200 else to_linear(small).reshape(-1, 3)
        level = np.median(pixels, axis=0)
        gain = np.clip(level.mean() / np.maximum(level, 1e-3), 0.8, 1.25)

    lifted = linear * gain
    # Roll off highlights, so grey printed parts and metal keep their detail.
    rolled = KNEE + (1 - KNEE) * (1 - np.exp(-(lifted - KNEE) / (1 - KNEE)))
    out = to_srgb(np.where(lifted > KNEE, rolled, lifted))
    return Image.fromarray((out * 255 + 0.5).astype(np.uint8)), fraction


def trim(im):
    """Crop spare background, leaving a margin. Close-ups that fill the frame stay as they are."""
    arr = np.asarray(im.resize((im.width // 8, im.height // 8), Image.BOX), dtype=np.float32) / 255
    subject = np.abs(arr - TARGET).max(axis=2) > 0.16
    rows = np.where(subject.mean(axis=1) > 0.004)[0]
    cols = np.where(subject.mean(axis=0) > 0.004)[0]
    if not len(rows) or not len(cols):
        return im
    top, bottom, left, right = rows[0] * 8, (rows[-1] + 1) * 8, cols[0] * 8, (cols[-1] + 1) * 8
    pad = int(MARGIN * max(bottom - top, right - left))
    box = (max(0, left - pad), max(0, top - pad), min(im.width, right + pad), min(im.height, bottom + pad))
    return im.crop(box)


def main():
    os.makedirs(os.path.join(OUT, 'thumbs'), exist_ok=True)
    for k in range(1, COUNT + 1):
        im = ImageOps.exif_transpose(Image.open(os.path.join(SRC, f'{k}.jpeg'))).convert('RGB')
        if k in ROTATE:
            im = im.transpose(ROTATE[k])
        fixed, fraction = corrected(im)
        fixed = trim(fixed)
        fixed.thumbnail((LONG_EDGE, LONG_EDGE), Image.LANCZOS)
        fixed.save(os.path.join(OUT, f'{k}.jpg'), quality=JPEG_Q, optimize=True, progressive=True)
        # Viewers show these at their own size, so the size on the page is set here.
        thumb = fixed.copy()
        thumb.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.LANCZOS)
        thumb.save(os.path.join(OUT, 'thumbs', f'{k}.jpg'), quality=JPEG_Q, optimize=True, progressive=True)
        print(f'{k:>2}: background {fraction:4.0%}  {fixed.width}x{fixed.height}')


if __name__ == '__main__':
    main()
