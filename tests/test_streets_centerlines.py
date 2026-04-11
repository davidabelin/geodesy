import csv
import sys

import geopandas as gpd
import pytest
from pyproj import CRS
from shapely.geometry import LineString, MultiLineString

import geodetics


def _row_by_street_id(rows, street, str_id):
    for row in rows:
        if row["street"] == street and int(row["str_id"]) == int(str_id):
            return row
    raise AssertionError(f"street/str_id not found: {street} / {str_id}")


def test_cli_defaults_for_streets_centerlines(monkeypatch):
    captured = {}

    def fake_run(args):
        captured.update(vars(args))

    monkeypatch.setattr(geodetics, "_run_streets_centerlines", fake_run)
    monkeypatch.setattr(sys, "argv", ["geodetics.py", "streets", "centerlines"])

    geodetics.main()

    assert captured["cmd"] == "streets"
    assert captured["streets_cmd"] == "centerlines"
    assert captured["input"] == str(geodetics.STREETS_DEFAULT_INPUT)
    assert captured["output"] == str(geodetics.STREETS_DEFAULT_OUTPUT)
    assert captured["summary_output"] is None
    assert captured["chart_output"] is None
    assert captured["gis_output"] is None
    assert captured["units"] == "feet"
    assert captured["ell"] == "wgs84"


def test_prepare_segments_filters_roadtype_and_uses_stored_length_or_fallback():
    gdf = gpd.GeoDataFrame(
        {
            "ST_NAME": ["East", "Side", "North", "Diag"],
            "OBJECTID": [1, 2, 3, 4],
            "ROADTYPE": ["Street", "Alley", "Street", "Street"],
            "STREETTYPE": ["ST", "ST", "ST", "AVE"],
            "QUADRANT": ["NW", "NW", "NW", "NW"],
            "Shape_Length": [25.0, 25.0, 0.0, 14.142],
        },
        geometry=[
            LineString([(0, 0), (10, 0)]),
            LineString([(0, 1), (10, 1)]),
            LineString([(0, 0), (0, 10)]),
            LineString([(0, 0), (10, 10)]),
        ],
        crs="EPSG:3857",
    )

    segments, spec = geodetics._prepare_street_centerline_segments(gdf, "none")

    assert spec.mode == "projected"
    assert list(segments["identifier"]) == ["East NW | 01", "North NW | 01"]
    assert segments["length_m"].tolist() == [25.0, 10.0]
    assert segments["street_class"].tolist() == ["EW", "NS"]
    assert segments["bearing"].tolist() == [90.0, 0.0]


def test_prepare_segments_explodes_multipart_and_marks_parts():
    gdf = gpd.GeoDataFrame(
        {
            "ST_NAME": ["Split"],
            "OBJECTID": [9],
            "STREETTYPE": ["ST"],
            "QUADRANT": ["NE"],
            "LENGTH": [20.0],
        },
        geometry=[
            MultiLineString(
                [
                    [(0, 0), (10, 0)],
                    [(0, 5), (10, 5)],
                ]
            )
        ],
        crs="EPSG:3857",
    )

    segments, _ = geodetics._prepare_street_centerline_segments(gdf, "wgs84")

    assert list(segments["identifier"]) == ["Split NE | 01", "Split NE | 02"]
    assert segments["length_m"].tolist() == [10.0, 10.0]


def test_identifier_numbering_uses_per_street_sequence_and_expands_when_needed():
    count = 101
    gdf = gpd.GeoDataFrame(
        {
            "ST_NAME": ["Long"] * count,
            "OBJECTID": list(range(count)),
            "STREETTYPE": ["ST"] * count,
            "QUADRANT": ["NW"] * count,
        },
        geometry=[
            LineString([(0, float(i)), (10, float(i))])
            for i in range(count)
        ],
        crs="EPSG:3857",
    )

    segments, _ = geodetics._prepare_street_centerline_segments(gdf, "wgs84")

    assert segments["identifier"].iloc[0] == "Long NW | 001"
    assert segments["identifier"].iloc[99] == "Long NW | 100"
    assert segments["identifier"].iloc[100] == "Long NW | 101"


def test_segment_midpoint_is_local_to_each_segment_geometry():
    spec = geodetics._build_street_measure_spec(CRS.from_epsg(3857), "wgs84")

    straight_mid = geodetics._segment_midpoint(LineString([(0, 0), (10, 0)]), spec)
    poly_mid = geodetics._segment_midpoint(LineString([(0, 0), (6, 0), (6, 8)]), spec)

    assert straight_mid.x == pytest.approx(5.0)
    assert straight_mid.y == pytest.approx(0.0)
    assert poly_mid.x == pytest.approx(6.0)
    assert poly_mid.y == pytest.approx(1.0)


def test_build_rows_ignores_perpendicular_cross_streets_and_leaves_missing_side_blank():
    gdf = gpd.GeoDataFrame(
        {
            "ST_NAME": ["Main", "North", "Cross"],
            "OBJECTID": [1, 2, 3],
            "STREETTYPE": ["ST", "ST", "ST"],
            "QUADRANT": ["NW", "NW", "NW"],
        },
        geometry=[
            LineString([(0, 0), (10, 0)]),
            LineString([(0, 5), (10, 5)]),
            LineString([(5, -10), (5, 10)]),
        ],
        crs="EPSG:3857",
    )

    segments, spec = geodetics._prepare_street_centerline_segments(gdf, "nad83")
    rows = geodetics._build_street_centerline_rows(segments, spec, "m")
    main_row = _row_by_street_id(rows, "Main NW", 1)

    assert main_row["street_1"] == "North NW"
    assert main_row["str_id1"] == 1
    assert main_row["dir_1"] == "N"
    assert main_row["dist_1"] == pytest.approx(5.0)
    assert main_row["street_2"] is None
    assert main_row["str_id2"] is None
    assert main_row["dir_2"] is None
    assert main_row["dist_2"] is None


def test_build_rows_orders_east_then_west_for_ns_segments():
    gdf = gpd.GeoDataFrame(
        {
            "ST_NAME": ["1st", "2nd", "3rd", "Cross"],
            "OBJECTID": [10, 20, 30, 40],
            "STREETTYPE": ["ST", "ST", "ST", "ST"],
            "QUADRANT": ["NE", "NE", "NE", "NE"],
        },
        geometry=[
            LineString([(0, 0), (0, 10)]),
            LineString([(4, 0), (4, 10)]),
            LineString([(-3, 0), (-3, 10)]),
            LineString([(-10, 5), (10, 5)]),
        ],
        crs="EPSG:3857",
    )

    segments, spec = geodetics._prepare_street_centerline_segments(gdf, "none")
    rows = geodetics._build_street_centerline_rows(segments, spec, "m")
    first_row = _row_by_street_id(rows, "1st NE", 1)

    assert first_row["street_1"] == "2nd NE"
    assert first_row["str_id1"] == 1
    assert first_row["dir_1"] == "E"
    assert first_row["dist_1"] == pytest.approx(4.0)
    assert first_row["street_2"] == "3rd NE"
    assert first_row["str_id2"] == 1
    assert first_row["dir_2"] == "W"
    assert first_row["dist_2"] == pytest.approx(3.0)


def test_measure_spec_prefers_input_crs_and_falls_back_to_ellipsoid():
    projected = geodetics._build_street_measure_spec(CRS.from_epsg(26985), "none")
    sphere = geodetics._build_street_measure_spec(None, "none")
    pseudomerc = geodetics._build_street_measure_spec(None, "pseudomerc")

    assert projected.mode == "projected"
    assert projected.crs == CRS.from_epsg(26985)
    assert sphere.mode == "sphere"
    assert pseudomerc.mode == "projected"
    assert pseudomerc.crs == CRS.from_epsg(3857)


def test_analyze_street_centerlines_writes_csv_for_temp_gpkg(tmp_path):
    input_path = tmp_path / "roads.gpkg"
    output_path = tmp_path / "stats.csv"
    summary_path = tmp_path / "stats_by_street.csv"
    chart_path = tmp_path / "stats_by_street.png"
    gis_path = tmp_path / "stats.gpkg"
    gdf = gpd.GeoDataFrame(
        {
            "FULLNAME": ["East", "North"],
            "OBJECTID": [100, 200],
            "TYPE": ["ST", "ST"],
            "QUAD": ["SE", "SE"],
            "Shape_Length": [10.0, 12.0],
        },
        geometry=[
            LineString([(0, 0), (10, 0)]),
            LineString([(0, 5), (10, 5)]),
        ],
        crs="EPSG:3857",
    )
    gdf.to_file(input_path, driver="GPKG")

    rows_written = geodetics.analyze_street_centerlines(
        input_path=input_path,
        output_path=output_path,
        unit="m",
        ellipsoid="none",
    )

    assert rows_written == 2
    with output_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert rows[0]["street"] == "East SE"
    assert rows[0]["str_id"] == "1"
    assert rows[0]["street_class"] == "EW"
    assert rows[0]["length"] == "10.0"
    assert rows[0]["street_1"] == "North SE"
    assert rows[0]["str_id1"] == "1"
    assert rows[0]["dir_1"] == "N"
    assert summary_path.exists()
    assert chart_path.exists()
    assert chart_path.stat().st_size > 0
    assert gis_path.exists()

    with summary_path.open(newline="", encoding="utf-8") as handle:
        summary_rows = list(csv.DictReader(handle))

    assert summary_rows[0]["street"] == "East SE"
    assert summary_rows[0]["segment_count"] == "1"
    assert summary_rows[0]["unit"] == "m"

    bundle_gdf = gpd.read_file(gis_path, layer=geodetics.STREET_CENTERLINE_GPKG_LAYER)
    assert list(bundle_gdf["street"]) == ["East SE", "North SE"]
    assert bundle_gdf.geometry.iloc[0].geom_type == "MultiLineString"


def test_analyze_street_centerlines_supports_shapefile_gis_output(tmp_path):
    input_path = tmp_path / "roads.gpkg"
    output_path = tmp_path / "stats.csv"
    gis_path = tmp_path / "stats_bundle.shp"
    gdf = gpd.GeoDataFrame(
        {
            "FULLNAME": ["East", "North"],
            "OBJECTID": [100, 200],
            "TYPE": ["ST", "ST"],
            "QUAD": ["SE", "SE"],
        },
        geometry=[
            LineString([(0, 0), (10, 0)]),
            LineString([(0, 5), (10, 5)]),
        ],
        crs="EPSG:3857",
    )
    gdf.to_file(input_path, driver="GPKG")

    geodetics.analyze_street_centerlines(
        input_path=input_path,
        output_path=output_path,
        unit="m",
        ellipsoid="none",
        gis_output_path=gis_path,
    )

    assert gis_path.exists()
    bundle_gdf = gpd.read_file(gis_path)
    assert "street" in bundle_gdf.columns
    assert bundle_gdf.geometry.iloc[0].geom_type == "MultiLineString"
