from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from ._paths import out_dir


def _resolve_out_path(path: Path) -> Path:
    return path if path.is_absolute() else (out_dir() / path)


def _default_kml_name(csv_path: Path) -> str:
    return f"{csv_path.stem}.kml"


def _cmd_kml(args: argparse.Namespace) -> int:
    from .draw_kml_spirals import generate_kml_from_csv

    csv_path = Path(args.csv)
    out = Path(args.output) if args.output else Path(_default_kml_name(csv_path))
    out_path = _resolve_out_path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    generate_kml_from_csv(str(csv_path), str(out_path), include_points=bool(args.include_points))
    print(out_path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m spirals", description="Spirals utilities.")
    sub = p.add_subparsers(dest="cmd", required=True)

    kml = sub.add_parser("kml", help="Generate a KML connecting points from a CSV.")
    kml.add_argument("--csv", type=str, required=True, help="Input CSV path with LOC,LAT,LON.")
    kml.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output KML filename/path (relative goes under spirals/out/).",
    )
    kml.add_argument(
        "--include-points",
        action="store_true",
        help="Also include point placemarks in the KML.",
    )
    kml.set_defaults(func=_cmd_kml)

    return p


def main(argv: Sequence[str] | None = None) -> int:
    p = build_parser()
    ns = p.parse_args(list(argv) if argv is not None else None)
    try:
        return int(ns.func(ns))
    except (ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

