from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from ._paths import data_dir, out_dir


def _resolve_out_path(path: Path) -> Path:
    return path if path.is_absolute() else (out_dir() / path)


def _add_common_io_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Input dataset path (GeoJSON).",
    )
    p.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output path (relative paths go under roadways/out/).",
    )


def _cmd_export_kml(args: argparse.Namespace) -> int:
    from .export_kml import export_geojson_to_kml

    export_geojson_to_kml(
        geojson_path=args.input,
        kml_path=_resolve_out_path(args.output),
        name_field=args.name_field,
        color_field=args.color_field,
        default_color=args.default_color,
        line_width=args.line_width,
        doc_name=args.doc_name,
        group_by=args.group_by,
    )
    return 0


def _cmd_dc_roadways(args: argparse.Namespace) -> int:
    from .pipelines.dc_roadways import run_dc_roadways_pipeline

    run_dc_roadways_pipeline(
        geojson_path=args.input,
        output_kml_path=_resolve_out_path(args.output_kml),
        output_ew_csv_path=_resolve_out_path(args.ew_csv),
        output_ns_csv_path=_resolve_out_path(args.ns_csv),
        road_type=args.road_type,
        name_field=args.name_field,
        type_field=args.type_field,
        style_scheme=args.style,
        keep_other=args.keep_other,
        line_width=args.line_width,
        snap_gap_m=args.snap_gap_m,
        snap_gap_frac=args.snap_gap_frac,
    )
    return 0


def _cmd_dc_centerlines(args: argparse.Namespace) -> int:
    from .pipelines.dc_centerlines import run_dc_centerlines_pipeline

    input_path = args.input
    if not input_path.exists() and not input_path.is_absolute():
        fallback = data_dir() / "Street_Centerlines_2013.shp"
        if fallback.exists():
            input_path = fallback

    run_dc_centerlines_pipeline(
        input_path=input_path,
        input_layer=args.layer,
        output_kml_path=_resolve_out_path(args.output_kml),
        output_ew_csv_path=_resolve_out_path(args.ew_csv),
        output_ns_csv_path=_resolve_out_path(args.ns_csv),
        roadtype=args.roadtype,
        streettype=args.streettype,
        style_scheme=args.style,
        keep_other=args.keep_other,
        line_width=args.line_width,
        snap_gap_m=args.snap_gap_m,
        snap_gap_frac=args.snap_gap_frac,
    )
    return 0


def _cmd_intersections(args: argparse.Namespace) -> int:
    from .pipelines.intersections import run_intersections

    run_intersections(
        a_input=args.a_input,
        b_input=args.b_input,
        a_layer=args.a_layer,
        b_layer=args.b_layer,
        a_field=args.a_field,
        a_value=args.a_value,
        b_field=args.b_field,
        b_value=args.b_value,
        a_name_field=args.a_name_field,
        b_name_field=args.b_name_field,
        derive_full_name=args.derive_full_name,
        full_name_field=args.full_name_field,
        group_by=args.group_by,
        output_geojson=_resolve_out_path(args.output_geojson),
        output_csv=_resolve_out_path(args.output_csv) if args.output_csv else None,
        output_kml=_resolve_out_path(args.output_kml) if args.output_kml else None,
        kml_color=args.kml_color,
        dedupe_grid_m=args.dedupe_grid_m,
    )
    return 0


def _cmd_centerlines_gpkg(args: argparse.Namespace) -> int:
    from .pipelines.datasets import build_centerlines_gpkg

    out = build_centerlines_gpkg(
        input_path=Path(args.input),
        output_path=Path(args.output),
        layer=args.layer,
        overwrite=bool(args.overwrite),
    )
    print(out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m roadways",
        description="Roadway utilities (GeoJSON -> styled KML, classification, metrics).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    export_kml = sub.add_parser(
        "export-kml",
        help="Export a (LineString/MultiLineString) GeoJSON to styled KML.",
    )
    _add_common_io_args(export_kml)
    export_kml.add_argument(
        "--name-field",
        default="route",
        help="Feature property to use as placemark name (fallback to ROUTENAME).",
    )
    export_kml.add_argument(
        "--group-by",
        default=None,
        help="Feature property to group into KML folders (e.g. route).",
    )
    export_kml.add_argument(
        "--color-field",
        default="color",
        help="Feature property holding color (RGBA tuple string or hex).",
    )
    export_kml.add_argument(
        "--default-color",
        default="ff0000ff",
        help="Fallback KML color in aabbggrr (default opaque red).",
    )
    export_kml.add_argument(
        "--line-width",
        type=float,
        default=1.0,
        help="KML line width.",
    )
    export_kml.add_argument(
        "--doc-name",
        default=None,
        help="Optional KML document name (defaults to input filename).",
    )
    export_kml.set_defaults(func=_cmd_export_kml)

    dc = sub.add_parser(
        "dc-roadways",
        help="DC roadway pipeline (SubBlock GeoJSON): merge segments, classify EW/NS, style, export KML + CSVs.",
    )
    dc.add_argument(
        "--input",
        type=Path,
        default=data_dir() / "Roadway_SubBlock.geojson",
        help="Input GeoJSON path (default: roadways/data/Roadway_SubBlock.geojson).",
    )
    dc.add_argument(
        "--output-kml",
        type=Path,
        default=out_dir() / "classified_roadways.kml",
        help="Output KML path.",
    )
    dc.add_argument(
        "--ew-csv",
        type=Path,
        default=out_dir() / "ew_street_distances.csv",
        help="Output EW distances CSV path.",
    )
    dc.add_argument(
        "--ns-csv",
        type=Path,
        default=out_dir() / "ns_street_distances.csv",
        help="Output NS distances CSV path.",
    )
    dc.add_argument(
        "--road-type",
        default="ST",
        help="Filter STREETTYPE to this value (default: ST).",
    )
    dc.add_argument(
        "--name-field",
        default="ROUTENAME",
        help="Name field to group/label roads (default: ROUTENAME).",
    )
    dc.add_argument(
        "--type-field",
        default="STREETTYPE",
        help="Road type field to filter (default: STREETTYPE).",
    )
    dc.add_argument(
        "--style",
        default="dc-street-v1",
        help="Color scheme: dc-street-v1 | none | cmap:<matplotlib_cmap> | solid:<kml_hex>.",
    )
    dc.add_argument(
        "--keep-other",
        action="store_true",
        help="Keep unclassified roadways (otherwise only EW/NS are output).",
    )
    dc.add_argument(
        "--line-width",
        type=float,
        default=1.0,
        help="KML line width.",
    )
    dc.add_argument(
        "--snap-gap-m",
        type=float,
        default=0.0,
        help="Optional: connect nearby segment endpoints within this distance (meters). 0 disables.",
    )
    dc.add_argument(
        "--snap-gap-frac",
        type=float,
        default=0.0,
        help="Optional: connect gaps <= (this * total road length). 0 disables.",
    )
    dc.set_defaults(func=_cmd_dc_roadways)

    cc = sub.add_parser(
        "dc-centerlines",
        help="DC centerlines pipeline (Shapefile): merge segments, classify EW/NS, style, export KML + CSVs.",
    )
    default_centerlines_gpkg = data_dir() / "centerlines.gpkg"
    default_centerlines_shp = data_dir() / "DC_Street_Centerlines/Street_Centerlines_2013.shp"
    cc.add_argument(
        "--input",
        type=Path,
        default=default_centerlines_gpkg if default_centerlines_gpkg.exists() else default_centerlines_shp,
        help="Input vector path (GPKG/SHP). Relative paths resolved from CWD.",
    )
    cc.add_argument(
        "--layer",
        default="centerlines" if default_centerlines_gpkg.exists() else None,
        help="Optional: layer name when reading a multi-layer dataset (e.g. GPKG).",
    )
    cc.add_argument(
        "--output-kml",
        type=Path,
        default=out_dir() / "classified_centerlines.kml",
        help="Output KML path.",
    )
    cc.add_argument(
        "--ew-csv",
        type=Path,
        default=out_dir() / "centerlines_ew_street_distances.csv",
        help="Output EW distances CSV path.",
    )
    cc.add_argument(
        "--ns-csv",
        type=Path,
        default=out_dir() / "centerlines_ns_street_distances.csv",
        help="Output NS distances CSV path.",
    )
    cc.add_argument(
        "--roadtype",
        default="Street",
        help="Filter ROADTYPE to this value (default: Street). Use '' to disable.",
    )
    cc.add_argument(
        "--streettype",
        default="",
        help="Optional: filter STREETTYPE (e.g. AVE, ST, RD). Empty disables.",
    )
    cc.add_argument(
        "--style",
        default="dc-street-v1",
        help="Color scheme: dc-street-v1 | none | cmap:<matplotlib_cmap> | solid:<kml_hex>.",
    )
    cc.add_argument(
        "--keep-other",
        action="store_true",
        help="Keep unclassified roadways (otherwise only EW/NS are output).",
    )
    cc.add_argument(
        "--line-width",
        type=float,
        default=1.0,
        help="KML line width.",
    )
    cc.add_argument(
        "--snap-gap-m",
        type=float,
        default=0.0,
        help="Optional: connect nearby segment endpoints within this distance (meters). 0 disables.",
    )
    cc.add_argument(
        "--snap-gap-frac",
        type=float,
        default=0.0,
        help="Optional: connect gaps <= (this * total road length). 0 disables.",
    )
    cc.set_defaults(func=_cmd_dc_centerlines)

    ix = sub.add_parser(
        "intersections",
        help="Compute point intersections between two vector layers (or two filters from one layer).",
    )
    ix.add_argument("--a-input", type=Path, required=True, help="A layer input path (shp/geojson/...)")
    ix.add_argument("--b-input", type=Path, default=None, help="Optional B input path (defaults to A).")
    ix.add_argument("--a-layer", default=None, help="Optional layer name for A (e.g. for GPKG).")
    ix.add_argument("--b-layer", default=None, help="Optional layer name for B (e.g. for GPKG).")
    ix.add_argument("--a-field", default=None, help="Optional: A attribute field to filter by equality.")
    ix.add_argument("--a-value", default=None, help="Optional: A attribute value for --a-field.")
    ix.add_argument("--b-field", default=None, help="Optional: B attribute field to filter by equality.")
    ix.add_argument("--b-value", default=None, help="Optional: B attribute value for --b-field.")
    ix.add_argument(
        "--a-name-field",
        default=None,
        help="Optional: field to label A features (defaults to --group-by if set).",
    )
    ix.add_argument(
        "--b-name-field",
        default=None,
        help="Optional: field to label B features (defaults to --group-by if set).",
    )
    ix.add_argument(
        "--derive-full-name",
        action="store_true",
        help="If inputs have ST_NAME/QUADRANT, derive FULL_NAME for grouping/labels.",
    )
    ix.add_argument(
        "--full-name-field",
        default="FULL_NAME",
        help="Field name to use when deriving FULL_NAME (default: FULL_NAME).",
    )
    ix.add_argument(
        "--group-by",
        default=None,
        help="Optional dissolve field before intersecting (reduces duplicates).",
    )
    ix.add_argument(
        "--output-geojson",
        type=Path,
        default=Path("intersections.geojson"),
        help="Output GeoJSON path (relative goes under roadways/out/).",
    )
    ix.add_argument(
        "--output-csv",
        type=Path,
        default=None,
        help="Optional output CSV path (with lon/lat).",
    )
    ix.add_argument(
        "--output-kml",
        type=Path,
        default=None,
        help="Optional output KML path.",
    )
    ix.add_argument(
        "--kml-color",
        default="ff00ffff",
        help="KML color for points (aabbggrr), default yellow.",
    )
    ix.add_argument(
        "--dedupe-grid-m",
        type=float,
        default=0.5,
        help="Dedupe intersection points onto this grid size in working CRS units (default 0.5).",
    )
    ix.set_defaults(func=_cmd_intersections)

    ds = sub.add_parser("datasets", help="Dataset management utilities.")
    ds_sub = ds.add_subparsers(dest="ds_cmd", required=True)

    gpkg = ds_sub.add_parser(
        "centerlines-gpkg",
        help="Build roadways/data/centerlines.gpkg from the centerlines Shapefile.",
    )
    gpkg.add_argument(
        "--input",
        type=str,
        default=str(data_dir() / "DC_Street_Centerlines/Street_Centerlines_2013.shp"),
        help="Input Shapefile path.",
    )
    gpkg.add_argument(
        "--output",
        type=str,
        default=str(data_dir() / "centerlines.gpkg"),
        help="Output GeoPackage path.",
    )
    gpkg.add_argument("--layer", default="centerlines", help="Output layer name.")
    gpkg.add_argument("--overwrite", action="store_true", help="Overwrite existing output.")
    gpkg.set_defaults(func=_cmd_centerlines_gpkg)

    return p


def main(argv: Sequence[str] | None = None) -> int:
    p = build_parser()
    args = p.parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except (ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
