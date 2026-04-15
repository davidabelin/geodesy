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
