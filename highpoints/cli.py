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


def _cmd_cluster(args: argparse.Namespace) -> int:
    """Run cluster analysis on highgrid output."""
    import numpy as np
    from .clusters import (
        load_highgrid_csv, 
        prepare_features, 
        cluster_points, 
        find_local_maxima, 
        write_clustered_csv, 
        write_geopackage, 
        plot_clusters_2d, 
        plot_elevation_profile
    )

    # Load data
    gdf = load_highgrid_csv(Path(args.input_csv), args.point_types)

    # Prepare features
    X = prepare_features(gdf, dims=args.dims, scale=args.scale)

    # Set clustering parameters
    if args.method == 'dbscan':
        kwargs = {'eps': args.eps, 'min_samples': args.min_samples}
    elif args.method == 'kmeans':
        kwargs = {'n_clusters': args.n_clusters}
    elif args.method == 'agglomerative':
        kwargs = {'n_clusters': args.n_clusters_agg}
    else:
        kwargs = {}

    # Cluster
    labels = cluster_points(X, method=args.method, **kwargs)

    # Find maxima
    maxima_gdf = find_local_maxima(gdf, labels, dims=args.dims)

    # Outputs
    if args.csv_output:
        write_clustered_csv(gdf, labels, Path(args.csv_output))

    if args.gpkg_output:
        write_geopackage(gdf, labels, maxima_gdf, Path(args.gpkg_output), overwrite=args.overwrite)

    if args.plot_2d:
        plot_clusters_2d(gdf, labels, Path(args.plot_2d))

    if args.plot_profile:
        plot_elevation_profile(gdf, labels, Path(args.plot_profile))

    print(f"Clustered {len(gdf)} points into {len(np.unique(labels[labels != -1]))} clusters")
    print(f"Found {len(maxima_gdf)} local maxima")
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

    cluster = sub.add_parser(
        "cluster",
        help="Perform cluster analysis on highgrid output points.",
    )
    cluster.add_argument(
        "--input-csv",
        required=True,
        help="Path to highgrid_out.csv file.",
    )
    cluster.add_argument(
        "--point-types",
        nargs='*',
        choices=['hi', 'lo', 'avg'],
        default=['hi', 'lo', 'avg'],
        help="Point types to include (default: all).",
    )
    cluster.add_argument(
        "--dims",
        choices=['1d', '2d', '3d'],
        default='3d',
        help="Dimensionality for clustering (default: 3d).",
    )
    cluster.add_argument(
        "--method",
        choices=['dbscan', 'kmeans', 'agglomerative'],
        default='dbscan',
        help="Clustering method (default: dbscan).",
    )
    cluster.add_argument(
        "--scale",
        action='store_true',
        help="Standardize features before clustering.",
    )
    cluster.add_argument(
        "--eps",
        type=float,
        default=100.0,
        help="DBSCAN eps parameter (default: 100).",
    )
    cluster.add_argument(
        "--min-samples",
        type=int,
        default=5,
        help="DBSCAN min_samples parameter (default: 5).",
    )
    cluster.add_argument(
        "--n-clusters",
        type=int,
        default=10,
        help="K-means n_clusters parameter (default: 10).",
    )
    cluster.add_argument(
        "--n-clusters-agg",
        type=int,
        default=10,
        help="Agglomerative n_clusters parameter (default: 10).",
    )
    cluster.add_argument(
        "--csv-output",
        help="Output clustered CSV path.",
    )
    cluster.add_argument(
        "--gpkg-output",
        help="Output GeoPackage path.",
    )
    cluster.add_argument(
        "--plot-2d",
        help="Output 2D cluster plot path.",
    )
    cluster.add_argument(
        "--plot-profile",
        help="Output elevation profile plot path.",
    )
    cluster.add_argument(
        "--overwrite",
        action='store_true',
        help="Overwrite existing output files.",
    )
    cluster.set_defaults(func=_cmd_cluster)

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
