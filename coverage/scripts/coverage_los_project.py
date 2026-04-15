from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any


CURRENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CURRENT_DIR.parents[1]
QGIS_HELPER_DIR = REPO_ROOT / "qgis"
if str(QGIS_HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(QGIS_HELPER_DIR))

from qgis_runtime import init_qgis_app, shutdown_qgis_app

from osgeo import ogr, osr
from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsProject,
    QgsRasterLayer,
    QgsRendererCategory,
    QgsSingleSymbolRenderer,
    QgsVectorLayer,
)


OGR_FIELD_TYPES = {
    "string": ogr.OFTString,
    "int": ogr.OFTInteger,
    "float": ogr.OFTReal,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a QGIS project from los-cover output.")
    parser.add_argument("--input-dir", required=True, help="Directory produced by `los-cover`.")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for the GeoPackage and QGIS project. Defaults under the input dir.",
    )
    parser.add_argument(
        "--dem",
        default=None,
        help="Optional DEM override. Defaults to the DEM path recorded in manifest.json.",
    )
    return parser


def _read_manifest(input_dir: Path) -> dict[str, Any]:
    manifest_path = input_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing manifest: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _read_geojson_features(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return list(payload.get("features", []))


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _coerce_bool(value: str | None) -> int:
    return 1 if str(value).strip() in {"1", "True", "true"} else 0


def _coerce_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _delete_existing(path: Path) -> None:
    driver = ogr.GetDriverByName("GPKG")
    if path.exists():
        driver.DeleteDataSource(str(path))


def _create_layer(
    dataset,
    name: str,
    geometry_type: int,
    fields: list[tuple[str, str]],
):
    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromEPSG(4326)
    layer = dataset.CreateLayer(name, srs=spatial_ref, geom_type=geometry_type)
    for field_name, field_kind in fields:
        layer.CreateField(ogr.FieldDefn(field_name, OGR_FIELD_TYPES[field_kind]))
    return layer


def _set_fields(feature, values: dict[str, Any]) -> None:
    for key, value in values.items():
        if value is None:
            continue
        feature.SetField(key, value)


def _point_geometry(coords: list[float]) -> ogr.Geometry:
    geometry = ogr.Geometry(ogr.wkbPoint25D)
    geometry.AddPoint(coords[0], coords[1], coords[2] if len(coords) >= 3 else 0.0)
    return geometry


def _line_geometry(coords: list[list[float]]) -> ogr.Geometry:
    geometry = ogr.Geometry(ogr.wkbLineString25D)
    for coord in coords:
        geometry.AddPoint(coord[0], coord[1], coord[2] if len(coord) >= 3 else 0.0)
    return geometry


def write_geopackage(
    output_path: Path,
    *,
    point_rows: list[dict[str, Any]],
    selected_segment_features: list[dict[str, Any]],
    coverage_offset_features: list[dict[str, Any]],
) -> Path:
    _delete_existing(output_path)
    driver = ogr.GetDriverByName("GPKG")
    dataset = driver.CreateDataSource(str(output_path))
    if dataset is None:
        raise RuntimeError(f"Failed to create GeoPackage: {output_path}")

    points_layer = _create_layer(
        dataset,
        "points_z",
        ogr.wkbPoint25D,
        [
            ("point_id", "string"),
            ("reachable", "int"),
            ("covered", "int"),
            ("assigned_candidate_id", "string"),
            ("assigned_anchor_id", "string"),
            ("residual_m", "float"),
            ("ground_m", "float"),
            ("absolute_m", "float"),
            ("display_m", "float"),
            ("alt_source", "string"),
        ],
    )
    for row in point_rows:
        feature = ogr.Feature(points_layer.GetLayerDefn())
        feature.SetGeometry(
            _point_geometry(
                [
                    float(row["point_lon"]),
                    float(row["point_lat"]),
                    float(row["display_alt_m"]),
                ]
            )
        )
        _set_fields(
            feature,
            {
                "point_id": row["point_id"],
                "reachable": _coerce_bool(row.get("reachable")),
                "covered": _coerce_bool(row.get("covered")),
                "assigned_candidate_id": row.get("assigned_candidate_id"),
                "assigned_anchor_id": row.get("assigned_anchor_id"),
                "residual_m": _coerce_float(row.get("residual_distance_m")),
                "ground_m": _coerce_float(row.get("ground_alt_m")),
                "absolute_m": _coerce_float(row.get("absolute_alt_m")),
                "display_m": _coerce_float(row.get("display_alt_m")),
                "alt_source": row.get("altitude_source"),
            },
        )
        points_layer.CreateFeature(feature)

    segments_layer = _create_layer(
        dataset,
        "selected_segments_z",
        ogr.wkbLineString25D,
        [
            ("candidate_id", "string"),
            ("anchor_id", "string"),
            ("selected_rank", "int"),
            ("cover_count", "int"),
            ("segment_m", "float"),
            ("surface_m", "float"),
            ("residual_m", "float"),
            ("kind", "string"),
        ],
    )
    anchors_seen: dict[str, dict[str, Any]] = {}
    free_endpoint_rows: list[dict[str, Any]] = []
    for feature_data in selected_segment_features:
        props = feature_data["properties"]
        coords = feature_data["geometry"]["coordinates"]

        feature = ogr.Feature(segments_layer.GetLayerDefn())
        feature.SetGeometry(_line_geometry(coords))
        _set_fields(
            feature,
            {
                "candidate_id": props.get("candidate_id"),
                "anchor_id": props.get("anchor_id"),
                "selected_rank": int(props.get("selected_rank", 0)),
                "cover_count": int(props.get("coverage_count", 0)),
                "segment_m": float(props.get("segment_length_m", 0.0)),
                "surface_m": float(props.get("surface_distance_m", 0.0)),
                "residual_m": float(props.get("residual_sum_m", 0.0)),
                "kind": props.get("generation_kind"),
            },
        )
        segments_layer.CreateFeature(feature)

        anchor_id = str(props.get("anchor_id"))
        anchors_seen.setdefault(
            anchor_id,
            {
                "anchor_id": anchor_id,
                "coords": coords[0],
                "selected_segment_count": 0,
            },
        )
        anchors_seen[anchor_id]["selected_segment_count"] += 1
        free_endpoint_rows.append(
            {
                "candidate_id": str(props.get("candidate_id")),
                "anchor_id": anchor_id,
                "coords": coords[-1],
                "surface_m": float(props.get("surface_distance_m", 0.0)),
            }
        )

    anchors_layer = _create_layer(
        dataset,
        "selected_anchors_z",
        ogr.wkbPoint25D,
        [("anchor_id", "string"), ("segment_count", "int")],
    )
    for anchor_data in anchors_seen.values():
        feature = ogr.Feature(anchors_layer.GetLayerDefn())
        feature.SetGeometry(_point_geometry(anchor_data["coords"]))
        _set_fields(
            feature,
            {
                "anchor_id": anchor_data["anchor_id"],
                "segment_count": int(anchor_data["selected_segment_count"]),
            },
        )
        anchors_layer.CreateFeature(feature)

    endpoints_layer = _create_layer(
        dataset,
        "free_endpoints_z",
        ogr.wkbPoint25D,
        [("candidate_id", "string"), ("anchor_id", "string"), ("surface_m", "float")],
    )
    for endpoint_data in free_endpoint_rows:
        feature = ogr.Feature(endpoints_layer.GetLayerDefn())
        feature.SetGeometry(_point_geometry(endpoint_data["coords"]))
        _set_fields(
            feature,
            {
                "candidate_id": endpoint_data["candidate_id"],
                "anchor_id": endpoint_data["anchor_id"],
                "surface_m": float(endpoint_data["surface_m"]),
            },
        )
        endpoints_layer.CreateFeature(feature)

    offsets_layer = _create_layer(
        dataset,
        "coverage_offsets_z",
        ogr.wkbLineString25D,
        [
            ("point_id", "string"),
            ("candidate_id", "string"),
            ("anchor_id", "string"),
            ("residual_m", "float"),
        ],
    )
    for feature_data in coverage_offset_features:
        props = feature_data["properties"]
        feature = ogr.Feature(offsets_layer.GetLayerDefn())
        feature.SetGeometry(_line_geometry(feature_data["geometry"]["coordinates"]))
        _set_fields(
            feature,
            {
                "point_id": props.get("point_id"),
                "candidate_id": props.get("candidate_id"),
                "anchor_id": props.get("anchor_id"),
                "residual_m": float(props.get("residual_distance_m", 0.0)),
            },
        )
        offsets_layer.CreateFeature(feature)

    dataset = None
    return output_path


def _load_layer(uri: str, name: str) -> QgsVectorLayer:
    layer = QgsVectorLayer(uri, name, "ogr")
    if not layer.isValid():
        raise RuntimeError(f"Failed to load layer: {uri}")
    return layer


def _style_points(layer: QgsVectorLayer) -> None:
    covered_symbol = QgsMarkerSymbol.createSimple(
        {"name": "circle", "color": "38,166,154,220", "outline_color": "255,255,255,200", "size": "3.6"}
    )
    uncovered_symbol = QgsMarkerSymbol.createSimple(
        {"name": "circle", "color": "196,48,43,235", "outline_color": "255,255,255,200", "size": "3.6"}
    )
    renderer = QgsCategorizedSymbolRenderer(
        "covered",
        [
            QgsRendererCategory(1, covered_symbol, "Covered point"),
            QgsRendererCategory(0, uncovered_symbol, "Uncovered point"),
        ],
    )
    layer.setRenderer(renderer)


def _style_segments(layer: QgsVectorLayer) -> None:
    symbol = QgsLineSymbol.createSimple({"line_color": "255,183,3,220", "line_width": "0.9"})
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))


def _style_anchors(layer: QgsVectorLayer) -> None:
    symbol = QgsMarkerSymbol.createSimple(
        {"name": "square", "color": "255,183,3,250", "outline_color": "48,48,48,220", "size": "4.0"}
    )
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))


def _style_endpoints(layer: QgsVectorLayer) -> None:
    symbol = QgsMarkerSymbol.createSimple(
        {"name": "triangle", "color": "66,133,244,240", "outline_color": "32,32,32,200", "size": "3.6"}
    )
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))


def _style_offsets(layer: QgsVectorLayer) -> None:
    symbol = QgsLineSymbol.createSimple(
        {
            "line_color": "120,120,120,180",
            "line_width": "0.35",
            "customdash": "2;1.2",
            "use_custom_dash": "1",
        }
    )
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))


def write_qgis_project(
    *,
    project_path: Path,
    gpkg_path: Path,
    dem_path: Path,
) -> Path:
    app, created = init_qgis_app(gui=False)
    try:
        project = QgsProject.instance()
        project.clear()
        project.setTitle("LOS Segment Cover")

        dem_layer = QgsRasterLayer(str(dem_path), dem_path.stem)
        if not dem_layer.isValid():
            raise RuntimeError(f"Failed to load DEM: {dem_path}")

        layers = {
            "points_z": _load_layer(f"{gpkg_path}|layername=points_z", "points_z"),
            "selected_segments_z": _load_layer(
                f"{gpkg_path}|layername=selected_segments_z",
                "selected_segments_z",
            ),
            "selected_anchors_z": _load_layer(
                f"{gpkg_path}|layername=selected_anchors_z",
                "selected_anchors_z",
            ),
            "free_endpoints_z": _load_layer(
                f"{gpkg_path}|layername=free_endpoints_z",
                "free_endpoints_z",
            ),
            "coverage_offsets_z": _load_layer(
                f"{gpkg_path}|layername=coverage_offsets_z",
                "coverage_offsets_z",
            ),
        }

        _style_points(layers["points_z"])
        _style_segments(layers["selected_segments_z"])
        _style_anchors(layers["selected_anchors_z"])
        _style_endpoints(layers["free_endpoints_z"])
        _style_offsets(layers["coverage_offsets_z"])

        root = project.layerTreeRoot()
        project.addMapLayer(dem_layer, addToLegend=False)
        root.insertLayer(0, dem_layer)

        for name in [
            "coverage_offsets_z",
            "free_endpoints_z",
            "selected_anchors_z",
            "selected_segments_z",
            "points_z",
        ]:
            layer = layers[name]
            project.addMapLayer(layer, addToLegend=False)
            root.insertLayer(0, layer)

        root.findLayer(dem_layer.id()).setItemVisibilityChecked(False)
        root.findLayer(layers["coverage_offsets_z"].id()).setItemVisibilityChecked(False)
        project.setCrs(layers["points_z"].crs())
        project.write(str(project_path))
        return project_path
    finally:
        shutdown_qgis_app(app, created)


def write_readme(path: Path, *, project_path: Path, gpkg_path: Path, dem_path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "# LOS Segment Cover QGIS Project",
                "",
                f"- GeoPackage: `{gpkg_path}`",
                f"- Project: `{project_path}`",
                f"- DEM: `{dem_path}`",
                "",
                "Safe 3D recipe:",
                "1. Open the generated `.qgs` project in QGIS 4.0.0.",
                "2. Open `View -> New 3D Map View` manually.",
                "3. In the 3D view terrain settings, choose the loaded DEM raster as terrain.",
                "4. Keep vertical scale at `1.0`.",
                "5. Turn shadows off.",
                "6. Turn eye-dome lighting off.",
                "7. Turn labels off.",
                "8. Start with the project extent around the LOS result before widening the view.",
                "",
                "Layer notes:",
                "- `points_z`: input points with covered/uncovered status",
                "- `selected_segments_z`: chosen LOS segments",
                "- `selected_anchors_z`: anchors used by the selected segments",
                "- `free_endpoints_z`: sampled free endpoints for the selected segments",
                "- `coverage_offsets_z`: residual point-to-segment offsets",
            ]
        ),
        encoding="utf-8",
    )


def run_project_export(args: argparse.Namespace) -> int:
    input_dir = Path(args.input_dir).resolve()
    manifest = _read_manifest(input_dir)
    output_dir = Path(args.output_dir).resolve() if args.output_dir else input_dir / "qgis_project"
    output_dir.mkdir(parents=True, exist_ok=True)

    selected_segments_path = Path(manifest["files"]["selected_segments"])
    point_status_path = Path(manifest["files"]["point_status"])
    coverage_offsets_path = Path(manifest["files"]["coverage_offsets"])
    dem_path = Path(args.dem).resolve() if args.dem else Path(manifest["dem_path"])

    selected_segments = _read_geojson_features(selected_segments_path)
    coverage_offsets = _read_geojson_features(coverage_offsets_path)
    point_rows = _read_csv_rows(point_status_path)

    gpkg_path = output_dir / "los_segment_cover.gpkg"
    project_path = output_dir / "los_segment_cover.qgs"
    readme_path = output_dir / "README.md"

    write_geopackage(
        gpkg_path,
        point_rows=point_rows,
        selected_segment_features=selected_segments,
        coverage_offset_features=coverage_offsets,
    )
    write_qgis_project(project_path=project_path, gpkg_path=gpkg_path, dem_path=dem_path)
    write_readme(readme_path, project_path=project_path, gpkg_path=gpkg_path, dem_path=dem_path)

    print(f"QGIS project output: {output_dir}")
    print(f"GeoPackage: {gpkg_path}")
    print(f"Project: {project_path}")
    print(f"README: {readme_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "los-project":
        argv = argv[1:]
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_project_export(args)


if __name__ == "__main__":
    raise SystemExit(main())
