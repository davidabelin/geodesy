"""Procedural contour drawings with visible-ink labels; no measured terrain."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from skimage.measure import find_contours

from .heuristics import save_labels
from .raster_io import write_json


def make_tile(size=512, seed=0):
    rng = np.random.default_rng(seed if seed >= 0 else 2**63 + abs(seed))
    y, x = np.mgrid[-1 : 1 : complex(size), -1 : 1 : complex(size)]
    kind = seed % 4
    if kind == 0:
        field = (x - rng.uniform(-0.5, 0.5)) ** 2 + (y - rng.uniform(-0.5, 0.5)) ** 2
    elif kind == 1:
        field = y + 0.4 * np.sin(3 * x) + 0.12 * np.sin(6 * x + y)
    elif kind == 2:
        field = x * x - y * y + 0.15 * x
    else:
        field = x + 0.3 * y + 0.1 * np.sin(4 * y)
    field += 0.025 * np.sin(7 * x + 4 * y + rng.uniform(0, 6))
    noise = rng.normal(0, 2.0, (size, size, 1))
    paper = np.clip(np.array([249, 192, 122]) + noise, 0, 255).astype("uint8")
    rgb = Image.fromarray(paper)
    lab = Image.new("L", (size, size), 0)
    draw, label = ImageDraw.Draw(rgb), ImageDraw.Draw(lab)
    # Water-like tint and hatch are appearance cues, never a height seed.
    if rng.random() < 0.7:
        edge = int(rng.uniform(0.1, 0.3) * size)
        draw.rectangle((0, 0, edge, size), fill=(226, 184, 139))
        label.rectangle((0, 0, edge, size), fill=2)
        for yy in range(0, size, 13):
            draw.line((0, yy, edge, yy - 8), fill=(191, 161, 137), width=1)
    step = rng.uniform(0.055, 0.15) * (512 / size)
    levels = np.arange(field.min() + step, field.max(), step)
    for level in levels:
        for curve in find_contours(field, level):
            points = [(float(c), float(r)) for r, c in curve]
            if len(points) < 2:
                continue
            color = ((73, 44, 34), (124, 75, 47), (162, 110, 70))[rng.integers(3)]
            width = int(rng.integers(2, 5))
            draw.line(points, fill=color, width=width)
            label.line(points, fill=1, width=width)
    # Erasures remove labels too: the model is not taught to invent hidden ink.
    for _ in range(max(1, size // 48)):
        cx, cy = rng.integers(0, size, 2)
        box = (
            int(cx),
            int(cy),
            int(cx + rng.integers(2, 7)),
            int(cy + rng.integers(2, 7)),
        )
        draw.rectangle(box, fill=(249, 192, 122))
        label.rectangle(box, fill=0)
    for i in range(max(4, size // 40)):
        cx, cy = map(int, rng.integers(0, size, 2))
        if i % 4 == 0:
            points = [(cx, cy), (cx + 10, cy + 18), (cx - 6, cy + 45)]
            draw.line(points, fill=(47, 51, 57), width=3)
            label.line(points, fill=3, width=3)
        elif i % 4 == 1:
            text = str(rng.integers(1, 999)) + " Road"
            draw.text((cx, cy), text, fill=(65, 48, 39))
            label.text((cx, cy), text, fill=3)
        elif i % 4 == 2:
            for dx in range(0, 60, 12):
                box = (cx + dx, cy, cx + dx + 5, cy + 2)
                draw.rectangle(box, fill=(181, 47, 40))
                label.rectangle(box, fill=3)
        else:
            for dx in (0, 6, 12):
                points = [(cx + dx, cy + 8), (cx + dx + 3, cy), (cx + dx + 6, cy + 8)]
                draw.line(points, fill=(102, 85, 46), width=2)
                label.line(points, fill=3, width=2)
    rgb = rgb.filter(ImageFilter.GaussianBlur(float(rng.uniform(0.15, 0.6))))
    image = np.clip(
        np.asarray(rgb).astype(float) * rng.uniform(0.9, 1.04), 0, 255
    ).astype("uint8")
    return image, np.array(lab)


def export_tiles(out, count, size, seed):
    out = Path(out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "labels").mkdir(exist_ok=True)
    for i in range(count):
        rgb, labels = make_tile(size, seed + i)
        Image.fromarray(rgb).save(out / "images" / f"{i:04d}.png")
        save_labels(out / "labels" / f"{i:04d}.png", labels)
    write_json(
        out / "manifest.json",
        {
            "kind": "procedural",
            "count": count,
            "size": size,
            "seed": seed,
            "elevation_data_used": False,
        },
    )
    return out
