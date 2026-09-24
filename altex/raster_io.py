"""Map-image I/O with explicit grid and validity preservation."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import ColorInterp, Resampling
from rasterio.windows import Window


def write_json(path, value):
    Path(path).write_text(
        json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


def source_window(src, aoi=None):
    x, y, w, h = aoi or (0, 0, src.width, src.height)
    if min(x, y) < 0 or min(w, h) < 1 or x + w > src.width or y + h > src.height:
        raise ValueError(
            "AOI must be a positive pixel window entirely inside the source"
        )
    return Window(x, y, w, h)


def check_image(src):
    if src.count not in (3, 4) or any(d != "uint8" for d in src.dtypes):
        raise ValueError(
            "Source must be an RGB/RGBA uint8 map image, not an elevation raster"
        )
    if src.crs is None:
        raise ValueError("Source map must have a CRS")


def read_rgb(src, window):
    check_image(src)
    rgb = src.read((1, 2, 3), window=window).transpose(1, 2, 0)
    valid = np.all(src.read_masks((1, 2, 3), window=window) > 0, axis=0)
    if src.count == 4:
        valid &= src.read(4, window=window) > 0
    return rgb, valid


def grid_profile(src, window):
    return dict(
        crs=src.crs,
        transform=src.window_transform(window),
        width=int(window.width),
        height=int(window.height),
    )


def write_raster(path, array, profile, nodata=None, valid=None, rgba=False):
    array = np.asarray(array)
    if array.ndim == 2:
        array = array[None]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        **profile,
        count=len(array),
        dtype=array.dtype,
        nodata=nodata,
        compress="deflate",
        tiled=True,
        BIGTIFF="IF_SAFER",
    ) as dst:
        dst.write(array)
        if valid is not None:
            dst.write_mask(np.asarray(valid, dtype="uint8") * 255)
        if rgba:
            dst.colorinterp = (
                ColorInterp.red,
                ColorInterp.green,
                ColorInterp.blue,
                ColorInterp.alpha,
            )
        factors = [f for f in (2, 4, 8, 16) if min(array.shape[-2:]) // f >= 16]
        if factors:
            dst.build_overviews(factors, Resampling.nearest)


def read_labels(path, profile=None):
    """Read class IDs, preserving indexed PNG values (never RGB palette colors)."""
    from PIL import Image

    path = Path(path)
    if path.suffix.lower() in (".tif", ".tiff"):
        with rasterio.open(path) as src:
            if src.count != 1:
                raise ValueError("Label raster must contain one band of class IDs")
            if profile is not None and (
                src.crs != profile["crs"]
                or not src.transform.almost_equals(profile["transform"])
                or (src.height, src.width) != (profile["height"], profile["width"])
            ):
                raise ValueError(
                    "Corrected labels must match the run's CRS, transform and dimensions"
                )
            values = src.read(1)
    else:
        with Image.open(path) as im:
            values = np.array(im)
    if values.ndim != 2 or not np.isin(values, [0, 1, 2, 3, 255]).all():
        raise ValueError("Labels must be indexed/grayscale class IDs 0, 1, 2, 3 or 255")
    if profile and values.shape != (profile["height"], profile["width"]):
        raise ValueError("Corrected label dimensions differ from the run")
    return values.astype("uint8")
