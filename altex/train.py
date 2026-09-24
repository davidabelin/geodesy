"""Train on procedural drawings and optional reviewed/provisional map tiles."""

from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torch.nn import functional as F
from torch.utils.data import DataLoader

from .data import TrainingTiles, load_real, read_manifest
from .heuristics import save_labels
from .model import UNet, device_for
from .raster_io import write_json
from .report import confusion, metrics
from .synth import make_tile


def loss_value(logits, target, weights):
    valid = target != 255
    ce = F.cross_entropy(logits, target, ignore_index=255, reduction="none")
    ce = ce.sum((1, 2)) / valid.sum((1, 2)).clamp_min(1)
    prob = logits.softmax(1)[:, 1] * valid
    truth = (target == 1).float()
    dice = 1 - (2 * (prob * truth).sum((1, 2)) + 1) / (
        prob.sum((1, 2)) + truth.sum((1, 2)) + 1
    )
    return ((ce + dice) * weights).mean()


@torch.inference_mode()
def evaluate(model, samples, device):
    matrix = np.zeros((4, 4), dtype="int64")
    for image, labels in samples:
        # Tiling here also bounds memory for manually supplied large review tiles.
        for y in range(0, labels.shape[0], 512):
            for x in range(0, labels.shape[1], 512):
                tile = image[y : y + 512, x : x + 512]
                h, w = tile.shape[:2]
                tile = np.pad(
                    tile,
                    ((0, max(0, 32 - h)), (0, max(0, 32 - w)), (0, 0)),
                    mode="edge",
                )
                tensor = (
                    torch.from_numpy(tile.transpose(2, 0, 1).copy())
                    .float()[None]
                    .to(device)
                    / 255
                )
                pred = model(tensor).argmax(1)[0, :h, :w].cpu().numpy()
                matrix += confusion(pred, labels[y : y + h, x : x + w])
    return metrics(matrix)


def train(
    out,
    real=None,
    epochs=30,
    tiles_per_epoch=400,
    size=512,
    batch=4,
    width=32,
    seed=0,
    device="auto",
    lr=0.001,
):
    out = Path(out)
    if (out / "best.pt").exists():
        raise ValueError(
            "Model directory already contains best.pt; choose a new output directory"
        )
    records = read_manifest(real)
    reviewed = [r for r in records if r["split"] == "validation" and r["reviewed"]]
    for rec in reviewed:
        _, labels = load_real(real, rec)
        if not (labels != 255).any():
            raise ValueError("A reviewed validation tile has no labeled pixels")
    # Positive training seeds and negative validation seeds occupy disjoint spaces.
    synthetic_val = [make_tile(size, -i - 1) for i in range(8)]
    return _train(
        out,
        real,
        records,
        reviewed,
        epochs,
        tiles_per_epoch,
        size,
        batch,
        width,
        seed,
        device,
        lr,
        synthetic_val,
    )


def _train(
    out,
    real,
    records,
    reviewed,
    epochs,
    tiles_per_epoch,
    size,
    batch,
    width,
    seed,
    device,
    lr,
    synthetic_val,
):
    device = device_for(device)
    torch.manual_seed(seed)
    model = UNet(width).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, epochs)
    scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda")
    out.mkdir(parents=True, exist_ok=True)
    history, best = [], -1.0
    for epoch in range(epochs):
        dataset = TrainingTiles(
            tiles_per_epoch, size, seed + epoch * tiles_per_epoch, real, records
        )
        loader = DataLoader(dataset, batch_size=batch, shuffle=True, num_workers=0)
        model.train()
        total = 0.0
        for images, labels, weights in loader:
            images, labels, weights = (
                images.to(device),
                labels.to(device),
                weights.float().to(device),
            )
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast(device.type, enabled=device.type == "cuda"):
                loss = loss_value(model(images), labels, weights)
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
            total += float(loss.detach()) * len(images)
        scheduler.step()
        model.eval()
        synthetic = evaluate(model, synthetic_val, device)
        actual = (
            evaluate(model, (load_real(real, r) for r in reviewed), device)
            if reviewed
            else None
        )
        row = dict(
            epoch=epoch + 1,
            loss=total / tiles_per_epoch,
            synthetic=synthetic,
            reviewed_hawkins=actual,
        )
        history.append(row)
        score = (actual or synthetic)["contour_f1"]
        state = dict(
            format="altex-visible-ink-v1",
            width=width,
            model=model.state_dict(),
            epoch=epoch + 1,
            seed=seed,
            elevation_data_used=False,
            selection="reviewed_hawkins" if actual else "synthetic_only",
        )
        torch.save(state, out / "last.pt")
        if score > best:
            best = score
            torch.save(state, out / "best.pt")
        write_json(
            out / "training.json",
            dict(
                device=str(device),
                history=history,
                validation_status=(
                    "reviewed_hawkins" if actual else "NO_REVIEWED_HAWKINS_VALIDATION"
                ),
                real_manifest=str(Path(real).resolve()) if real else None,
                configuration=dict(
                    size=size,
                    width=width,
                    batch=batch,
                    seed=seed,
                    lr=lr,
                    epochs=epochs,
                    tiles_per_epoch=tiles_per_epoch,
                ),
            ),
        )
        image, _ = synthetic_val[0]
        with torch.inference_mode():
            tensor = (
                torch.from_numpy(image.transpose(2, 0, 1).copy())
                .float()[None]
                .to(device)
                / 255
            )
            preview = model(tensor).argmax(1)[0].cpu().numpy().astype("uint8")
        Image.fromarray(image).save(out / "preview_source.png")
        save_labels(out / "preview_labels.png", preview)
        print(
            f"epoch {epoch+1}/{epochs}: loss={row['loss']:.4f}, contour F1={score:.4f} ({state['selection']})",
            flush=True,
        )
    return out
