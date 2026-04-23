from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pyogrio
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import Point

from highpoints import highgrid as hg

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_test_dem(path: Path) -> Path:
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
        crs="EPSG:3857",
        transform=from_origin(0, 4, 1, 1),
        nodata=-9999.0,
    ) as dataset:
        dataset.write(values, 1)
    return path


def _write_boundary(path: Path, *, east: tuple[float, float] = (4, 4)) -> Path:
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


def test_run_highgrid_finds_expected_peaks_and_layers(tmp_path):
    dem = _write_test_dem(tmp_path / "dem.tif")
    boundary = _write_boundary(tmp_path / "boundary.gpkg")
    output = tmp_path / "highgrid.gpkg"

    result = hg.run_highgrid(
        dem_path=dem,
        boundary_path=boundary,
        output_path=output,
        grid_size=2,
        work_crs="EPSG:3857",
        parallelogram_tolerance_m=0.01,
        overwrite=True,
    )

    assert result.cell_count == 4
    assert result.peak_count == 4
    assert output.exists()
    layers = set(pyogrio.list_layers(output)[:, 0])
    assert layers == {
        "highgrid_cells",
        "highgrid_peaks",
        "highgrid_boundary",
        "highgrid_corners",
    }

    cells = gpd.read_file(output, layer="highgrid_cells")
    assert len(cells) == 4
    expected_elevations = {
        "U01_V01": 4.0,
        "U01_V02": 12.0,
        "U02_V01": 8.0,
        "U02_V02": 16.0,
    }
    for row in cells.itertuples():
        assert row.status == "ok"
        assert row.valid_px == 4
        assert row.peak_elev_m == pytest.approx(expected_elevations[row.cell_name])

    peaks = gpd.read_file(output, layer="highgrid_peaks")
    boundary_layer = gpd.read_file(output, layer="highgrid_boundary")
    corners = gpd.read_file(output, layer="highgrid_corners")
    assert len(peaks) == 4
    assert len(boundary_layer) == 1
    assert list(corners["corner"]) == ["W", "N", "E", "S"]


@pytest.mark.parametrize("value", ["0", "101", "not-an-int"])
def test_parse_grid_size_rejects_invalid_values(value):
    with pytest.raises(argparse.ArgumentTypeError):
        hg.parse_grid_size(value)


def test_missing_boundary_cli_fails_cleanly(tmp_path):
    dem = _write_test_dem(tmp_path / "dem.tif")
    output = tmp_path / "missing_boundary.gpkg"

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "highpoints" / "highgrid.py"),
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
    assert "--boundary" in completed.stderr
    assert "--corners" in completed.stderr


def test_parallelogram_validation_rejects_bad_fourth_corner(tmp_path):
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
    assert "peaks: 4" in package_run.stdout
    assert package_output.exists()
