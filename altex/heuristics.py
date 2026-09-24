"""Conservative provisional labels; these are not reviewed contour truth."""

import numpy as np
from scipy.ndimage import binary_dilation

PALETTE = [245, 209, 153, 230, 55, 35, 45, 130, 220, 35, 35, 45] + [0] * (256 * 3 - 12)
PALETTE[255 * 3 : 256 * 3] = [255, 0, 255]


def save_labels(path, labels):
    from PIL import Image

    im = Image.fromarray(labels.astype("uint8")).convert("P")
    im.putpalette(PALETTE)
    im.save(path)


def pseudo_labels(rgb, valid):
    colors = rgb.astype(float)
    r, g, b = colors.transpose(2, 0, 1)
    labels = np.full(valid.shape, 255, dtype="uint8")
    if not valid.any():
        return labels
    cut = min(170, np.percentile(r[valid], 25))
    ink = (r < cut) & valid
    brown = ink & (r > g * 1.18) & (g > b * 1.15)
    # Background and strongly colored distractors only; ambiguous water stays ignored.
    paper = (r > 210) & (g > 155) & ((r - b) > 105)
    other = ink & (((r - g) < 15) | ((r > g * 1.8) & (r > 140)))
    labels[paper & ~binary_dilation(ink, iterations=2)] = 0
    labels[brown] = 1
    labels[other] = 3
    labels[~valid] = 255
    return labels
