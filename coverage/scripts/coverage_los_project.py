from __future__ import annotations

import argparse
import csv
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any


CURRENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CURRENT_DIR.parents[1]
QGIS_HELPER_DIR = REPO_ROOT / "qgis"
if str(QGIS_HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(QGIS_HELPER_DIR))

from qgis_runtime import init_qgis_app, shutdown_qgis_app

from osgeo import gdal, ogr, osr
from qgis.PyQt.QtCore import qInstallMessageHandler
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

gdal.UseExceptions()
ogr.UseExceptions()


OGR_FIELD_TYPES = {
    "string": ogr.OFTString,
    "int": ogr.OFTInteger,
    "float": ogr.OFTReal,
}
STYLE_DIR = REPO_ROOT / "coverage"
LAYER_STYLE_FILES = {
    "points_z": STYLE_DIR / "los_cover_points.qml",
    "pair_segments_z": STYLE_DIR / "los_cover_lines.qml",
    "meaningful_lines_z": STYLE_DIR / "meaningful_lines.qml",
    "selected_lines_z": STYLE_DIR / "selected_lines.qml",
}


@contextmanager
def _suppress_known_qt_noise():
    """Suppress a narrow Qt warning emitted while QGIS serializes the project.

    QGIS 4.0.1 currently prints `QMetaEnum::keysToValue: empty keys string.`
    during this headless export path. The warning is noisy but does not indicate
    a failed project write, so the command filters only that exact message and
    leaves other Qt messages visible.
    """

    previous_handler = None

    def handler(mode, context, message):  # noqa: ANN001 - Qt callback signature
        if message == "QMetaEnum::keysToValue: empty keys string.":
            return
        if previous_handler is not None:
            previous_handler(mode, context, message)
            return
        print(message, file=sys.stderr)

    previous_handler = qInstallMessageHandler(handler)
    try:
        yield
    finally:
        qInstallMessageHandler(previous_handler)


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
    *,
    epsg: int,
):
    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromEPSG(int(epsg))
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
    pair_segment_features: list[dict[str, Any]],
    meaningful_line_features: list[dict[str, Any]],
    selected_line_features: list[dict[str, Any]],
    coverage_offset_features: list[dict[str, Any]],
    output_epsg: int = 4326,
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
            ("assigned_endpoint_id", "string"),
            ("residual_m", "float"),
            ("residual_ft", "float"),
            ("ground_m", "float"),
            ("ground_ft", "float"),
            ("absolute_m", "float"),
            ("absolute_ft", "float"),
            ("display_m", "float"),
            ("display_ft", "float"),
            ("alt_source", "string"),
        ],
        epsg=output_epsg,
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
                "assigned_endpoint_id": row.get("assigned_endpoint_id"),
                "residual_m": _coerce_float(row.get("residual_distance_m")),
                "residual_ft": _coerce_float(row.get("residual_distance_ft")),
                "ground_m": _coerce_float(row.get("ground_alt_m")),
                "ground_ft": _coerce_float(row.get("ground_alt_ft")),
                "absolute_m": _coerce_float(row.get("absolute_alt_m")),
                "absolute_ft": _coerce_float(row.get("absolute_alt_ft")),
                "display_m": _coerce_float(row.get("display_alt_m")),
                "display_ft": _coerce_float(row.get("display_alt_ft")),
                "alt_source": row.get("altitude_source"),
            },
        )
        points_layer.CreateFeature(feature)

    line_layer_fields = [
        ("candidate_id", "string"),
        ("anchor_id", "string"),
        ("endpoint_id", "string"),
        ("selected", "int"),
        ("selected_rank", "int"),
        ("cover_count", "int"),
        ("meaningful", "int"),
        ("segment_m", "float"),
        ("segment_ft", "float"),
        ("surface_m", "float"),
        ("surface_ft", "float"),
        ("residual_m", "float"),
        ("residual_ft", "float"),
        ("residual_mean", "float"),
        ("residual_mean_ft", "float"),
        ("kind", "string"),
        ("covered_ids", "string"),
    ]

    def _write_line_layer(layer_name: str, features: list[dict[str, Any]]) -> None:
        layer = _create_layer(
            dataset,
            layer_name,
            ogr.wkbLineString25D,
            line_layer_fields,
            epsg=output_epsg,
        )
        for feature_data in features:
            props = feature_data["properties"]
            feature = ogr.Feature(layer.GetLayerDefn())
            feature.SetGeometry(_line_geometry(feature_data["geometry"]["coordinates"]))
            _set_fields(
                feature,
                {
                    "candidate_id": props.get("candidate_id"),
                    "anchor_id": props.get("anchor_id"),
                    "endpoint_id": props.get("endpoint_id"),
                    "selected": int(props.get("selected", 0)),
                    "selected_rank": int(props.get("selected_rank", 0)),
                    "cover_count": int(props.get("coverage_count", 0)),
                    "meaningful": int(props.get("meaningful", 0)),
                    "segment_m": float(props.get("segment_length_m", 0.0)),
                    "segment_ft": _coerce_float(props.get("segment_length_ft")),
                    "surface_m": float(props.get("surface_distance_m", 0.0)),
                    "surface_ft": _coerce_float(props.get("surface_distance_ft")),
                    "residual_m": float(props.get("residual_sum_m", 0.0)),
                    "residual_ft": _coerce_float(props.get("residual_sum_ft")),
                    "residual_mean": float(props.get("residual_mean_m", 0.0)),
                    "residual_mean_ft": _coerce_float(props.get("residual_mean_ft")),
                    "kind": props.get("generation_kind"),
                    "covered_ids": props.get("covered_point_ids"),
                },
            )
            layer.CreateFeature(feature)

    _write_line_layer("pair_segments_z", pair_segment_features)
    _write_line_layer("meaningful_lines_z", meaningful_line_features)
    _write_line_layer("selected_lines_z", selected_line_features)

    offsets_layer = _create_layer(
        dataset,
        "coverage_offsets_z",
        ogr.wkbLineString25D,
        [
            ("point_id", "string"),
            ("candidate_id", "string"),
            ("anchor_id", "string"),
            ("endpoint_id", "string"),
            ("residual_m", "float"),
            ("residual_ft", "float"),
        ],
        epsg=output_epsg,
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
                "endpoint_id": props.get("endpoint_id"),
                "residual_m": float(props.get("residual_distance_m", 0.0)),
                "residual_ft": _coerce_float(props.get("residual_distance_ft")),
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
    symbol = QgsLineSymbol.createSimple({"line_color": "255,183,3,220", "line_width": "1.0"})
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))


def _style_pair_segments(layer: QgsVectorLayer) -> None:
    symbol = QgsLineSymbol.createSimple({"line_color": "150,150,150,150", "line_width": "0.35"})
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))


def _style_meaningful_lines(layer: QgsVectorLayer) -> None:
    symbol = QgsLineSymbol.createSimple({"line_color": "230,126,34,220", "line_width": "0.8"})
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


def _apply_style_file(layer: QgsVectorLayer, style_path: Path) -> bool:
    """Apply a repository QML style if it exists."""
    if not style_path.exists():
        return False
    layer.loadNamedStyle(str(style_path))
    layer.triggerRepaint()
    return True


def _style_project_layer(layer_name: str, layer: QgsVectorLayer) -> None:
    """Style one exported layer, preferring hand-tuned QML files."""
    style_path = LAYER_STYLE_FILES.get(layer_name)
    if style_path is not None and _apply_style_file(layer, style_path):
        return

    if layer_name == "points_z":
        _style_points(layer)
    elif layer_name == "pair_segments_z":
        _style_pair_segments(layer)
    elif layer_name == "meaningful_lines_z":
        _style_meaningful_lines(layer)
    elif layer_name == "selected_lines_z":
        _style_segments(layer)
    elif layer_name == "coverage_offsets_z":
        _style_offsets(layer)


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
            "pair_segments_z": _load_layer(
                f"{gpkg_path}|layername=pair_segments_z",
                "pair_segments_z",
            ),
            "meaningful_lines_z": _load_layer(
                f"{gpkg_path}|layername=meaningful_lines_z",
                "meaningful_lines_z",
            ),
            "selected_lines_z": _load_layer(
                f"{gpkg_path}|layername=selected_lines_z",
                "selected_lines_z",
            ),
            "coverage_offsets_z": _load_layer(
                f"{gpkg_path}|layername=coverage_offsets_z",
                "coverage_offsets_z",
            ),
        }

        for layer_name, layer in layers.items():
            _style_project_layer(layer_name, layer)

        root = project.layerTreeRoot()
        project.addMapLayer(dem_layer, addToLegend=False)
        root.insertLayer(0, dem_layer)

        for name in [
            "coverage_offsets_z",
            "pair_segments_z",
            "meaningful_lines_z",
            "selected_lines_z",
            "points_z",
        ]:
            layer = layers[name]
            project.addMapLayer(layer, addToLegend=False)
            root.insertLayer(0, layer)

        root.findLayer(dem_layer.id()).setItemVisibilityChecked(False)
        root.findLayer(layers["coverage_offsets_z"].id()).setItemVisibilityChecked(False)
        root.findLayer(layers["pair_segments_z"].id()).setItemVisibilityChecked(False)
        project.setCrs(layers["points_z"].crs())
        with _suppress_known_qt_noise():
            project.write(str(project_path))
        return project_path
    finally:
        shutdown_qgis_app(app, created)


def write_readme(
    path: Path,
    *,
    project_path: Path,
    gpkg_path: Path,
    dem_path: Path,
    output_crs: str,
    output_unit: str,
    geometry_z_unit: str,
) -> None:
    path.write_text(
        "\n".join(
            [
                "# LOS Segment Cover QGIS Project",
                "",
                f"- GeoPackage: `{gpkg_path}`",
                f"- Project: `{project_path}`",
                f"- DEM: `{dem_path}`",
                f"- Layer CRS: `{output_crs}`",
                f"- Report unit: `{output_unit}`",
                f"- Geometry Z unit: `{geometry_z_unit}`",
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
                "- `pair_segments_z`: all LOS-valid point-to-point segments",
                "- `meaningful_lines_z`: deduped 3+ point line families",
                "- `selected_lines_z`: line families chosen by the solver",
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

    pair_segments_path = Path(manifest["files"]["pair_segments"])
    meaningful_lines_path = Path(manifest["files"]["meaningful_lines"])
    selected_segments_path = Path(manifest["files"]["selected_segments"])
    point_status_path = Path(manifest["files"]["point_status"])
    coverage_offsets_path = Path(manifest["files"]["coverage_offsets"])
    dem_path = Path(args.dem).resolve() if args.dem else Path(manifest["dem_path"])
    output_epsg = int(manifest.get("output_epsg", 4326))
    output_crs = str(manifest.get("output_crs", f"EPSG:{output_epsg}"))
    output_unit = str(manifest.get("output_unit", "meters"))
    geometry_z_unit = str(manifest.get("geometry_z_unit", "meters"))

    pair_segments = _read_geojson_features(pair_segments_path)
    meaningful_lines = _read_geojson_features(meaningful_lines_path)
    selected_segments = _read_geojson_features(selected_segments_path)
    coverage_offsets = _read_geojson_features(coverage_offsets_path)
    point_rows = _read_csv_rows(point_status_path)

    gpkg_path = output_dir / "los_segment_cover.gpkg"
    project_path = output_dir / "los_segment_cover.qgs"
    readme_path = output_dir / "README.md"

    write_geopackage(
        gpkg_path,
        point_rows=point_rows,
        pair_segment_features=pair_segments,
        meaningful_line_features=meaningful_lines,
        selected_line_features=selected_segments,
        coverage_offset_features=coverage_offsets,
        output_epsg=output_epsg,
    )
    write_qgis_project(project_path=project_path, gpkg_path=gpkg_path, dem_path=dem_path)
    write_readme(
        readme_path,
        project_path=project_path,
        gpkg_path=gpkg_path,
        dem_path=dem_path,
        output_crs=output_crs,
        output_unit=output_unit,
        geometry_z_unit=geometry_z_unit,
    )

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
