from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from ._paths import out_dir
from .pipeline import generate_kml_from_csv


def _resolve_out_path(path: Path) -> Path:
    return path if path.is_absolute() else (out_dir() / path)


def _default_output_for_csv(csv_path: Path) -> Path:
    stem = csv_path.name.rsplit(".", 1)[0]
    return out_dir() / f"{stem}.kml"


def _cmd_kml(args: argparse.Namespace) -> int:
    csv_path = Path(args.csv)
    out_path = _resolve_out_path(Path(args.output)) if args.output else _default_output_for_csv(csv_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    generate_kml_from_csv(csv_path=csv_path, output_kml=out_path, include_points=bool(args.include_points))
    print(out_path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m spirals", description="Spirals utilities.")
    sub = p.add_subparsers(dest="cmd", required=True)

    kml = sub.add_parser("kml", help="Connect CSV points into KML line sequences.")
    kml.add_argument("--csv", required=True, help="Input CSV path (must include LOC, LAT, LON columns).")
    kml.add_argument(
        "--output",
        default=None,
        help="Output KML path (default: spirals/out/<csv-stem>.kml). Relative goes under spirals/out/.",
    )
    kml.add_argument("--include-points", action="store_true", help="Include point placemarks in the KML.")
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

