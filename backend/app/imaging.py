import base64
import io
from collections import namedtuple

import numpy as np
from PIL import Image

Strip = namedtuple("Strip", "index y0 y1 ys ye pad_top pad_bottom")


def complexity(width, height, radius):
    return width * height * (2 * radius + 1) ** 2


def make_kernel(name, radius):
    ax = np.arange(-radius, radius + 1)
    xx, yy = np.meshgrid(ax, ax)
    sigma = max(radius / 2.0, 0.8)
    g = np.exp(-(xx ** 2 + yy ** 2) / (2 * sigma ** 2))
    g /= g.sum()
    if name == "blur":
        kernel = g
    elif name == "sharpen":
        kernel = -g
        kernel[radius, radius] += 2.0
    else:
        raise ValueError("невідомий фільтр: " + name)
    return kernel.astype(np.float32)


def split_rows(height, n_strips, radius):
    n = max(1, min(n_strips, height))
    strips = []
    for i in range(n):
        y0 = i * height // n
        y1 = (i + 1) * height // n
        ys = max(0, y0 - radius)
        ye = min(height, y1 + radius)
        strips.append(Strip(len(strips), y0, y1, ys, ye, radius - (y0 - ys), radius - (ye - y1)))
    return strips


def convolve_strip(strip_u8, kernel, pad_top, pad_bottom, on_row=None, should_stop=None):
    r = kernel.shape[0] // 2
    x = np.pad(
        strip_u8.astype(np.float32),
        ((pad_top, pad_bottom), (r, r), (0, 0)),
        mode="edge",
    )
    h = x.shape[0] - 2 * r
    w = x.shape[1] - 2 * r
    out = np.zeros((h, w, 3), dtype=np.float32)

    for dy in range(2 * r + 1):
        if should_stop is not None and should_stop():
            return None
        for dx in range(2 * r + 1):
            out += kernel[dy, dx] * x[dy:dy + h, dx:dx + w, :]
        if on_row is not None:
            on_row()

    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def to_png_b64(arr):
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG", compress_level=1)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def from_b64(text):
    data = base64.b64decode(text)
    return np.array(Image.open(io.BytesIO(data)).convert("RGB"))


def to_png_bytes(arr):
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()
