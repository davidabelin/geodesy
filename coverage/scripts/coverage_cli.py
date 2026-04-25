from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from coverage_core import (
    Dataset,
    build_matrix_rows,
    convert_distance_to_meters,
    dataset_summary,
    greedy_budgeted_max_cover,
    greedy_full_cover,
    load_dataset,
    networkx_full_cover,
    write_rows,
    write_qgis_bundle,
)
from coverage_los_core import has_dem_backend, length_to_meters, run_los_cover


REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "coverage" / "results"
QGIS_PYTHON = Path.home() / "AppData/Local/Programs/OSGeo4W/bin/python-qgis.bat"


def _default_output_path(
    command: str,
    demand_dataset: Dataset,
    candidate_dataset: Dataset | None,
    output_format: str,
) -> Path:
    suffix = ".json" if output_format == "json" else ".csv"
    if candidate_dataset is None:
        name = f"{command}__{demand_dataset.path.stem}{suffix}"
    else:
        name = (
            f"{command}__{demand_dataset.path.stem}__"
            f"{candidate_dataset.path.stem}{suffix}"
        )
    return RESULTS_DIR / name


def _add_dataset_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--input",
        required=True,
        help="Demand dataset path. If relative, also tries repo root and repo data/.",
    )
    parser.add_argument(
        "--candidates",
        default=None,
        help="Candidate-site dataset path. Defaults to --input when omitted.",
    )
    parser.add_argument("--id-field", default=None, help="Override point ID field.")
    parser.add_argument("--lat-field", default=None, help="Override CSV latitude field.")
    parser.add_argument("--lon-field", default=None, help="Override CSV longitude field.")
    parser.add_argument("--alt-field", default=None, help="Override altitude field.")


def _add_coverage_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--radius",
        required=True,
        type=float,
        help="Coverage radius in the selected unit.",
    )
    parser.add_argument(
        "--unit",
        choices=["meters", "m", "km", "miles", "mi", "feet", "ft"],
        default="meters",
        help="Unit for --radius.",
    )
    parser.add_argument(
        "--distance-mode",
        choices=["surface", "ecef-3d"],
        default="surface",
        help="surface = ellipsoidal geodesic distance; ecef-3d = 3D Euclidean distance.",
    )
    parser.add_argument(
        "--exclude-self",
        action="store_true",
        help="Exclude same-ID demand/candidate matches.",
    )


def _add_output_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--output",
        default=None,
        help="Explicit output path. Defaults into coverage/results/.",
    )
    parser.add_argument(
        "--format",
        choices=["csv", "json"],
        default="csv",
        help="Output format. Defaults to CSV.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Coverage CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    inspect = sub.add_parser("inspect", help="Inspect an input point dataset.")
    inspect.add_argument(
        "--input",
        required=True,
        help="Dataset path. If relative, also tries repo root and repo data/.",
    )
    inspect.add_argument("--id-field", default=None, help="Override point ID field.")
    inspect.add_argument("--lat-field", default=None, help="Override CSV latitude field.")
    inspect.add_argument("--lon-field", default=None, help="Override CSV longitude field.")
    inspect.add_argument("--alt-field", default=None, help="Override altitude field.")
    _add_output_args(inspect)

    matrix = sub.add_parser(
        "matrix",
        help="Build a demand/candidate coverage matrix using a radius threshold.",
    )
    _add_dataset_args(matrix)
    _add_coverage_args(matrix)
    _add_output_args(matrix)
    matrix.add_argument(
        "--covered-only",
        action="store_true",
        help="Write only rows where coverage is true.",
    )

    full_cover = sub.add_parser(
        "full-cover",
        help="Full-cover approximation: choose sites until all reachable demand is covered.",
    )
    _add_dataset_args(full_cover)
    _add_coverage_args(full_cover)
    _add_output_args(full_cover)
    full_cover.add_argument(
        "--solver",
        choices=["greedy", "networkx"],
        default="greedy",
        help="Full-cover solver. Defaults to greedy.",
    )

    max_cover = sub.add_parser(
        "max-cover",
        help="Greedy budgeted max-cover approximation.",
    )
    _add_dataset_args(max_cover)
    _add_coverage_args(max_cover)
    _add_output_args(max_cover)
    max_cover.add_argument(
        "--budget",
        required=True,
        type=int,
        help="Maximum number of candidate sites to pick.",
    )

    qgis_bundle = sub.add_parser(
        "qgis-bundle",
        help="Write the legacy radius-first QGIS visual bundle with GeoJSON layers and a PyQGIS loader script.",
    )
    _add_dataset_args(qgis_bundle)
    _add_coverage_args(qgis_bundle)
    qgis_bundle.add_argument(
        "--solver",
        choices=["none", "full-cover", "networkx-full-cover", "max-cover"],
        default="full-cover",
        help="Optional solver used to flag selected candidates in the bundle.",
    )
    qgis_bundle.add_argument(
        "--budget",
        type=int,
        default=None,
        help="Candidate budget when --solver=max-cover.",
    )
    qgis_bundle.add_argument(
        "--output-dir",
        default=None,
        help="Bundle directory. Defaults under coverage/results/.",
    )

    los_cover = sub.add_parser(
        "los-cover",
        help="Solve the direct LOS segment-cover problem and write reusable output artifacts.",
    )
    los_cover.add_argument(
        "--input",
        required=True,
        help="Point dataset path. If relative, also tries repo root and repo data/.",
    )
    los_cover.add_argument("--dem", required=True, help="Local DEM raster path.")
    los_cover.add_argument("--output-dir", required=True, help="Output directory for solver artifacts.")
    los_cover.add_argument("--id-field", default=None, help="Override point ID field.")
    los_cover.add_argument("--lat-field", default=None, help="Override CSV latitude field.")
    los_cover.add_argument("--lon-field", default=None, help="Override CSV longitude field.")
    los_cover.add_argument("--alt-field", default=None, help="Override altitude field.")
    los_cover.add_argument(
        "--max-segment-length",
        required=True,
        type=float,
        help="Maximum surface distance for LOS-valid point-to-point candidate segments, in --unit.",
    )
    los_cover.add_argument(
        "--unit",
        choices=["meters", "m", "feet", "ft"],
        default="meters",
        help="Unit for --max-segment-length and unit-aware LOS args. Defaults to meters.",
    )
    los_cover.add_argument(
        "--output-crs",
        default="WGS84",
        help="Output horizontal CRS: WGS84/EPSG:4326 or NAD83/EPSG:4269. Defaults to WGS84.",
    )
    los_cover.add_argument(
        "--dem-unit",
        choices=["meters", "m", "feet", "ft"],
        default="meters",
        help="Vertical unit of DEM cell values before internal conversion. Defaults to meters.",
    )
    los_cover.add_argument("--line-tolerance", type=float, default=None, help="Line-membership tolerance in --unit.")
    los_cover.add_argument("--line-tolerance-m", type=float, default=None, help="Line-membership tolerance in meters.")
    los_cover.add_argument("--anchor-height-m", type=float, default=2.0)
    los_cover.add_argument("--point-height", type=float, default=None, help="Point height offset in --unit.")
    los_cover.add_argument("--point-height-m", type=float, default=None, help="Point height offset in meters.")
    los_cover.add_argument("--endpoint-height-m", type=float, default=2.0)
    los_cover.add_argument("--azimuth-step-deg", type=float, default=10.0)
    los_cover.add_argument("--endpoint-step-m", type=float, default=25.0)
    los_cover.add_argument("--sample-step", type=float, default=None, help="LOS terrain sampling step in --unit.")
    los_cover.add_argument("--sample-step-m", type=float, default=None, help="LOS terrain sampling step in meters.")
    los_cover.add_argument("--solver", choices=["greedy", "hybrid", "networkx"], default="hybrid")

    return parser


def _load_datasets(args: argparse.Namespace) -> tuple[Dataset, Dataset]:
    demand = load_dataset(
        args.input,
        REPO_ROOT,
        id_field=args.id_field,
        lat_field=args.lat_field,
        lon_field=args.lon_field,
        alt_field=args.alt_field,
    )
    candidate_input = args.candidates or args.input
    candidates = load_dataset(
        candidate_input,
        REPO_ROOT,
        id_field=args.id_field,
        lat_field=args.lat_field,
        lon_field=args.lon_field,
        alt_field=args.alt_field,
    )
    return demand, candidates


def _print_output(label: str, path: Path, row_count: int) -> None:
    print(f"{label}: {path}")
    print(f"Rows written: {row_count}")


def _inspect_command(args: argparse.Namespace) -> int:
    dataset = load_dataset(
        args.input,
        REPO_ROOT,
        id_field=args.id_field,
        lat_field=args.lat_field,
        lon_field=args.lon_field,
        alt_field=args.alt_field,
    )
    rows = [dataset_summary(dataset)]
    output_path = Path(args.output) if args.output else _default_output_path(
        "inspect", dataset, None, args.format
    )
    write_rows(output_path, rows, args.format)
    _print_output("Inspect output", output_path, len(rows))
    print(f"Input records: {len(dataset.points)}")
    return 0


def _matrix_command(args: argparse.Namespace) -> int:
    demand, candidates = _load_datasets(args)
    radius_m = convert_distance_to_meters(args.radius, args.unit)
    rows = build_matrix_rows(
        demand.points,
        candidates.points,
        radius_m=radius_m,
        distance_mode=args.distance_mode,
        covered_only=args.covered_only,
        exclude_self=args.exclude_self,
    )
    output_path = Path(args.output) if args.output else _default_output_path(
        "matrix", demand, candidates, args.format
    )
    write_rows(output_path, rows, args.format)
    covered_count = sum(1 for row in rows if row.get("covered"))
    _print_output("Matrix output", output_path, len(rows))
    print(f"Covered pairs: {covered_count}")
    return 0


def _full_cover_command(args: argparse.Namespace) -> int:
    demand, candidates = _load_datasets(args)
    radius_m = convert_distance_to_meters(args.radius, args.unit)
    if args.solver == "greedy":
        rows, uncovered_ids = greedy_full_cover(
            demand.points,
            candidates.points,
            radius_m=radius_m,
            distance_mode=args.distance_mode,
            exclude_self=args.exclude_self,
        )
    elif args.solver == "networkx":
        rows, uncovered_ids = networkx_full_cover(
            demand.points,
            candidates.points,
            radius_m=radius_m,
            distance_mode=args.distance_mode,
            exclude_self=args.exclude_self,
        )
    else:
        raise ValueError(f"Unsupported full-cover solver: {args.solver}")
    output_path = Path(args.output) if args.output else _default_output_path(
        "full_cover", demand, candidates, args.format
    )
    write_rows(output_path, rows, args.format)
    _print_output("Full-cover output", output_path, len(rows))
    print(f"Selected candidates: {len(rows)}")
    print(f"Uncovered demand count: {len(uncovered_ids)}")
    if uncovered_ids:
        print("Uncovered demand IDs:", ", ".join(uncovered_ids))
    return 0


def _max_cover_command(args: argparse.Namespace) -> int:
    demand, candidates = _load_datasets(args)
    radius_m = convert_distance_to_meters(args.radius, args.unit)
    rows, uncovered_ids = greedy_budgeted_max_cover(
        demand.points,
        candidates.points,
        radius_m=radius_m,
        distance_mode=args.distance_mode,
        budget=args.budget,
        exclude_self=args.exclude_self,
    )
    output_path = Path(args.output) if args.output else _default_output_path(
        "max_cover", demand, candidates, args.format
    )
    write_rows(output_path, rows, args.format)
    covered_total = (
        rows[-1]["total_covered_count"] if rows else 0
    )
    _print_output("Max-cover output", output_path, len(rows))
    print(f"Selected candidates: {len(rows)}")
    print(f"Demand points covered: {covered_total}")
    print(f"Uncovered demand count: {len(uncovered_ids)}")
    if uncovered_ids:
        print("Uncovered demand IDs:", ", ".join(uncovered_ids))
    return 0


def _qgis_bundle_command(args: argparse.Namespace) -> int:
    demand, candidates = _load_datasets(args)
    radius_m = convert_distance_to_meters(args.radius, args.unit)
    if args.output_dir:
        bundle_dir = Path(args.output_dir)
    else:
        bundle_dir = (
            RESULTS_DIR
            / f"qgis_bundle__{demand.path.stem}__{candidates.path.stem}"
        )

    manifest = write_qgis_bundle(
        bundle_dir,
        demand_dataset=demand,
        candidate_dataset=candidates,
        radius_m=radius_m,
        distance_mode=args.distance_mode,
        exclude_self=args.exclude_self,
        solver=args.solver,
        budget=args.budget,
    )
    print(f"QGIS bundle output: {bundle_dir}")
    print(f"Covered demand points: {manifest['covered_demand_count']}")
    print(f"Demand points covered by selected candidates: {manifest['selected_covered_demand_count']}")
    print(f"Selected candidates: {manifest['selected_candidate_count']}")
    print(f"Loader script: {manifest['files']['loader_script']}")
    return 0


def _reexec_los_cover_under_qgis(argv: list[str]) -> int:
    if not QGIS_PYTHON.exists():
        raise RuntimeError(
            "DEM access is unavailable in this interpreter and "
            f"`{QGIS_PYTHON}` was not found."
        )

    env = dict(os.environ)
    env["COVERAGE_LOS_REEXEC"] = "1"
    result = subprocess.run(
        [str(QGIS_PYTHON), str(Path(__file__).resolve()), *argv],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return int(result.returncode)


def _los_cover_command(args: argparse.Namespace, argv: list[str]) -> int:
    if not has_dem_backend() and os.environ.get("COVERAGE_LOS_REEXEC") != "1":
        return _reexec_los_cover_under_qgis(argv)

    line_tolerance_m = _resolve_unit_length(
        unit_value=args.line_tolerance,
        meter_value=args.line_tolerance_m,
        default_m=5.0,
        unit=args.unit,
    )
    point_height_m = _resolve_unit_length(
        unit_value=args.point_height,
        meter_value=args.point_height_m,
        default_m=2.0,
        unit=args.unit,
    )
    sample_step_m = _resolve_unit_length(
        unit_value=args.sample_step,
        meter_value=args.sample_step_m,
        default_m=10.0,
        unit=args.unit,
    )
    manifest = run_los_cover(
        input_path=args.input,
        dem_path=args.dem,
        output_dir=args.output_dir,
        repo_root=REPO_ROOT,
        id_field=args.id_field,
        lat_field=args.lat_field,
        lon_field=args.lon_field,
        alt_field=args.alt_field,
        max_segment_length_m=length_to_meters(args.max_segment_length, args.unit),
        line_tolerance_m=line_tolerance_m,
        anchor_height_m=args.anchor_height_m,
        point_height_m=point_height_m,
        endpoint_height_m=args.endpoint_height_m,
        azimuth_step_deg=args.azimuth_step_deg,
        endpoint_step_m=args.endpoint_step_m,
        sample_step_m=sample_step_m,
        solver=args.solver,
        output_crs=args.output_crs,
        output_unit=args.unit,
        dem_unit=args.dem_unit,
    )
    print(f"LOS cover output: {manifest['output_dir']}")
    print(f"Output CRS: {manifest['output_crs']}")
    print(f"Output unit: {manifest['output_unit']}")
    print(f"LOS-valid point pairs: {manifest['pair_segment_count']}")
    print(f"Meaningful 3+ point lines: {manifest['meaningful_line_count']}")
    print(f"Selected lines: {manifest['selected_segment_count']}")
    print(f"Covered points: {manifest['covered_point_count']} / {manifest['point_count']}")
    print(f"Exact refinement: {manifest['selection']['exact_status']}")
    print(f"Selected segments file: {manifest['files']['selected_segments']}")
    return 0


def _resolve_unit_length(
    *,
    unit_value: float | None,
    meter_value: float | None,
    default_m: float,
    unit: str,
) -> float:
    if meter_value is not None:
        return float(meter_value)
    if unit_value is not None:
        return length_to_meters(unit_value, unit)
    return float(default_m)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "inspect":
        return _inspect_command(args)
    if args.cmd == "matrix":
        return _matrix_command(args)
    if args.cmd == "full-cover":
        return _full_cover_command(args)
    if args.cmd == "max-cover":
        return _max_cover_command(args)
    if args.cmd == "qgis-bundle":
        return _qgis_bundle_command(args)
    if args.cmd == "los-cover":
        return _los_cover_command(args, argv)

    parser.error(f"Unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
