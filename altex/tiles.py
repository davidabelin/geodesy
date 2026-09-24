"""Export geographically separated map tiles for correction and review."""

from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.windows import Window

from .heuristics import pseudo_labels, save_labels
from .raster_io import check_image, read_rgb, source_window, write_json


def export_tiles(source, out, count=24, size=512, seed=0, aoi=None):
    out = Path(out)
    if (out / "manifest.json").exists():
        raise ValueError(
            "Tile directory already has a manifest; choose a new directory to preserve corrections"
        )
    rng = np.random.default_rng(seed)
    with rasterio.open(source) as src:
        check_image(src)
        area = source_window(src, aoi)
        cols, rows = int(area.width) // size, int(area.height) // size
        if cols < 2 or rows < 1 or count < 2:
            raise ValueError(
                "Need at least two full tile columns and count >= 2 for spatial holdout"
            )
        split_col = min(cols - 1, max(1, int(cols * 0.75)))
        pools = {"train": [], "validation": []}
        for row in range(rows):
            for col in range(cols):
                split = "train" if col < split_col else "validation"
                pools[split].append(
                    (int(area.col_off) + col * size, int(area.row_off) + row * size)
                )
        (out / "images").mkdir(parents=True, exist_ok=True)
        (out / "labels").mkdir(exist_ok=True)
        records = []
        for split, pool in pools.items():
            rng.shuffle(pool)
            wanted = (
                max(1, count // 4)
                if split == "validation"
                else count - max(1, count // 4)
            )
            for x, y in pool:
                if sum(r["split"] == split for r in records) >= wanted:
                    break
                rgb, valid = read_rgb(src, Window(x, y, size, size))
                if valid.mean() < 0.5:
                    continue
                name = f"{split}_{x}_{y}"
                Image.fromarray(rgb).save(out / "images" / f"{name}.png")
                save_labels(out / "labels" / f"{name}.png", pseudo_labels(rgb, valid))
                records.append(
                    dict(
                        image=f"images/{name}.png",
                        label=f"labels/{name}.png",
                        window=[x, y, size, size],
                        split=split,
                        reviewed=False,
                    )
                )
        if not all(any(r["split"] == s for r in records) for s in pools):
            raise ValueError(
                "No valid map tiles in one spatial split; choose another AOI"
            )
        write_json(
            out / "manifest.json",
            dict(
                kind="hawkins_review",
                source=str(Path(source).resolve()),
                crs=str(src.crs),
                transform=list(src.transform),
                tiles=records,
                instructions="Correct class IDs in labels, then set reviewed=true per corrected tile. Keep splits fixed.",
            ),
        )
    return out
