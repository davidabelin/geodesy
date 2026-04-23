"""Regression tests for the highgrid cell-point workflow.

The synthetic DEM is a 4x4 raster over a 4x4 projected square. The 2x2 test
grid makes each cell cover exactly four pixels, which keeps expected high,
low, mean, and avg-nearest values easy to verify without fixtures from the
larger DC data tree.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import Point

from highpoints import highgrid as hg

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_test_dem(path: Path, *, crs: str = "EPSG:3857") -> Path:
    """Write a 4x4 DEM with predictable values for 2x2 grid assertions."""
    values = np.array(
        [
            [7, 8, 15, 16],
            [5, 6, 13, 14],
            [3, 4, 11, 12],
            [1, 2, 9, 10],
        ],
        dtype=np.float32,
    )
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=values.shape[0],
        width=values.shape[1],
        count=1,
        dtype="float32",
        crs=crs,
        transform=from_origin(0, 4, 1, 1),
        nodata=-9999.0,
    ) as dataset:
        dataset.write(values, 1)
    return path


def _write_boundary(path: Path, *, east: tuple[float, float] = (4, 4)) -> Path:
    """Write a four-point projected boundary fixture in W,N,E,S order."""
    gdf = gpd.GeoDataFrame(
        {"Name": ["W", "N", "E", "S"]},
        geometry=[
            Point(0, 0),
            Point(0, 4),
            Point(*east),
            Point(4, 0),
        ],
        crs="EPSG:3857",
    )
    gdf.to_file(path, layer="corners", driver="GPKG", engine="pyogrio")
    return path


def _write_boundary_csv(path: Path) -> Path:
    """Write a small default-style corner CSV fixture."""
    path.write_text(
        "\n".join(
            [
                "Name,Lat,Lon,Alt",
                "W,0,0,1",
                "N,4,0,2",
                "E,4,4,3",
                "S,0,4,4",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_run_highgrid_finds_expected_hi_lo_avg_points_layers_and_csv(tmp_path):
    """End-to-end core run writes expected layers, values, and CSV rows."""
    dem = _write_test_dem(tmp_path / "dem.tif")
    boundary = _write_boundary(tmp_path / "boundary.gpkg")
    output = tmp_path / "highgrid.gpkg"
    csv_output = tmp_path / "highgrid_out.csv"

    result = hg.run_highgrid(
        dem_path=dem,
        boundary_path=boundary,
        output_path=output,
        csv_output_path=csv_output,
        grid_size=2,
        work_crs="EPSG:3857",
        parallelogram_tolerance_m=0.01,
        overwrite=True,
    )

    assert result.cell_count == 4
    assert result.peak_count == 4
    assert result.hi_point_count == 4
    assert result.lo_point_count == 4
    assert result.avg_point_count == 4
    assert output.exists()
    assert csv_output.exists()
    layers = set(pyogrio.list_layers(output)[:, 0])
    assert layers == {
        "highgrid_cells",
        "hi-points",
        "lo-points",
        "avg-points",
        "highgrid_boundary",
        "highgrid_corners",
    }

    cells = gpd.read_file(output, layer="highgrid_cells")
    assert len(cells) == 4
    expected_hi = {
        "U01_V01": 4.0,
        "U01_V02": 12.0,
        "U02_V01": 8.0,
        "U02_V02": 16.0,
    }
    expected_lo = {
        "U01_V01": 1.0,
        "U01_V02": 9.0,
        "U02_V01": 5.0,
        "U02_V02": 13.0,
    }
    expected_avg = {
        "U01_V01": 3.0,
        "U01_V02": 11.0,
        "U02_V01": 7.0,
        "U02_V02": 15.0,
    }
    expected_mean = {
        "U01_V01": 2.5,
        "U01_V02": 10.5,
        "U02_V01": 6.5,
        "U02_V02": 14.5,
    }
    for row in cells.itertuples():
        assert row.status == "ok"
        assert row.valid_px == 4
        assert row.hi_elev_m == pytest.approx(expected_hi[row.cell_name])
        assert row.lo_elev_m == pytest.approx(expected_lo[row.cell_name])
        assert row.avg_elev_m == pytest.approx(expected_avg[row.cell_name])
        assert row.mean_elev_m == pytest.approx(expected_mean[row.cell_name])
        assert row.avg_delta_m == pytest.approx(0.5)
        assert row.offset_angle_deg == 0

    hi_points = gpd.read_file(output, layer="hi-points")
    lo_points = gpd.read_file(output, layer="lo-points")
    avg_points = gpd.read_file(output, layer="avg-points")
    boundary_layer = gpd.read_file(output, layer="highgrid_boundary")
    corners = gpd.read_file(output, layer="highgrid_corners")
    assert len(hi_points) == 4
    assert len(lo_points) == 4
    assert len(avg_points) == 4
    assert set(hi_points["point_type"]) == {"hi"}
    assert set(lo_points["point_type"]) == {"lo"}
    assert set(avg_points["point_type"]) == {"avg"}
    assert len(boundary_layer) == 1
    assert list(corners["corner"]) == ["W", "N", "E", "S"]

    csv_rows = pd.read_csv(csv_output)
    assert len(csv_rows) == 12
    assert set(csv_rows["point_type"]) == {"hi", "lo", "avg"}
    assert set(csv_rows["offset_angle_deg"]) == {0}


@pytest.mark.parametrize("value", ["0", "101", "not-an-int"])
def test_parse_grid_size_rejects_invalid_values(value):
    """Grid sizes outside 1..100, or not integers, fail argparse validation."""
    with pytest.raises(argparse.ArgumentTypeError):
        hg.parse_grid_size(value)


def test_default_corner_csv_is_used_when_boundary_is_omitted(tmp_path, monkeypatch):
    """The importable core falls back to the default corner CSV when omitted."""
    dem = _write_test_dem(tmp_path / "dem.tif", crs="EPSG:4269")
    default_csv = _write_boundary_csv(tmp_path / "crnrpoints.csv")
    output = tmp_path / "default_csv.gpkg"

    monkeypatch.setattr(hg, "default_corner_path", lambda: default_csv)

    result = hg.run_highgrid(
        dem_path=dem,
        output_path=output,
        grid_size=2,
        work_crs="EPSG:4269",
        parallelogram_tolerance_m=0.01,
        overwrite=True,
    )

    assert result.hi_point_count == 4
    assert result.lo_point_count == 4
    assert result.avg_point_count == 4
    corners = gpd.read_file(output, layer="highgrid_corners")
    assert list(corners["alt_m"]) == [1, 2, 3, 4]


def test_offset_angle_rotates_working_corners(tmp_path):
    """A clockwise-positive offset angle rotates corners before cell output."""
    dem = _write_test_dem(tmp_path / "dem.tif")
    boundary = _write_boundary(tmp_path / "boundary.gpkg")
    output = tmp_path / "rotated.gpkg"

    result = hg.run_highgrid(
        dem_path=dem,
        boundary_path=boundary,
        output_path=output,
        grid_size=2,
        work_crs="EPSG:3857",
        parallelogram_tolerance_m=0.01,
        offset_angle_deg=90,
        overwrite=True,
    )

    assert result.avg_point_count == 4
    corners = gpd.read_file(output, layer="highgrid_corners")
    coords = {
        row.corner: (row.geometry.x, row.geometry.y) for row in corners.itertuples()
    }
    assert coords["W"] == pytest.approx((0, 4))
    assert coords["N"] == pytest.approx((4, 4))
    assert coords["E"] == pytest.approx((4, 0))
    assert coords["S"] == pytest.approx((0, 0))
    assert set(corners["offset_angle_deg"]) == {90}

    cells = gpd.read_file(output, layer="highgrid_cells")
    assert set(cells["offset_angle_deg"]) == {90}


def test_boundary_and_corners_are_mutually_exclusive(tmp_path):
    """The script CLI rejects simultaneous boundary file and inline corners."""
    dem = _write_test_dem(tmp_path / "dem.tif")
    boundary = _write_boundary(tmp_path / "boundary.gpkg")
    output = tmp_path / "exclusive.gpkg"

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "highpoints" / "highgrid.py"),
            "--boundary",
            str(boundary),
            "--corners",
            "0,0;0,4;4,4;4,0",
            "--grid-size",
            "2",
            "--dem",
            str(dem),
            "--output",
            str(output),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "not allowed with argument" in completed.stderr


def test_parallelogram_validation_rejects_bad_fourth_corner(tmp_path):
    """Bad four-corner input fails before any output is written."""
    dem = _write_test_dem(tmp_path / "dem.tif")
    boundary = _write_boundary(tmp_path / "bad_boundary.gpkg", east=(5, 5))

    with pytest.raises(ValueError, match="not a parallelogram"):
        hg.run_highgrid(
            dem_path=dem,
            boundary_path=boundary,
            output_path=tmp_path / "bad.gpkg",
            grid_size=2,
            work_crs="EPSG:3857",
            parallelogram_tolerance_m=0.1,
            overwrite=True,
        )


def test_script_and_package_cli_invocations(tmp_path):
    """Direct script and package CLI paths both delegate to highgrid."""
    dem = _write_test_dem(tmp_path / "dem.tif")
    boundary = _write_boundary(tmp_path / "boundary.gpkg")
    script_output = tmp_path / "script.gpkg"
    package_output = tmp_path / "package.gpkg"
    common_args = [
        "--boundary",
        str(boundary),
        "--grid-size",
        "2",
        "--dem",
        str(dem),
        "--work-crs",
        "EPSG:3857",
        "--overwrite",
    ]

    script_run = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "highpoints" / "highgrid.py"),
            *common_args,
            "--output",
            str(script_output),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert script_run.returncode == 0, script_run.stderr
    assert "cells: 4" in script_run.stdout
    assert "hi-points: 4" in script_run.stdout
    assert "lo-points: 4" in script_run.stdout
    assert "avg-points: 4" in script_run.stdout
    assert "csv:" in script_run.stdout
    assert script_output.exists()

    package_run = subprocess.run(
        [
            sys.executable,
            "-m",
            "highpoints",
            "high-grid",
            *common_args,
            "--output",
            str(package_output),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert package_run.returncode == 0, package_run.stderr
    assert "hi-points: 4" in package_run.stdout
    assert "lo-points: 4" in package_run.stdout
    assert "avg-points: 4" in package_run.stdout
    assert "csv:" in package_run.stdout
    assert package_output.exists()
