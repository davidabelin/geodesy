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
    greedy_budgeted_max_cover,
    greedy_full_cover,
    load_dataset,
    networkx_full_cover,
    write_rows,
)
from coverage_los_core import has_dem_backend, length_to_meters, run_los_cover


REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "coverage" / "results"
QGIS_PYTHON = Path.home() / "AppData/Local/Programs/OSGeo4W/bin/python-qgis.bat"


def _parse_workers(value: str) -> int | None:
    """Normalize CLI worker settings before the solver receives them.

    `none` keeps the LOS candidate loop single-threaded, `max` uses the local
    CPU count, and positive integers request an explicit worker count. Keeping
    this validation at the CLI boundary prevents the lower-level solver from
    needing to know about user-facing spellings.
    """
    normalized = value.strip().lower()
    if normalized == "none":
        return None

    cpu_count = os.cpu_count() or 1
    if normalized == "max":
        return cpu_count

    try:
        worker_count = int(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--workers must be 'none', 'max', or an integer from 1 to CPU count."
        ) from exc

    if worker_count < 1 or worker_count > cpu_count:
        raise argparse.ArgumentTypeError(
            f"--workers must be between 1 and {cpu_count}, or use 'none'/'max'."
        )
    return worker_count


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
    """Build the coverage command tree used by `cvr.bat`.

    `cvr.bat` handles QGIS-only commands before this parser runs. Commands kept
    here are pure-Python workflows or workflows that can re-enter OSGeo4W only
    when a DEM backend is unavailable.
    """
    parser = argparse.ArgumentParser(description="Coverage CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

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

    radius_cover = sub.add_parser(
        "radius-cover",
        help="Choose radius-cover candidates, optionally constrained by --budget.",
    )
    _add_dataset_args(radius_cover)
    _add_coverage_args(radius_cover)
    _add_output_args(radius_cover)
    radius_cover.add_argument(
        "--solver",
        choices=["greedy", "networkx"],
        default="greedy",
        help="Solver used when --budget is omitted. Defaults to greedy.",
    )
    radius_cover.add_argument(
        "--budget",
        type=int,
        default=None,
        help="Maximum candidates to pick. When omitted, selects until all reachable demand is covered.",
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
    los_cover.add_argument("--point-height", type=float, default=None, help="Point height offset in --unit.")
    los_cover.add_argument("--point-height-m", type=float, default=None, help="Point height offset in meters.")
    los_cover.add_argument("--sample-step", type=float, default=None, help="LOS terrain sampling step in --unit.")
    los_cover.add_argument("--sample-step-m", type=float, default=None, help="LOS terrain sampling step in meters.")
    los_cover.add_argument("--solver", choices=["greedy", "hybrid", "networkx"], default="hybrid")
    los_cover.add_argument(
        "--workers",
        type=_parse_workers,
        default=None,
        metavar="none|max|N",
        help="Parallel LOS candidate workers: none, max, or an integer from 1 to CPU count. Defaults to none.",
    )

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


def _radius_cover_command(args: argparse.Namespace) -> int:
    """Run the merged radius-cover command.

    The command keeps one radius workflow in the CLI: without `--budget` it
    selects enough candidates to cover all reachable demand, and with `--budget`
    it selects the best candidates within that limit. The output row schema is
    the same for both modes.
    """
    demand, candidates = _load_datasets(args)
    radius_m = convert_distance_to_meters(args.radius, args.unit)
    if args.budget is None and args.solver == "greedy":
        rows, uncovered_ids = greedy_full_cover(
            demand.points,
            candidates.points,
            radius_m=radius_m,
            distance_mode=args.distance_mode,
            exclude_self=args.exclude_self,
        )
        output_label = "Radius-cover output"
    elif args.budget is None and args.solver == "networkx":
        rows, uncovered_ids = networkx_full_cover(
            demand.points,
            candidates.points,
            radius_m=radius_m,
            distance_mode=args.distance_mode,
            exclude_self=args.exclude_self,
        )
        output_label = "Radius-cover output"
    else:
        if args.solver != "greedy":
            raise ValueError("--solver networkx is only available when --budget is omitted.")
        if args.budget < 1:
            raise ValueError("--budget must be at least 1.")
        rows, uncovered_ids = greedy_budgeted_max_cover(
            demand.points,
            candidates.points,
            radius_m=radius_m,
            distance_mode=args.distance_mode,
            budget=args.budget,
            exclude_self=args.exclude_self,
        )
        output_label = "Radius budgeted-cover output"

    output_path = Path(args.output) if args.output else _default_output_path(
        "radius_cover", demand, candidates, args.format
    )
    write_rows(output_path, rows, args.format)
    covered_total = rows[-1]["total_covered_count"] if rows else 0
    _print_output(output_label, output_path, len(rows))
    print(f"Selected candidates: {len(rows)}")
    print(f"Demand points covered: {covered_total}")
    print(f"Uncovered demand count: {len(uncovered_ids)}")
    if uncovered_ids:
        print("Uncovered demand IDs:", ", ".join(uncovered_ids))
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
        point_height_m=point_height_m,
        sample_step_m=sample_step_m,
        solver=args.solver,
        output_crs=args.output_crs,
        output_unit=args.unit,
        dem_unit=args.dem_unit,
        workers=args.workers,
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

    if args.cmd == "matrix":
        return _matrix_command(args)
    if args.cmd == "radius-cover":
        return _radius_cover_command(args)
    if args.cmd == "los-cover":
        return _los_cover_command(args, argv)

    parser.error(f"Unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
