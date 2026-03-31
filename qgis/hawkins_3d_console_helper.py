"""Thin QGIS Desktop console wrapper around the Hawkins pipeline.

The QGIS Python console is useful for ad hoc reruns, but it is not treated as
an interactive CLI. This helper simply forwards preset paths/options into the
same pipeline used by the external batch/CLI runner.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from hawkins_3d_core import default_config, run_pipeline
from qgis_runtime import find_repo_root


def run_hawkins(
    mode: str = "pilot",
    aoi: str = "auto",
    contour_interval: str = "auto",
    z_unit: str = "auto",
):
    """Run the Hawkins pipeline from the QGIS Python console."""
    repo_root = find_repo_root(CURRENT_DIR)
    config = default_config(
        mode=mode,
        output_dir=repo_root / "qgis/Hawkins/3D map",
        aoi=aoi,
        contour_interval=contour_interval,
        z_unit=z_unit,
    )
    report = run_pipeline(config)
    print(json.dumps(report, indent=2))
    return report


__all__ = ["run_hawkins"]
