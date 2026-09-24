"""Training samples and checked spatial holdouts for reviewed map labels."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image
from torch.utils.data import Dataset

from .raster_io import read_labels
from .synth import make_tile


def read_manifest(directory):
    if directory is None:
        return []
    directory = Path(directory)
    doc = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if doc.get("kind") != "hawkins_review":
        raise ValueError("Real tiles require a Hawkins review manifest")
    records = doc["tiles"]
    for rec in records:
        if (
            rec["split"] not in ("train", "validation")
            or type(rec.get("reviewed")) is not bool
        ):
            raise ValueError(
                "Each tile needs split=train|validation and a boolean reviewed flag"
            )
        if len(rec["window"]) != 4 or min(rec["window"][2:]) <= 0:
            raise ValueError("Invalid tile window")
        if (
            not (directory / rec["image"]).is_file()
            or not (directory / rec["label"]).is_file()
        ):
            raise ValueError("Manifest references missing tile images or labels")
    train = [r for r in records if r["split"] == "train"]
    val = [r for r in records if r["split"] == "validation"]
    for a in train:
        ax, ay, aw, ah = a["window"]
        for b in val:
            bx, by, bw, bh = b["window"]
            if ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah:
                raise ValueError("Training and validation tile windows overlap")
            if a["image"] == b["image"] or a["label"] == b["label"]:
                raise ValueError(
                    "Training and validation cannot share image/label files"
                )
    return records


def load_real(directory, rec):
    with Image.open(Path(directory) / rec["image"]) as im:
        image = np.array(im.convert("RGB"))
    labels = read_labels(Path(directory) / rec["label"])
    if image.shape[:2] != labels.shape or image.shape[:2] != tuple(
        reversed(rec["window"][2:])
    ):
        raise ValueError("Tile image/label dimensions must match their manifest window")
    return image, labels


class TrainingTiles(Dataset):
    def __init__(self, count, size, seed, real=None, records=()):
        self.count, self.size, self.seed = count, size, seed
        self.real = real
        self.records = [r for r in records if r["split"] == "train"]

    def __len__(self):
        return self.count

    def __getitem__(self, index):
        rng = np.random.default_rng(self.seed + index)
        weight = 1.0
        if self.records and index % 2:
            rec = self.records[int(rng.integers(len(self.records)))]
            image, labels = load_real(self.real, rec)
            weight = 1.0 if rec["reviewed"] else 0.2
            ph, pw = max(0, self.size - labels.shape[0]), max(
                0, self.size - labels.shape[1]
            )
            image = np.pad(image, ((0, ph), (0, pw), (0, 0)), mode="edge")
            labels = np.pad(labels, ((0, ph), (0, pw)), constant_values=255)
            y = int(rng.integers(labels.shape[0] - self.size + 1))
            x = int(rng.integers(labels.shape[1] - self.size + 1))
            image, labels = (
                image[y : y + self.size, x : x + self.size],
                labels[y : y + self.size, x : x + self.size],
            )
        else:
            image, labels = make_tile(self.size, self.seed + index)
        k = int(rng.integers(4))
        image, labels = np.rot90(image, k), np.rot90(labels, k)
        if rng.random() < 0.5:
            image, labels = image[:, ::-1], labels[:, ::-1]
        return (
            np.ascontiguousarray(image.transpose(2, 0, 1), dtype="float32") / 255,
            np.ascontiguousarray(labels, dtype="int64"),
            weight,
        )
