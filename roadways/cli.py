from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from ._paths import data_dir, out_dir
from .export_kml import export_geojson_to_kml
from .pipelines import datasets
from .pipelines.dc_centerlines import run_dc_centerlines_pipeline
from .pipelines.dc_roadways import run_dc_roadways_pipeline
from .pipelines.intersections import run_intersections


def _resolve_out_path(path: Path) -> Path:
    return path if path.is_absolute() else (out_dir() / path)


def _default_centerlines_input() -> Path:
    gpkg = datasets.default_centerlines_gpkg()
    if gpkg.exists():
        return gpkg
    return datasets.default_centerlines_shp()


def _default_centerlines_layer(input_path: Path) -> str | None:
    if input_path.suffix.lower() == ".gpkg":
        return "centerlines"
    return None


def _cmd_export_kml(args: argparse.Namespace) -> int:
    export_geojson_to_kml(
        Path(args.input),
        _resolve_out_path(Path(args.output)),
        name_field=str(args.name_field),
        group_by=str(args.group_by) if args.group_by else None,
        color_field=str(args.color_field),
        default_color=str(args.default_color),
        line_width=float(args.line_width),
        doc_name=str(args.doc_name) if args.doc_name else None,
    )
    print(_resolve_out_path(Path(args.output)))
    return 0


def _cmd_dc_centerlines(args: argparse.Namespace) -> int:
    input_path = Path(args.input) if args.input else _default_centerlines_input()
    layer = str(args.layer) if args.layer else _default_centerlines_layer(input_path)

    run_dc_centerlines_pipeline(
        input_path=input_path,
        input_layer=layer,
        output_kml_path=_resolve_out_path(Path(args.output_kml)),
        output_ew_csv_path=_resolve_out_path(Path(args.ew_csv)),
        output_ns_csv_path=_resolve_out_path(Path(args.ns_csv)),
        roadtype=str(args.roadtype) if args.roadtype is not None else None,
        streettype=str(args.streettype or ""),
        style_scheme=str(args.style),
        keep_other=bool(args.keep_other),
        line_width=float(args.line_width),
        snap_gap_m=float(args.snap_gap_m),
        snap_gap_frac=float(args.snap_gap_frac),
    )
    print(_resolve_out_path(Path(args.output_kml)))
    print(_resolve_out_path(Path(args.ew_csv)))
    print(_resolve_out_path(Path(args.ns_csv)))
    return 0


def _cmd_dc_roadways(args: argparse.Namespace) -> int:
    run_dc_roadways_pipeline(
        geojson_path=Path(args.input),
        output_kml_path=_resolve_out_path(Path(args.output_kml)),
        output_ew_csv_path=_resolve_out_path(Path(args.ew_csv)),
        output_ns_csv_path=_resolve_out_path(Path(args.ns_csv)),
        road_type=str(args.road_type),
        name_field=str(args.name_field),
        type_field=str(args.type_field),
        style_scheme=str(args.style),
        keep_other=bool(args.keep_other),
        line_width=float(args.line_width),
        snap_gap_m=float(args.snap_gap_m),
        snap_gap_frac=float(args.snap_gap_frac),
    )
    print(_resolve_out_path(Path(args.output_kml)))
    print(_resolve_out_path(Path(args.ew_csv)))
    print(_resolve_out_path(Path(args.ns_csv)))
    return 0


def _cmd_intersections(args: argparse.Namespace) -> int:
    output_geojson = _resolve_out_path(Path(args.output_geojson))
    output_csv = _resolve_out_path(Path(args.output_csv)) if args.output_csv else None
    output_kml = _resolve_out_path(Path(args.output_kml)) if args.output_kml else None

    run_intersections(
        a_input=Path(args.a_input),
        b_input=Path(args.b_input) if args.b_input else None,
        a_layer=str(args.a_layer) if args.a_layer else None,
        b_layer=str(args.b_layer) if args.b_layer else None,
        a_field=str(args.a_field) if args.a_field else None,
        a_value=str(args.a_value) if args.a_value is not None else None,
        b_field=str(args.b_field) if args.b_field else None,
        b_value=str(args.b_value) if args.b_value is not None else None,
        a_name_field=str(args.a_name_field) if args.a_name_field else None,
        b_name_field=str(args.b_name_field) if args.b_name_field else None,
        derive_full_name=bool(args.derive_full_name),
        full_name_field=str(args.full_name_field),
        group_by=str(args.group_by) if args.group_by else None,
        output_geojson=output_geojson,
        output_csv=output_csv,
        output_kml=output_kml,
        kml_color=str(args.kml_color),
        dedupe_grid_m=float(args.dedupe_grid_m),
    )
    print(output_geojson)
    if output_csv:
        print(output_csv)
    if output_kml:
        print(output_kml)
    return 0


def _resolve_data_out_path(path: Path) -> Path:
    return path if path.is_absolute() else (data_dir() / path)


def _cmd_datasets_centerlines_gpkg(args: argparse.Namespace) -> int:
    out_path = Path(args.output)
    if not out_path.is_absolute() and out_path.parent == Path("."):
        out_path = _resolve_data_out_path(out_path)

    built = datasets.build_centerlines_gpkg(
        input_path=Path(args.input),
        output_path=out_path,
        layer=str(args.layer),
        overwrite=bool(args.overwrite),
    )
    print(built)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m roadways", description="Roadways utilities.")
    sub = p.add_subparsers(dest="cmd", required=True)

    exp = sub.add_parser("export-kml", help="Export a LineString/MultiLineString GeoJSON to KML.")
    exp.add_argument("--input", required=True, help="Input GeoJSON path.")
    exp.add_argument("--output", required=True, help="Output KML path (relative goes under roadways/out/).")
    exp.add_argument("--name-field", default="route", help="Feature property to use for placemark names.")
    exp.add_argument("--group-by", default=None, help="Optional property to group features into folders.")
    exp.add_argument("--color-field", default="color", help="Feature property to read KML color from.")
    exp.add_argument("--default-color", default="ff0000ff", help="Fallback KML AABBGGRR color.")
    exp.add_argument("--line-width", type=float, default=1.0, help="KML line width in pixels.")
    exp.add_argument("--doc-name", default=None, help="Optional KML document name.")
    exp.set_defaults(func=_cmd_export_kml)

    cc = sub.add_parser(
        "dc-centerlines",
        help="Classify DC street centerlines (SHP/GPKG), compute separations, and export.",
    )
    cc.add_argument(
        "--input",
        default=None,
        help="Input path (default: roadways/data/centerlines.gpkg if present, else SHP).",
    )
    cc.add_argument(
        "--layer",
        default=None,
        help="Optional layer name (GPKG default: centerlines).",
    )
    cc.add_argument(
        "--output-kml",
        default="classified_centerlines.kml",
        help="Output KML path (relative goes under roadways/out/).",
    )
    cc.add_argument(
        "--ew-csv",
        default="centerlines_ew_street_distances.csv",
        help="Output EW distances CSV (relative goes under roadways/out/).",
    )
    cc.add_argument(
        "--ns-csv",
        default="centerlines_ns_street_distances.csv",
        help="Output NS distances CSV (relative goes under roadways/out/).",
    )
    cc.add_argument(
        "--roadtype",
        default="Street",
        help="Filter by ROADTYPE (set to empty to disable).",
    )
    cc.add_argument(
        "--streettype",
        default="",
        help="Filter by STREETTYPE (e.g., AVE; accepts Ave/Avenue).",
    )
    cc.add_argument(
        "--style",
        default="dc-street-v1",
        help="Color scheme: dc-street-v1 | none | cmap:<name> | solid:<hex>.",
    )
    cc.add_argument("--keep-other", action="store_true", help="Keep non-EW/NS roads in KML output.")
    cc.add_argument("--line-width", type=float, default=1.0, help="KML line width in pixels.")
    cc.add_argument("--snap-gap-m", type=float, default=0.0, help="Snap segment endpoints within this many meters.")
    cc.add_argument(
        "--snap-gap-frac",
        type=float,
        default=0.0,
        help="Snap segment endpoints within this fraction of total merged length.",
    )
    cc.set_defaults(func=_cmd_dc_centerlines)

    dr = sub.add_parser(
        "dc-roadways",
        help="Legacy pipeline: classify DC roadways from Roadway_SubBlock.geojson.",
    )
    dr.add_argument(
        "--input",
        default=str(data_dir() / "Roadway_SubBlock.geojson"),
        help="Input GeoJSON path.",
    )
    dr.add_argument(
        "--output-kml",
        default="classified_roadways.kml",
        help="Output KML path (relative goes under roadways/out/).",
    )
    dr.add_argument(
        "--ew-csv",
        default="ew_street_distances.csv",
        help="Output EW distances CSV (relative goes under roadways/out/).",
    )
    dr.add_argument(
        "--ns-csv",
        default="ns_street_distances.csv",
        help="Output NS distances CSV (relative goes under roadways/out/).",
    )
    dr.add_argument("--road-type", default="ST", help="Value in --type-field to select.")
    dr.add_argument("--name-field", default="ROUTENAME", help="Field name containing roadway name.")
    dr.add_argument("--type-field", default="STREETTYPE", help="Field name containing roadway type.")
    dr.add_argument(
        "--style",
        default="dc-street-v1",
        help="Color scheme: dc-street-v1 | none | cmap:<name> | solid:<hex>.",
    )
    dr.add_argument("--keep-other", action="store_true", help="Keep non-EW/NS roads in KML output.")
    dr.add_argument("--line-width", type=float, default=1.0, help="KML line width in pixels.")
    dr.add_argument("--snap-gap-m", type=float, default=0.0, help="Snap segment endpoints within this many meters.")
    dr.add_argument(
        "--snap-gap-frac",
        type=float,
        default=0.0,
        help="Snap segment endpoints within this fraction of total merged length.",
    )
    dr.set_defaults(func=_cmd_dc_roadways)

    ix = sub.add_parser("intersections", help="Compute point intersections between two vector layers.")
    ix.add_argument("--a-input", required=True, help="Input dataset A path (SHP/GPKG/etc).")
    ix.add_argument("--b-input", default=None, help="Input dataset B path (defaults to A).")
    ix.add_argument("--a-layer", default=None, help="Optional layer name for dataset A (GPKG).")
    ix.add_argument("--b-layer", default=None, help="Optional layer name for dataset B (GPKG).")
    ix.add_argument("--a-field", default=None, help="Optional filter field for dataset A.")
    ix.add_argument("--a-value", default=None, help="Optional filter value for dataset A.")
    ix.add_argument("--b-field", default=None, help="Optional filter field for dataset B.")
    ix.add_argument("--b-value", default=None, help="Optional filter value for dataset B.")
    ix.add_argument("--a-name-field", default=None, help="Optional label field for dataset A.")
    ix.add_argument("--b-name-field", default=None, help="Optional label field for dataset B.")
    ix.add_argument(
        "--derive-full-name",
        action="store_true",
        help="Build FULL_NAME from ST_NAME+QUADRANT if possible.",
    )
    ix.add_argument("--full-name-field", default="FULL_NAME", help="Name of derived full-name field.")
    ix.add_argument(
        "--group-by",
        default=None,
        help="Optional dissolve field to group before intersecting (reduces duplicate points).",
    )
    ix.add_argument(
        "--output-geojson",
        default="intersections.geojson",
        help="Output GeoJSON path (relative goes under roadways/out/).",
    )
    ix.add_argument(
        "--output-csv",
        default=None,
        help="Optional output CSV path (relative goes under roadways/out/).",
    )
    ix.add_argument(
        "--output-kml",
        default=None,
        help="Optional output KML path (relative goes under roadways/out/).",
    )
    ix.add_argument("--kml-color", default="ff00ffff", help="KML AABBGGRR color for points.")
    ix.add_argument("--dedupe-grid-m", type=float, default=0.5, help="Grid size (meters) to dedupe points.")
    ix.set_defaults(func=_cmd_intersections)

    ds = sub.add_parser("datasets", help="Dataset utilities.")
    ds_sub = ds.add_subparsers(dest="datasets_cmd", required=True)

    gpkg = ds_sub.add_parser("centerlines-gpkg", help="Convert DC Centerlines SHP to a canonical GPKG.")
    gpkg.add_argument("--input", default=str(datasets.default_centerlines_shp()), help="Input SHP path.")
    gpkg.add_argument("--output", default=str(datasets.default_centerlines_gpkg()), help="Output GPKG path.")
    gpkg.add_argument("--layer", default="centerlines", help="Output layer name.")
    gpkg.add_argument("--overwrite", action="store_true", help="Overwrite output if it exists.")
    gpkg.set_defaults(func=_cmd_datasets_centerlines_gpkg)

    return p


def main(argv: Sequence[str] | None = None) -> int:
    p = build_parser()
    ns = p.parse_args(list(argv) if argv is not None else None)
    try:
        return int(ns.func(ns))
    except (ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
