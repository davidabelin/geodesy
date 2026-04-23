from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from ._paths import out_dir, package_root


def _default_dem_path() -> Path:
    return package_root().parent / "data" / "tif" / "dc_dem.tif"


def _default_highgrid_output() -> Path:
    return out_dir() / "highgrid.gpkg"


def _grid_size(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "grid size must be an integer from 1 to 100"
        ) from exc
    if parsed < 1 or parsed > 100:
        raise argparse.ArgumentTypeError("grid size must be an integer from 1 to 100")
    return parsed


def _cmd_high_grid(args: argparse.Namespace) -> int:
    from .highgrid import run_from_namespace

    result = run_from_namespace(args)
    print(result.output_path)
    print(f"cells: {result.cell_count}")
    print(f"peaks: {result.peak_count}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m highpoints", description="Highpoints utilities."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    high_grid = sub.add_parser(
        "high-grid",
        help="Find the highest DEM point in each parallelogram grid cell.",
    )
    boundary = high_grid.add_mutually_exclusive_group(required=True)
    boundary.add_argument(
        "--boundary", help="Vector file containing four boundary corners."
    )
    boundary.add_argument(
        "--corners",
        help="Inline W,N,E,S corners as 'lon,lat;lon,lat;lon,lat;lon,lat'.",
    )
    high_grid.add_argument("--grid-size", required=True, type=_grid_size)
    high_grid.add_argument(
        "--dem", default=str(_default_dem_path()), help="DEM GeoTIFF path."
    )
    high_grid.add_argument(
        "--output",
        default=str(_default_highgrid_output()),
        help="Output GeoPackage path.",
    )
    high_grid.add_argument(
        "--work-crs", default="EPSG:26985", help="Projected work CRS."
    )
    high_grid.add_argument(
        "--corner-names",
        default="auto",
        help="auto, or four comma-separated names in W,N,E,S order.",
    )
    high_grid.add_argument(
        "--parallelogram-tolerance-m",
        default=30.0,
        type=float,
        help="Maximum parallelogram residual in work CRS units.",
    )
    high_grid.add_argument(
        "--all-touched",
        action="store_true",
        help="Include DEM pixels touched by a cell, not just center-in-cell pixels.",
    )
    high_grid.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing output GeoPackage.",
    )
    high_grid.set_defaults(func=_cmd_high_grid)

    return p


def main(argv: Sequence[str] | None = None) -> int:
    p = build_parser()
    ns = p.parse_args(list(argv) if argv is not None else None)
    try:
        return int(ns.func(ns))
    except (ImportError, ValueError, RuntimeError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
