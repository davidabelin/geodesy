"""Package command-line interface for highpoints tools.

The current package CLI is intentionally narrow: ``python -m highpoints
high-grid`` delegates to :mod:`highpoints.highgrid`. Legacy hidrant/highpoint
experiments live under ``highpoints/legacy`` and are not imported here, which
keeps the package entry point usable without PyQGIS state or moved modules.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from ._paths import out_dir, package_root


def _default_dem_path() -> Path:
    """Return the repository DEM path used by the high-grid subcommand."""
    return package_root().parent / "data" / "tif" / "dc_dem.tif"


def _default_corner_path() -> Path:
    """Return the default corner CSV path exposed in CLI help."""
    return package_root() / "crnrpoints.csv"


def _default_highgrid_output() -> Path:
    """Return the default GeoPackage path for package CLI runs."""
    return out_dir() / "highgrid.gpkg"


def _default_highgrid_csv_output() -> Path:
    """Return the default CSV path for package CLI help text."""
    return out_dir() / "highgrid_out.csv"


def _default_highgrid_project_output() -> Path:
    """Return the default QGIS project path for package CLI help text."""
    return out_dir() / "highgrid.qgz"


def _grid_size(value: str) -> int:
    """Parse and validate the shared ``--grid-size`` argument."""
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
    """Run the high-grid implementation and print its standard summary."""
    from .highgrid import run_from_namespace

    result = run_from_namespace(args)
    print(result.output_path)
    print(f"csv: {result.csv_output_path}")
    if result.project_output_path is not None:
        print(f"qgis: {result.project_output_path}")
    print(f"cells: {result.cell_count}")
    print(f"hi-points: {result.hi_point_count}")
    print(f"lo-points: {result.lo_point_count}")
    print(f"avg-points: {result.avg_point_count}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the package-level parser and register available subcommands."""
    p = argparse.ArgumentParser(
        prog="python -m highpoints", description="Highpoints utilities."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    high_grid = sub.add_parser(
        "high-grid",
        help=(
            "Find the highest, lowest, and mean-nearest DEM points in each "
            "parallelogram grid cell."
        ),
    )
    boundary = high_grid.add_mutually_exclusive_group()
    boundary.add_argument(
        "--boundary",
        help=(
            "Vector or CSV file containing four boundary corners "
            f"(default: {_default_corner_path()})."
        ),
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
        "--csv-output",
        default=None,
        help=(
            "Output CSV path "
            f"(default: highgrid_out.csv beside --output; standard default "
            f"{_default_highgrid_csv_output()})."
        ),
    )
    high_grid.add_argument(
        "--project-output",
        default=None,
        help=(
            "Output QGIS project archive path "
            f"(default: .qgz beside --output; standard default "
            f"{_default_highgrid_project_output()})."
        ),
    )
    high_grid.add_argument(
        "--no-project",
        action="store_true",
        help="Do not write a QGIS .qgz project archive.",
    )
    high_grid.add_argument(
        "--input-crs",
        default=None,
        help=(
            "CRS for CRS-less boundary CSV/inline coordinates. Defaults to "
            "the DEM CRS; vector files with their own CRS keep using it."
        ),
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
        help="Maximum parallelogram residual in DEM CRS units.",
    )
    high_grid.add_argument(
        "--offset-angle",
        default=0.0,
        type=float,
        help=(
            "Clockwise-positive degrees to rotate the working grid around "
            "the input boundary center."
        ),
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
    high_grid.add_argument(
        "--yes",
        action="store_true",
        help="Confirm very expensive grid runs without an interactive prompt.",
    )
    high_grid.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress DEM progress messages.",
    )
    high_grid.add_argument(
        "--progress-interval",
        default=10.0,
        type=float,
        help="Seconds between DEM progress messages (default: 10).",
    )
    high_grid.set_defaults(func=_cmd_high_grid)

    return p


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point used by ``highpoints.__main__``."""
    p = build_parser()
    ns = p.parse_args(list(argv) if argv is not None else None)
    try:
        return int(ns.func(ns))
    except (ImportError, ValueError, RuntimeError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
