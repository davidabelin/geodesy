from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from ._paths import data_dir, out_dir


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
        help="Output dataset path (KML or CSV depending on command).",
    )


def _cmd_export_kml(args: argparse.Namespace) -> int:
    from .export_kml import export_geojson_to_kml

    export_geojson_to_kml(
        geojson_path=args.input,
        kml_path=args.output,
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
        output_kml_path=args.output_kml,
        output_ew_csv_path=args.ew_csv,
        output_ns_csv_path=args.ns_csv,
        road_type=args.road_type,
        name_field=args.name_field,
        type_field=args.type_field,
        style_scheme=args.style,
        keep_other=args.keep_other,
    )
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
        default=2.0,
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
        help="DC roadway pipeline: merge segments, classify EW/NS, style, export KML + CSVs.",
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
        choices=["dc-street-v1", "none"],
        help="Color styling scheme for KML output.",
    )
    dc.add_argument(
        "--keep-other",
        action="store_true",
        help="Keep unclassified roadways (otherwise only EW/NS are output).",
    )
    dc.set_defaults(func=_cmd_dc_roadways)

    return p


def main(argv: Sequence[str] | None = None) -> int:
    p = build_parser()
    args = p.parse_args(list(argv) if argv is not None else None)
    return int(args.func(args))
