from __future__ import annotations

import argparse
from pathlib import Path

from coverage_core import (
    Dataset,
    build_matrix_rows,
    convert_distance_to_meters,
    dataset_summary,
    greedy_budgeted_max_cover,
    greedy_full_cover,
    load_dataset,
    write_rows,
    write_qgis_bundle,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "coverage" / "results"


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
        help="Greedy full-cover approximation: choose sites until all reachable demand is covered.",
    )
    _add_dataset_args(full_cover)
    _add_coverage_args(full_cover)
    _add_output_args(full_cover)

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
        help="Write a QGIS-friendly visual bundle with GeoJSON layers and a PyQGIS loader script.",
    )
    _add_dataset_args(qgis_bundle)
    _add_coverage_args(qgis_bundle)
    qgis_bundle.add_argument(
        "--solver",
        choices=["none", "full-cover", "max-cover"],
        default="full-cover",
        help="Optional greedy solver used to flag selected candidates in the bundle.",
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
    rows, uncovered_ids = greedy_full_cover(
        demand.points,
        candidates.points,
        radius_m=radius_m,
        distance_mode=args.distance_mode,
        exclude_self=args.exclude_self,
    )
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


def main(argv: list[str] | None = None) -> int:
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

    parser.error(f"Unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
