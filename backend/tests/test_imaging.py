import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.imaging import (convolve_strip, from_b64, make_kernel, split_rows,
                         to_png_b64)


def run_case(width, height, radius, n_strips, filter_name):
    rng = np.random.default_rng(42)
    img = rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)
    kernel = make_kernel(filter_name, radius)

    whole = convolve_strip(img, kernel, radius, radius)

    parts = []
    for s in split_rows(height, n_strips, radius):
        piece = from_b64(to_png_b64(img[s.ys:s.ye]))
        parts.append(convolve_strip(piece, kernel, s.pad_top, s.pad_bottom))
    glued = np.vstack(parts)

    assert glued.shape == whole.shape, (glued.shape, whole.shape)
    assert np.array_equal(glued, whole), "смуги дали інший результат!"
    print(f"OK  {width}x{height} r={radius} смуг={n_strips} фільтр={filter_name}")


if __name__ == "__main__":
    run_case(50, 40, 3, 7, "blur")
    run_case(64, 64, 5, 16, "sharpen")
    run_case(30, 10, 4, 16, "blur")
    run_case(80, 33, 1, 5, "sharpen")
    run_case(20, 100, 15, 16, "blur")
    print("Усі тести пройшли")
