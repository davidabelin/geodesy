"""Small configurable four-class U-Net; no elevation features or outputs."""

import torch
from torch import nn
from torch.nn import functional as F


def block(inp, out):
    return nn.Sequential(
        nn.Conv2d(inp, out, 3, padding=1),
        nn.BatchNorm2d(out),
        nn.ReLU(),
        nn.Conv2d(out, out, 3, padding=1),
        nn.BatchNorm2d(out),
        nn.ReLU(),
    )


class UNet(nn.Module):
    def __init__(self, width=32):
        super().__init__()
        self.down = nn.ModuleList(
            [block(3, width)]
            + [block(width * 2**i, width * 2 ** (i + 1)) for i in range(4)]
        )
        self.up = nn.ModuleList(
            [
                block(width * (2 ** (i + 1) + 2**i), width * 2**i)
                for i in reversed(range(4))
            ]
        )
        self.head = nn.Conv2d(width, 4, 1)

    def forward(self, x):
        skips = []
        for i, layer in enumerate(self.down):
            if i:
                x = F.max_pool2d(x, 2)
            x = layer(x)
            skips.append(x)
        for layer, skip in zip(self.up, reversed(skips[:-1])):
            x = F.interpolate(
                x, size=skip.shape[-2:], mode="bilinear", align_corners=False
            )
            x = layer(torch.cat([x, skip], dim=1))
        return self.head(x)


def device_for(name="auto"):
    if name == "auto":
        name = "cuda" if torch.cuda.is_available() else "cpu"
    if name == "cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA requested but unavailable; use --device cpu")
    return torch.device(name)


def load_model(checkpoint, device):
    state = torch.load(checkpoint, map_location="cpu", weights_only=True)
    if state.get("format") != "altex-visible-ink-v1":
        raise ValueError("Not an altex visible-ink checkpoint")
    model = UNet(state["width"])
    model.load_state_dict(state["model"])
    return model.to(device).eval()
