from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest

from highpoints.clusters import (
    find_local_maxima,
    load_highgrid_csv,
    write_geopackage,
)


def _write_cluster_csv(path: Path) -> Path:
    path.write_text(
        "\n".join(
            [
                "cell_id,cell_name,point_type,elev_m,lon,lat,x,y",
                "1,U01_V01,hi,10,-77.0,38.0,-76.5,38.5",
                "2,U01_V02,hi,20,-78.0,39.0,-76.25,38.75",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _write_cluster_csv_with_crs(path: Path, crs_authid: str) -> Path:
    path.write_text(
        "\n".join(
            [
                "cell_id,cell_name,point_type,elev_m,lon,lat,x,y,crs_authid",
                f"1,U01_V01,hi,10,-77.0,38.0,100,200,{crs_authid}",
                f"2,U01_V02,hi,20,-78.0,39.0,300,400,{crs_authid}",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_load_highgrid_csv_uses_dem_xy_as_nad83_geometry(tmp_path: Path) -> None:
    csv_path = _write_cluster_csv(tmp_path / "highgrid_out.csv")

    gdf = load_highgrid_csv(csv_path, ["hi"])

    assert gdf.crs.to_epsg() == 4269
    assert gdf.geometry.iloc[0].x == pytest.approx(-76.5)
    assert gdf.geometry.iloc[0].y == pytest.approx(38.5)


def test_load_highgrid_csv_prefers_csv_crs_metadata(tmp_path: Path) -> None:
    csv_path = _write_cluster_csv_with_crs(tmp_path / "highgrid_out.csv", "EPSG:3857")

    gdf = load_highgrid_csv(csv_path, ["hi"])

    assert gdf.crs.to_epsg() == 3857
    assert gdf.geometry.iloc[0].x == pytest.approx(100)
    assert gdf.geometry.iloc[0].y == pytest.approx(200)


def test_load_highgrid_csv_crs_override_wins(tmp_path: Path) -> None:
    csv_path = _write_cluster_csv_with_crs(tmp_path / "highgrid_out.csv", "EPSG:3857")

    gdf = load_highgrid_csv(csv_path, ["hi"], crs="EPSG:4269")

    assert gdf.crs.to_epsg() == 4269


def test_write_geopackage_preserves_nad83_cluster_layers(tmp_path: Path) -> None:
    csv_path = _write_cluster_csv(tmp_path / "highgrid_out.csv")
    gdf = load_highgrid_csv(csv_path, ["hi"])
    labels = np.array([0, 0])
    maxima = find_local_maxima(gdf, labels)
    output_path = tmp_path / "clusters.gpkg"

    write_geopackage(gdf, labels, maxima, output_path)

    clustered = gpd.read_file(output_path, layer="clustered_points")
    maxima_layer = gpd.read_file(output_path, layer="local_maxima")
    assert clustered.crs.to_epsg() == 4269
    assert maxima_layer.crs.to_epsg() == 4269
