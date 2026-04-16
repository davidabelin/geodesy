import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = REPO_ROOT / "coverage" / "scripts" / "coverage_cli.py"
CVR_BAT = REPO_ROOT / "cvr.bat"
QGIS_PYTHON = Path.home() / "AppData/Local/Programs/OSGeo4W/bin/python-qgis.bat"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )


def run_los_cover(tmp_path: Path) -> Path:
    output_dir = tmp_path / "los_cover"
    result = run_cli(
        "los-cover",
        "--input",
        "data\\refpnts.csv",
        "--dem",
        "data\\tif\\dc_dem.tif",
        "--max-segment-length",
        "100",
        "--azimuth-step-deg",
        "90",
        "--endpoint-step-m",
        "50",
        "--sample-step-m",
        "25",
        "--output-dir",
        str(output_dir),
    )
    assert result.returncode == 0, result.stderr
    return output_dir


def test_inspect_csv_writes_summary(tmp_path: Path) -> None:
    input_csv = tmp_path / "points.csv"
    input_csv.write_text(
        "LOC,LAT,LON\nA,38.0,-77.0\nB,38.001,-77.0\n",
        encoding="utf-8",
    )
    output_csv = tmp_path / "inspect.csv"

    result = run_cli("inspect", "--input", str(input_csv), "--output", str(output_csv))

    assert result.returncode == 0, result.stderr
    assert output_csv.exists()

    with output_csv.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    assert len(rows) == 1
    assert rows[0]["record_count"] == "2"
    assert rows[0]["id_field"] == "LOC"
    assert rows[0]["lat_field"] == "LAT"
    assert rows[0]["lon_field"] == "LON"


def test_matrix_supports_csv_demands_and_geojson_candidates(tmp_path: Path) -> None:
    demand_csv = tmp_path / "demands.csv"
    demand_csv.write_text(
        "LOC,LAT,LON\nA,38.0,-77.0\nB,38.001,-77.0\nC,38.01,-77.0\n",
        encoding="utf-8",
    )
    candidate_geojson = tmp_path / "candidates.geojson"
    candidate_geojson.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [-77.0, 38.0]},
                        "properties": {"LOC": "S1"},
                    },
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [-77.0, 38.01]},
                        "properties": {"LOC": "S2"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    output_csv = tmp_path / "matrix.csv"

    result = run_cli(
        "matrix",
        "--input",
        str(demand_csv),
        "--candidates",
        str(candidate_geojson),
        "--radius",
        "200",
        "--unit",
        "meters",
        "--covered-only",
        "--output",
        str(output_csv),
    )

    assert result.returncode == 0, result.stderr
    with output_csv.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    covered_pairs = {(row["demand_id"], row["candidate_id"]) for row in rows}
    assert covered_pairs == {("A", "S1"), ("B", "S1"), ("C", "S2")}


def test_greedy_cover_commands_select_expected_sites(tmp_path: Path) -> None:
    demand_csv = tmp_path / "demands.csv"
    demand_csv.write_text(
        "LOC,LAT,LON\nA,38.0,-77.0\nB,38.001,-77.0\nC,38.01,-77.0\n",
        encoding="utf-8",
    )
    candidate_csv = tmp_path / "candidates.csv"
    candidate_csv.write_text(
        "LOC,LAT,LON\nS1,38.0,-77.0\nS2,38.01,-77.0\n",
        encoding="utf-8",
    )

    full_output = tmp_path / "full.csv"
    full_result = run_cli(
        "full-cover",
        "--input",
        str(demand_csv),
        "--candidates",
        str(candidate_csv),
        "--radius",
        "200",
        "--unit",
        "meters",
        "--output",
        str(full_output),
    )

    assert full_result.returncode == 0, full_result.stderr
    with full_output.open("r", encoding="utf-8", newline="") as fh:
        full_rows = list(csv.DictReader(fh))

    assert [row["candidate_id"] for row in full_rows] == ["S1", "S2"]
    assert full_rows[-1]["remaining_uncovered_count"] == "0"

    max_output = tmp_path / "max.csv"
    max_result = run_cli(
        "max-cover",
        "--input",
        str(demand_csv),
        "--candidates",
        str(candidate_csv),
        "--radius",
        "200",
        "--unit",
        "meters",
        "--budget",
        "1",
        "--output",
        str(max_output),
    )

    assert max_result.returncode == 0, max_result.stderr
    with max_output.open("r", encoding="utf-8", newline="") as fh:
        max_rows = list(csv.DictReader(fh))

    assert len(max_rows) == 1
    assert max_rows[0]["candidate_id"] == "S1"
    assert max_rows[0]["remaining_uncovered_count"] == "1"


def test_qgis_bundle_writes_visual_layers_and_manifest(tmp_path: Path) -> None:
    demand_csv = tmp_path / "demands.csv"
    demand_csv.write_text(
        "LOC,LAT,LON\nA,38.0,-77.0\nB,38.001,-77.0\nC,38.01,-77.0\n",
        encoding="utf-8",
    )
    candidate_csv = tmp_path / "candidates.csv"
    candidate_csv.write_text(
        "LOC,LAT,LON\nS1,38.0,-77.0\nS2,38.01,-77.0\n",
        encoding="utf-8",
    )
    bundle_dir = tmp_path / "bundle"

    result = run_cli(
        "qgis-bundle",
        "--input",
        str(demand_csv),
        "--candidates",
        str(candidate_csv),
        "--radius",
        "200",
        "--unit",
        "meters",
        "--solver",
        "max-cover",
        "--budget",
        "1",
        "--output-dir",
        str(bundle_dir),
    )

    assert result.returncode == 0, result.stderr

    manifest = json.loads((bundle_dir / "bundle_manifest.json").read_text(encoding="utf-8"))
    assert manifest["covered_demand_count"] == 3
    assert manifest["selected_covered_demand_count"] == 2
    assert manifest["selected_candidate_count"] == 1

    demands = json.loads((bundle_dir / "demands.geojson").read_text(encoding="utf-8"))
    candidates = json.loads((bundle_dir / "candidates.geojson").read_text(encoding="utf-8"))
    links = json.loads((bundle_dir / "coverage_links.geojson").read_text(encoding="utf-8"))
    zones = json.loads((bundle_dir / "coverage_zones.geojson").read_text(encoding="utf-8"))
    loader = (bundle_dir / "load_bundle_qgis.py").read_text(encoding="utf-8")

    assert len(demands["features"]) == 3
    assert len(candidates["features"]) == 2
    assert len(links["features"]) == 3
    assert len(zones["features"]) == 2
    assert "coverage_zones" in loader
    assert "style_demands" in loader


@pytest.mark.skipif(not QGIS_PYTHON.exists(), reason="QGIS python runtime not installed")
def test_los_bundle_writes_qgis_ready_outputs(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "los_bundle"
    result = subprocess.run(
        [
            "cmd",
            "/c",
            str(CVR_BAT),
            "los-bundle",
            "--input",
            "data\\refpnts.csv",
            "--candidates",
            "data\\map_refpnts.geojson",
            "--dem",
            "data\\tif\\dc_dem.tif",
            "--radius",
            "250",
            "--unit",
            "meters",
            "--output-dir",
            str(bundle_dir),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )

    assert result.returncode == 0, result.stderr

    manifest = json.loads((bundle_dir / "bundle_manifest.json").read_text(encoding="utf-8"))
    assert manifest["los_pair_count"] >= 1
    assert manifest["visible_los_count"] >= 1
    assert (bundle_dir / "demands_3d.geojson").exists()
    assert (bundle_dir / "candidates_3d.geojson").exists()
    assert (bundle_dir / "terrain_traces_3d.geojson").exists()
    assert (bundle_dir / "los_lines_3d.geojson").exists()
    assert (bundle_dir / "load_los_bundle_qgis.py").exists()


@pytest.mark.skipif(not QGIS_PYTHON.exists(), reason="QGIS python runtime not installed")
def test_los_cover_writes_solver_artifacts(tmp_path: Path) -> None:
    output_dir = run_los_cover(tmp_path)

    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    point_status_rows = list(
        csv.DictReader((output_dir / "point_status.csv").open("r", encoding="utf-8", newline=""))
    )
    candidate_rows = list(
        csv.DictReader((output_dir / "candidate_summary.csv").open("r", encoding="utf-8", newline=""))
    )
    pair_rows = list(
        csv.DictReader((output_dir / "pair_summary.csv").open("r", encoding="utf-8", newline=""))
    )

    assert manifest["point_count"] == 17
    assert manifest["pair_segment_count"] >= 1
    assert manifest["line_family_count"] >= 1
    assert manifest["meaningful_line_count"] >= 0
    assert manifest["selected_meaningful_line_count"] >= 0
    assert (output_dir / "pair_segments.geojson").exists()
    assert (output_dir / "meaningful_lines.geojson").exists()
    assert (output_dir / "selected_segments.geojson").exists()
    assert (output_dir / "coverage_offsets.geojson").exists()
    assert (output_dir / "README.md").exists()
    assert len(point_status_rows) == 17
    assert len(candidate_rows) >= manifest["selected_segment_count"]
    assert len(pair_rows) >= len(candidate_rows)
    assert {"covered", "reachable", "assigned_candidate_id"}.issubset(point_status_rows[0].keys())
    assert all(not candidate_id.endswith("__self") for candidate_id in manifest["selected_candidate_ids"])


@pytest.mark.skipif(not QGIS_PYTHON.exists(), reason="QGIS python runtime not installed")
def test_los_cover_supports_nad83_and_feet_output(tmp_path: Path) -> None:
    output_dir = tmp_path / "los_cover_nad83_ft"
    result = run_cli(
        "los-cover",
        "--input",
        "data\\refpnts.csv",
        "--dem",
        "data\\tif\\dc_dem.tif",
        "--max-segment-length",
        "300",
        "--unit",
        "feet",
        "--line-tolerance",
        "15",
        "--point-height",
        "6",
        "--sample-step",
        "75",
        "--output-crs",
        "NAD83",
        "--output-dir",
        str(output_dir),
    )

    assert result.returncode == 0, result.stderr
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    candidate_rows = list(
        csv.DictReader((output_dir / "candidate_summary.csv").open("r", encoding="utf-8", newline=""))
    )
    pair_geojson = json.loads((output_dir / "pair_segments.geojson").read_text(encoding="utf-8"))

    assert manifest["output_crs"] == "EPSG:4269"
    assert manifest["output_unit"] == "feet"
    assert manifest["config"]["max_segment_length_ft"] == 300.0
    assert manifest["config"]["line_tolerance_ft"] == 15.0
    assert pair_geojson["crs"]["properties"]["name"] == "EPSG:4269"
    assert "surface_distance_ft" in candidate_rows[0]
    assert "surface_distance_m" in candidate_rows[0]


@pytest.mark.skipif(not QGIS_PYTHON.exists(), reason="QGIS python runtime not installed")
def test_los_project_writes_geopackage_and_project(tmp_path: Path) -> None:
    input_dir = run_los_cover(tmp_path)
    output_dir = tmp_path / "qgis_project"
    result = subprocess.run(
        [
            "cmd",
            "/c",
            str(CVR_BAT),
            "los-project",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (output_dir / "los_segment_cover.gpkg").exists()
    assert (output_dir / "los_segment_cover.qgs").exists()
    assert (output_dir / "README.md").exists()
    project_text = (output_dir / "los_segment_cover.qgs").read_text(encoding="utf-8")
    assert "pair_segments_z" in project_text
    assert "meaningful_lines_z" in project_text
    assert "selected_lines_z" in project_text
