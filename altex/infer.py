"""Windowed map inference with overlap blending and disk-backed accumulation."""

from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import rasterio
import torch
from rasterio.windows import Window

from .model import device_for, load_model
from .raster_io import (
    check_image,
    grid_profile,
    read_rgb,
    source_window,
    write_json,
    write_raster,
)


def starts(length, tile, overlap):
    if length <= tile:
        return [0]
    positions = list(range(0, length - tile + 1, tile - overlap))
    if positions[-1] != length - tile:
        positions.append(length - tile)
    return positions


@torch.inference_mode()
def predict_tile(model, rgb, device, tta=False):
    tensor = (
        torch.from_numpy(rgb.transpose(2, 0, 1).copy()).float()[None].to(device) / 255
    )
    prob = model(tensor).softmax(1)
    if tta:
        prob = (prob + model(tensor.flip(-1)).softmax(1).flip(-1)) / 2
    return prob[0].cpu().numpy()


def segment(
    source, checkpoint, out, aoi=None, tile=1024, overlap=128, device="auto", tta=False
):
    if tile < 32 or not 0 < overlap < tile:
        raise ValueError(
            "Inference tile must be >= 32 and overlap between 1 and tile-1"
        )
    out = Path(out)
    if (out / "segment.json").exists():
        raise ValueError("Run already segmented; choose a new output directory")
    device = device_for(device)
    model = load_model(checkpoint, device)
    out.mkdir(parents=True, exist_ok=True)
    with rasterio.open(source) as src:
        check_image(src)
        area = source_window(src, aoi)
        profile = grid_profile(src, area)
        h, w = int(area.height), int(area.width)
        ys, xs = starts(h, tile, overlap), starts(w, tile, overlap)
        weight = np.maximum(np.outer(np.hanning(tile), np.hanning(tile)), 0.001).astype(
            "float32"
        )
        with TemporaryDirectory(prefix="blend-", dir=out) as temp:
            sums = np.memmap(
                Path(temp) / "sum.bin", mode="w+", shape=(4, h, w), dtype="float32"
            )
            denom = np.memmap(
                Path(temp) / "weight.bin", mode="w+", shape=(h, w), dtype="float32"
            )
            try:
                sums[:] = 0
                denom[:] = 0
                for yi, y in enumerate(ys):
                    for x in xs:
                        hh, ww = min(tile, h - y), min(tile, w - x)
                        rgb, valid = read_rgb(
                            src, Window(area.col_off + x, area.row_off + y, ww, hh)
                        )
                        rgb[~valid] = (249, 192, 122)
                        rgb = np.pad(
                            rgb, ((0, tile - hh), (0, tile - ww), (0, 0)), mode="edge"
                        )
                        probs = predict_tile(model, rgb, device, tta)[:, :hh, :ww]
                        wt = weight[:hh, :ww] * valid
                        sums[:, y : y + hh, x : x + ww] += probs * wt
                        denom[y : y + hh, x : x + ww] += wt
                    print(f"segment row {yi+1}/{len(ys)}", flush=True)
                classes = np.full((h, w), 255, dtype="uint8")
                valid = denom > 0
                for y in range(0, h, 256):
                    block = sums[:, y : y + 256]
                    block /= np.maximum(denom[y : y + 256], 1e-12)
                    classes[y : y + 256] = np.where(
                        valid[y : y + 256], block.argmax(0), 255
                    )
                write_raster(out / "probabilities.tif", sums, profile, valid=valid)
                write_raster(
                    out / "classes.tif", classes, profile, nodata=255, valid=valid
                )
                rgb, _ = read_rgb(src, area)
                rgba = np.concatenate(
                    [rgb, (valid * 255).astype("uint8")[..., None]], axis=2
                )
                write_raster(
                    out / "source.tif", rgba.transpose(2, 0, 1), profile, rgba=True
                )
            finally:
                sums._mmap.close()
                denom._mmap.close()
    write_json(
        out / "segment.json",
        dict(
            source=str(Path(source).resolve()),
            checkpoint=str(Path(checkpoint).resolve()),
            window=[int(area.col_off), int(area.row_off), w, h],
            tile=tile,
            overlap=overlap,
            flip_tta=tta,
            elevation_data_used=False,
            classes=["paper", "contour", "water", "other_ink"],
            ignore=255,
            status="model_predictions_require_review",
        ),
    )
    return out
