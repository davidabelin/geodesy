"""CLI for visible historical contour extraction, with no elevation inputs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ._paths import default_source_tif, out_dir, package_root


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def aoi_value(value):
    try:
        parts = tuple(map(int, value.split(",")))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "AOI must be x,y,width,height in source pixels"
        ) from exc
    if len(parts) != 4 or min(parts[:2]) < 0 or min(parts[2:]) <= 0:
        raise argparse.ArgumentTypeError(
            "AOI must be nonnegative x,y and positive width,height"
        )
    return parts


def parser():
    p = argparse.ArgumentParser(
        prog="python -m altex",
        description="Extract visible Hawkins contours for review and counting. No modern elevation inputs or altitude output.",
    )
    commands = p.add_subparsers(dest="command", required=True)
    synth = commands.add_parser(
        "synth", help="render procedural contour training examples"
    )
    synth.add_argument(
        "--out", type=Path, default=package_root() / "train_data" / "synth"
    )
    synth.add_argument("--count", type=positive, default=64)
    synth.add_argument("--size", type=positive, default=512)
    synth.add_argument("--seed", type=int, default=0)
    tiles = commands.add_parser(
        "tiles", help="export editable provisional labels with spatial holdout"
    )
    tiles.add_argument("--source", type=Path, default=default_source_tif())
    tiles.add_argument(
        "--out", type=Path, default=package_root() / "train_data" / "real"
    )
    tiles.add_argument("--count", type=positive, default=24)
    tiles.add_argument("--size", type=positive, default=512)
    tiles.add_argument("--seed", type=int, default=0)
    tiles.add_argument("--aoi", type=aoi_value)
    train = commands.add_parser(
        "train", help="train U-Net; evaluate only reviewed Hawkins holdouts"
    )
    train.add_argument("--out", type=Path, default=out_dir() / "model")
    train.add_argument(
        "--real", type=Path, help="directory containing a review manifest"
    )
    for name, default in (
        ("epochs", 30),
        ("tiles-per-epoch", 400),
        ("size", 512),
        ("batch", 4),
        ("width", 32),
    ):
        train.add_argument(f"--{name}", type=positive, default=default)
    train.add_argument("--seed", type=int, default=0)
    train.add_argument("--lr", type=float, default=0.001)
    train.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    segment = commands.add_parser(
        "segment", help="blend overlapping predictions on the source grid"
    )
    segment.add_argument("--source", type=Path, default=default_source_tif())
    segment.add_argument("--checkpoint", required=True, type=Path)
    segment.add_argument("--out", type=Path, required=True)
    segment.add_argument("--aoi", type=aoi_value)
    segment.add_argument("--tile", type=positive, default=1024)
    segment.add_argument("--overlap", type=positive, default=128)
    segment.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    segment.add_argument(
        "--tta", action="store_true", help="average horizontal-flip predictions"
    )
    trace = commands.add_parser(
        "trace", help="trace fragments and export separately reviewed gap proposals"
    )
    trace.add_argument("--run-dir", type=Path, required=True)
    trace.add_argument("--out", type=Path, help="default: RUN_DIR/trace")
    trace.add_argument(
        "--labels",
        type=Path,
        help="corrected one-band GeoTIFF or indexed PNG; overrides model labels",
    )
    trace.add_argument(
        "--accept-repairs",
        dest="accepted",
        type=Path,
        help="JSON with current revision and explicit accepted_ids",
    )
    trace.add_argument("--low", type=float, default=0.3)
    trace.add_argument("--high", type=float, default=0.6)
    trace.add_argument("--max-gap", type=positive, default=12)
    trace.add_argument("--min-evidence", type=float, default=0.05)
    trace.add_argument(
        "--transects",
        type=Path,
        help="JSON list of local pixel start/end, expected count and readable flag",
    )
    return p


def main(argv=None):
    args = vars(parser().parse_args(argv))
    command = args.pop("command")
    try:
        if command == "synth":
            from .synth import export_tiles

            result = export_tiles(**args)
        elif command == "tiles":
            from .tiles import export_tiles

            result = export_tiles(**args)
        elif command == "train":
            if args["size"] < 32 or args["seed"] < 0 or not 0 < args["lr"] < 1:
                raise ValueError(
                    "Training requires size >= 32, seed >= 0 and 0 < lr < 1"
                )
            from .train import train

            result = train(**args)
        elif command == "segment":
            from .infer import segment

            result = segment(**args)
        else:
            from .topology import trace

            result = trace(**args)
        print(result)
        return 0
    except (ImportError, OSError, ValueError, KeyError) as exc:
        print(f"altex: {exc}", file=sys.stderr)
        return 2
