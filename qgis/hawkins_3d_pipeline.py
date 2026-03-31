"""CLI entrypoint for the Hawkins 3D reconstruction workflow.

Run this script through the OSGeo4W-provided ``python-qgis.bat`` launcher so
GDAL, PROJ, and PyQGIS all come from the same QGIS Desktop runtime.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from hawkins_3d_core import default_config, run_pipeline


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line interface for audit, pilot, and full runs."""
    parser = argparse.ArgumentParser(
        description="Audit or build Hawkins-derived terrain assets with the QGIS runtime."
    )
    parser.add_argument("--mode", choices=["audit", "pilot", "full"], required=True)
    parser.add_argument("--source-raster", type=Path, help="Primary Hawkins raster path.")
    parser.add_argument("--output-dir", type=Path, help="Directory for generated outputs.")
    parser.add_argument(
        "--aoi",
        default="auto",
        help="auto or xmin,ymin,xmax,ymax in the source raster CRS.",
    )
    parser.add_argument(
        "--contour-interval",
        default="auto",
        help="Contour interval in meters for QA contours, or auto.",
    )
    parser.add_argument(
        "--z-unit",
        default="auto",
        help="Default unit for parsing raw contour labels when elev_m is blank.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments, run the pipeline, and print the JSON report."""
    args = build_parser().parse_args(argv)
    config = default_config(
        mode=args.mode,
        source_raster=args.source_raster,
        output_dir=args.output_dir,
        aoi=args.aoi,
        contour_interval=args.contour_interval,
        z_unit=args.z_unit,
    )
    report = run_pipeline(config)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
