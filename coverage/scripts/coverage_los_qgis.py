from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Optional

from osgeo import gdal
from pyproj import CRS, Transformer

from coverage_core import (
    Dataset,
    PointRecord,
    WGS84,
    convert_distance_to_meters,
    distance_m,
    ensure_parent_dir,
    greedy_budgeted_max_cover,
    greedy_full_cover,
    load_dataset,
    write_rows,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "coverage" / "results"


class DemSampler:
    def __init__(self, dem_path: Path):
        gdal.UseExceptions()
        self.path = dem_path.resolve()
        self.dataset = gdal.Open(str(self.path))
        if self.dataset is None:
            raise RuntimeError(f"Could not open DEM: {self.path}")

        self.band = self.dataset.GetRasterBand(1)
        self.nodata = self.band.GetNoDataValue()
        self.geotransform = self.dataset.GetGeoTransform()
        projection = self.dataset.GetProjection()
        self.crs = CRS.from_wkt(projection) if projection else CRS.from_epsg(4326)
        self.transformer = Transformer.from_crs(
            "EPSG:4326", self.crs, always_xy=True
        )

        gt = self.geotransform
        if abs(gt[2]) > 1e-12 or abs(gt[4]) > 1e-12:
            raise RuntimeError(
                "DEM has rotated geotransform; this LOS script expects north-up rasters."
            )

    def sample(self, lon: float, lat: float) -> float:
        x, y = self.transformer.transform(lon, lat)
        gt = self.geotransform
        px = int(math.floor((x - gt[0]) / gt[1]))
        py = int(math.floor((y - gt[3]) / gt[5]))

        if px < 0 or py < 0 or px >= self.dataset.RasterXSize or py >= self.dataset.RasterYSize:
            raise ValueError(f"Point outside DEM extent: lon={lon}, lat={lat}")

        array = self.band.ReadAsArray(px, py, 1, 1)
        value = float(array[0][0])
        if self.nodata is not None and value == self.nodata:
            raise ValueError(f"DEM nodata at lon={lon}, lat={lat}")
        return value


def _feature(geometry: dict[str, Any], properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": properties,
    }


def _feature_collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def _write_geojson(path: Path, features: list[dict[str, Any]]) -> None:
    ensure_parent_dir(path)
    path.write_text(json.dumps(_feature_collection(features), indent=2), encoding="utf-8")


def _rounded_xyz(lon: float, lat: float, z: float) -> list[float]:
    return [round(lon, 9), round(lat, 9), round(z, 3)]


def _sample_path(
    start: PointRecord,
    end: PointRecord,
    *,
    start_z_m: float,
    end_z_m: float,
    sample_step_m: float,
    dem_sampler: DemSampler,
) -> dict[str, Any]:
    az12, _, geodesic_distance_m = WGS84.inv(start.lon, start.lat, end.lon, end.lat)
    step_count = max(1, int(math.ceil(geodesic_distance_m / sample_step_m)))

    min_clearance_m: Optional[float] = None
    blocked = False
    blocked_sample_index: Optional[int] = None
    terrain_vertices: list[list[float]] = []

    start_ground_m = dem_sampler.sample(start.lon, start.lat)
    end_ground_m = dem_sampler.sample(end.lon, end.lat)
    terrain_vertices.append(_rounded_xyz(start.lon, start.lat, start_ground_m))

    for sample_index in range(1, step_count):
        fraction = sample_index / step_count
        lon, lat, _ = WGS84.fwd(start.lon, start.lat, az12, geodesic_distance_m * fraction)
        terrain_m = dem_sampler.sample(lon, lat)
        line_z_m = start_z_m + fraction * (end_z_m - start_z_m)
        clearance_m = line_z_m - terrain_m
        if min_clearance_m is None or clearance_m < min_clearance_m:
            min_clearance_m = clearance_m
        if clearance_m <= 0 and not blocked:
            blocked = True
            blocked_sample_index = sample_index
        terrain_vertices.append(_rounded_xyz(lon, lat, terrain_m))

    terrain_vertices.append(_rounded_xyz(end.lon, end.lat, end_ground_m))
    if min_clearance_m is None:
        min_clearance_m = min(start_z_m - start_ground_m, end_z_m - end_ground_m)

    return {
        "geodesic_distance_m": float(geodesic_distance_m),
        "terrain_vertices": terrain_vertices,
        "visible": not blocked,
        "min_clearance_m": float(min_clearance_m),
        "sample_count": step_count + 1,
        "blocked_sample_index": blocked_sample_index,
    }


def _build_selected_sets(
    solver: str,
    *,
    demand_dataset: Dataset,
    candidate_dataset: Dataset,
    radius_m: float,
    distance_mode: str,
    exclude_self: bool,
    budget: Optional[int],
) -> tuple[dict[str, int], dict[str, int], list[str]]:
    selected_rank_by_id: dict[str, int] = {}
    selected_new_cover_by_id: dict[str, int] = {}
    uncovered_ids: list[str] = []

    if solver == "none":
        return selected_rank_by_id, selected_new_cover_by_id, uncovered_ids

    if solver == "full-cover":
        solver_rows, uncovered_ids = greedy_full_cover(
            demand_dataset.points,
            candidate_dataset.points,
            radius_m=radius_m,
            distance_mode=distance_mode,
            exclude_self=exclude_self,
        )
    elif solver == "max-cover":
        if budget is None:
            raise ValueError("--budget is required when solver=max-cover")
        solver_rows, uncovered_ids = greedy_budgeted_max_cover(
            demand_dataset.points,
            candidate_dataset.points,
            radius_m=radius_m,
            distance_mode=distance_mode,
            budget=budget,
            exclude_self=exclude_self,
        )
    else:
        raise ValueError(f"Unsupported solver: {solver}")

    for row in solver_rows:
        candidate_id = str(row["candidate_id"])
        selected_rank_by_id[candidate_id] = int(row["rank"])
        selected_new_cover_by_id[candidate_id] = int(row["newly_covered_count"])
    return selected_rank_by_id, selected_new_cover_by_id, uncovered_ids


def _make_qgis_loader_script(
    title: str,
    *,
    bundle_dir: Path,
    dem_path: Path,
    demands_path: Path,
    candidates_path: Path,
    terrain_traces_path: Path,
    los_lines_path: Path,
    summary_csv_path: Path,
    solver_rows_path: Path,
) -> str:
    payload = {
        "title": title,
        "bundle_dir": str(bundle_dir.resolve()),
        "dem": str(dem_path.resolve()),
        "demands": str(demands_path.resolve()),
        "candidates": str(candidates_path.resolve()),
        "terrain_traces": str(terrain_traces_path.resolve()),
        "los_lines": str(los_lines_path.resolve()),
        "summary_csv": str(summary_csv_path.resolve()),
        "solver_rows": str(solver_rows_path.resolve()),
    }
    return f"""from qgis.core import (
    Qgis,
    QgsCategorizedSymbolRenderer,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsProject,
    QgsRasterLayer,
    QgsRendererCategory,
    QgsTextFormat,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis._3d import QgsLine3DSymbol, QgsPhongMaterialSettings, QgsPoint3DSymbol, QgsVectorLayer3DRenderer
from qgis.PyQt.QtGui import QColor

BUNDLE = {json.dumps(payload, indent=4)}


def add_vector(label, path):
    layer = QgsVectorLayer(path, label, "ogr")
    if not layer.isValid():
        raise RuntimeError(f"Failed to load {{label}} from {{path}}")
    QgsProject.instance().addMapLayer(layer, False)
    return layer


def add_raster(label, path):
    layer = QgsRasterLayer(path, label)
    if not layer.isValid():
        raise RuntimeError(f"Failed to load raster {{label}} from {{path}}")
    QgsProject.instance().addMapLayer(layer, False)
    return layer


def add_to_group(group_name, layer):
    root = QgsProject.instance().layerTreeRoot()
    group = root.findGroup(group_name)
    if group is None:
        group = root.insertGroup(0, group_name)
    group.addLayer(layer)


def set_labels(layer, field_name, color, size=8):
    settings = QgsPalLayerSettings()
    settings.fieldName = field_name
    settings.enabled = True
    text_format = QgsTextFormat()
    text_format.setSize(size)
    text_format.setColor(QColor(color))
    settings.setFormat(text_format)
    layer.setLabelsEnabled(True)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))


def phong(color_text, opacity=1.0):
    material = QgsPhongMaterialSettings()
    color = QColor(color_text)
    material.setAmbient(color)
    material.setDiffuse(color)
    material.setOpacity(opacity)
    return material


def set_point_3d(layer, color_text):
    symbol = QgsPoint3DSymbol()
    symbol.setShape(QgsPoint3DSymbol.Sphere)
    symbol.setAltitudeClamping(Qgis.AltitudeClamping.Absolute)
    symbol.setMaterialSettings(phong(color_text, 0.98))
    renderer = QgsVectorLayer3DRenderer(symbol)
    renderer.setLayer(layer)
    layer.setRenderer3D(renderer)


def set_line_3d(layer, color_text, width=2.0):
    symbol = QgsLine3DSymbol()
    symbol.setAltitudeClamping(Qgis.AltitudeClamping.Absolute)
    symbol.setAltitudeBinding(Qgis.AltitudeBinding.Vertex)
    symbol.setRenderAsSimpleLines(True)
    symbol.setWidth(width)
    symbol.setMaterialSettings(phong(color_text, 0.96))
    renderer = QgsVectorLayer3DRenderer(symbol)
    renderer.setLayer(layer)
    layer.setRenderer3D(renderer)


def style_demands(layer):
    covered_symbol = QgsMarkerSymbol.createSimple({{
        "name": "circle",
        "color": "78,205,196,230",
        "outline_color": "18,18,18,220",
        "size": "3.5",
    }})
    uncovered_symbol = QgsMarkerSymbol.createSimple({{
        "name": "circle",
        "color": "215,48,39,235",
        "outline_color": "18,18,18,220",
        "size": "3.5",
    }})
    renderer = QgsCategorizedSymbolRenderer(
        "selected_visible",
        [
            QgsRendererCategory(1, covered_symbol, "Visible from selected LOS set"),
            QgsRendererCategory(0, uncovered_symbol, "Not visible from selected LOS set"),
        ],
    )
    layer.setRenderer(renderer)
    set_labels(layer, "point_id", "#f6f6f6", 8)
    set_point_3d(layer, "#4ecdc4")
    layer.triggerRepaint()


def style_candidates(layer):
    selected_symbol = QgsMarkerSymbol.createSimple({{
        "name": "square",
        "color": "255,196,61,245",
        "outline_color": "18,18,18,220",
        "size": "4.2",
    }})
    other_symbol = QgsMarkerSymbol.createSimple({{
        "name": "circle",
        "color": "160,160,160,220",
        "outline_color": "18,18,18,220",
        "size": "3.0",
    }})
    renderer = QgsCategorizedSymbolRenderer(
        "selected",
        [
            QgsRendererCategory(1, selected_symbol, "Selected candidate"),
            QgsRendererCategory(0, other_symbol, "Candidate"),
        ],
    )
    layer.setRenderer(renderer)
    set_labels(layer, "point_id", "#f2f2f2", 8)
    set_point_3d(layer, "#ffc43d")
    layer.triggerRepaint()


def style_los(layer):
    selected_visible = QgsLineSymbol.createSimple({{
        "line_color": "0,245,255,240",
        "line_width": "0.9",
    }})
    visible_symbol = QgsLineSymbol.createSimple({{
        "line_color": "0,245,255,120",
        "line_width": "0.45",
    }})
    blocked_symbol = QgsLineSymbol.createSimple({{
        "line_color": "255,84,112,230",
        "line_width": "0.7",
        "customdash": "3;1.5",
        "use_custom_dash": "1",
    }})
    selected_blocked = QgsLineSymbol.createSimple({{
        "line_color": "255,140,66,240",
        "line_width": "0.9",
        "customdash": "3;1.5",
        "use_custom_dash": "1",
    }})
    renderer = QgsCategorizedSymbolRenderer(
        "render_class",
        [
            QgsRendererCategory("selected_visible", selected_visible, "Selected visible LOS"),
            QgsRendererCategory("visible", visible_symbol, "Visible LOS"),
            QgsRendererCategory("blocked", blocked_symbol, "Blocked LOS"),
            QgsRendererCategory("selected_blocked", selected_blocked, "Selected blocked LOS"),
        ],
    )
    layer.setRenderer(renderer)
    set_line_3d(layer, "#00f5ff", 2.0)
    layer.triggerRepaint()


def style_terrain(layer):
    symbol = QgsLineSymbol.createSimple({{
        "line_color": "255,255,255,70",
        "line_width": "0.25",
    }})
    layer.renderer().setSymbol(symbol)
    set_line_3d(layer, "#d9d9d9", 1.0)
    layer.setOpacity(0.45)
    layer.triggerRepaint()


group_name = BUNDLE["title"]
dem = add_raster("dc_dem", BUNDLE["dem"])
terrain = add_vector("terrain_traces", BUNDLE["terrain_traces"])
los = add_vector("los_lines", BUNDLE["los_lines"])
candidates = add_vector("candidates_3d", BUNDLE["candidates"])
demands = add_vector("demands_3d", BUNDLE["demands"])

add_to_group(group_name, dem)
add_to_group(group_name, terrain)
add_to_group(group_name, los)
add_to_group(group_name, candidates)
add_to_group(group_name, demands)

style_terrain(terrain)
style_los(los)
style_candidates(candidates)
style_demands(demands)

print("Loaded LOS bundle:", BUNDLE["bundle_dir"])
print("Next in QGIS: View -> New 3D Map View")
print("Then set terrain to dc_dem and inspect los_lines over terrain_traces.")
"""


def _point_feature(
    point: PointRecord,
    *,
    ground_alt_m: float,
    display_alt_m: float,
    role: str,
    properties: dict[str, Any],
) -> dict[str, Any]:
    return _feature(
        {
            "type": "Point",
            "coordinates": _rounded_xyz(point.lon, point.lat, display_alt_m),
        },
        {
            "point_id": point.point_id,
            "ground_alt_m": round(ground_alt_m, 3),
            "display_alt_m": round(display_alt_m, 3),
            "role": role,
            **properties,
        },
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Coverage LOS bundle via QGIS runtime")
    sub = parser.add_subparsers(dest="cmd", required=True)

    los = sub.add_parser(
        "los-bundle",
        help="Legacy radius-first LOS bundle for QGIS inspection.",
    )
    los.add_argument("--input", required=True)
    los.add_argument("--candidates", default=None)
    los.add_argument("--dem", default=str(REPO_ROOT / "data" / "tif" / "dc_dem.tif"))
    los.add_argument("--id-field", default=None)
    los.add_argument("--lat-field", default=None)
    los.add_argument("--lon-field", default=None)
    los.add_argument("--alt-field", default=None)
    los.add_argument("--radius", required=True, type=float)
    los.add_argument(
        "--unit",
        choices=["meters", "m", "km", "miles", "mi", "feet", "ft"],
        default="meters",
    )
    los.add_argument(
        "--distance-mode",
        choices=["surface", "ecef-3d"],
        default="surface",
        help="Mode for the initial pair filter before LOS sampling.",
    )
    los.add_argument("--exclude-self", action="store_true")
    los.add_argument(
        "--observer-height-m",
        type=float,
        default=2.0,
        help="Height added above DEM altitude at demand points.",
    )
    los.add_argument(
        "--target-height-m",
        type=float,
        default=2.0,
        help="Height added above DEM altitude at candidate points.",
    )
    los.add_argument(
        "--sample-step-m",
        type=float,
        default=20.0,
        help="Sampling step for terrain checks along each LOS line.",
    )
    los.add_argument(
        "--solver",
        choices=["none", "full-cover", "max-cover"],
        default="max-cover",
        help="Optional greedy solver used to flag selected candidate lines.",
    )
    los.add_argument("--budget", type=int, default=5)
    los.add_argument("--output-dir", default=None)
    return parser


def run_los_bundle(args: argparse.Namespace) -> int:
    demand_dataset = load_dataset(
        args.input,
        REPO_ROOT,
        id_field=args.id_field,
        lat_field=args.lat_field,
        lon_field=args.lon_field,
        alt_field=args.alt_field,
    )
    candidate_dataset = load_dataset(
        args.candidates or args.input,
        REPO_ROOT,
        id_field=args.id_field,
        lat_field=args.lat_field,
        lon_field=args.lon_field,
        alt_field=args.alt_field,
    )
    dem_path = Path(args.dem)
    if not dem_path.is_absolute():
        dem_path = (REPO_ROOT / dem_path).resolve()
    if not dem_path.exists():
        raise FileNotFoundError(f"DEM not found: {args.dem}")

    radius_m = convert_distance_to_meters(args.radius, args.unit)
    selected_rank_by_id, selected_new_cover_by_id, uncovered_ids = _build_selected_sets(
        args.solver,
        demand_dataset=demand_dataset,
        candidate_dataset=candidate_dataset,
        radius_m=radius_m,
        distance_mode=args.distance_mode,
        exclude_self=args.exclude_self,
        budget=args.budget,
    )

    bundle_dir = (
        Path(args.output_dir)
        if args.output_dir
        else RESULTS_DIR / f"los_bundle__{demand_dataset.path.stem}__{candidate_dataset.path.stem}"
    )
    bundle_dir.mkdir(parents=True, exist_ok=True)

    dem_sampler = DemSampler(dem_path)

    demand_ground_alt: dict[str, float] = {}
    candidate_ground_alt: dict[str, float] = {}
    for point in demand_dataset.points:
        demand_ground_alt[point.point_id] = dem_sampler.sample(point.lon, point.lat)
    for point in candidate_dataset.points:
        candidate_ground_alt[point.point_id] = dem_sampler.sample(point.lon, point.lat)

    demand_features: list[dict[str, Any]] = []
    candidate_features: list[dict[str, Any]] = []
    terrain_features: list[dict[str, Any]] = []
    los_features: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

    demand_stats: dict[str, dict[str, Any]] = {
        point.point_id: {
            "selected_visible": 0,
            "any_visible": 0,
            "visible_count": 0,
            "blocked_count": 0,
        }
        for point in demand_dataset.points
    }
    candidate_stats: dict[str, dict[str, Any]] = {
        point.point_id: {
            "selected": 1 if point.point_id in selected_rank_by_id else 0,
            "selected_rank": selected_rank_by_id.get(point.point_id, 0),
            "selected_newly_covered_count": selected_new_cover_by_id.get(point.point_id, 0),
            "visible_los_count": 0,
            "blocked_los_count": 0,
        }
        for point in candidate_dataset.points
    }

    for demand in demand_dataset.points:
        for candidate in candidate_dataset.points:
            if args.exclude_self and demand.point_id == candidate.point_id:
                continue

            filter_distance_m = float(distance_m(demand, candidate, mode=args.distance_mode))
            if filter_distance_m > radius_m:
                continue

            demand_ground_m = demand_ground_alt[demand.point_id]
            candidate_ground_m = candidate_ground_alt[candidate.point_id]
            demand_display_m = demand_ground_m + args.observer_height_m
            candidate_display_m = candidate_ground_m + args.target_height_m

            path_info = _sample_path(
                demand,
                candidate,
                start_z_m=demand_display_m,
                end_z_m=candidate_display_m,
                sample_step_m=args.sample_step_m,
                dem_sampler=dem_sampler,
            )
            visible = bool(path_info["visible"])
            selected_candidate = 1 if candidate.point_id in selected_rank_by_id else 0

            render_class = "visible" if visible else "blocked"
            if selected_candidate:
                render_class = f"selected_{render_class}"

            if visible:
                demand_stats[demand.point_id]["any_visible"] = 1
                demand_stats[demand.point_id]["visible_count"] += 1
                candidate_stats[candidate.point_id]["visible_los_count"] += 1
                if selected_candidate:
                    demand_stats[demand.point_id]["selected_visible"] = 1
            else:
                demand_stats[demand.point_id]["blocked_count"] += 1
                candidate_stats[candidate.point_id]["blocked_los_count"] += 1

            terrain_features.append(
                _feature(
                    {
                        "type": "LineString",
                        "coordinates": path_info["terrain_vertices"],
                    },
                    {
                        "demand_id": demand.point_id,
                        "candidate_id": candidate.point_id,
                        "render_class": render_class,
                        "visible": 1 if visible else 0,
                        "selected_candidate": selected_candidate,
                        "min_clearance_m": round(path_info["min_clearance_m"], 3),
                        "distance_m": round(path_info["geodesic_distance_m"], 3),
                    },
                )
            )
            los_features.append(
                _feature(
                    {
                        "type": "LineString",
                        "coordinates": [
                            _rounded_xyz(demand.lon, demand.lat, demand_display_m),
                            _rounded_xyz(candidate.lon, candidate.lat, candidate_display_m),
                        ],
                    },
                    {
                        "demand_id": demand.point_id,
                        "candidate_id": candidate.point_id,
                        "render_class": render_class,
                        "visible": 1 if visible else 0,
                        "selected_candidate": selected_candidate,
                        "distance_m": round(path_info["geodesic_distance_m"], 3),
                        "min_clearance_m": round(path_info["min_clearance_m"], 3),
                        "blocked_sample_index": path_info["blocked_sample_index"],
                        "sample_count": path_info["sample_count"],
                    },
                )
            )
            summary_rows.append(
                {
                    "demand_id": demand.point_id,
                    "candidate_id": candidate.point_id,
                    "distance_m": round(path_info["geodesic_distance_m"], 3),
                    "visible": visible,
                    "selected_candidate": selected_candidate,
                    "min_clearance_m": round(path_info["min_clearance_m"], 3),
                    "sample_count": path_info["sample_count"],
                    "render_class": render_class,
                }
            )

    for point in demand_dataset.points:
        demand_features.append(
            _point_feature(
                point,
                ground_alt_m=demand_ground_alt[point.point_id],
                display_alt_m=demand_ground_alt[point.point_id] + args.observer_height_m,
                role="demand",
                properties=demand_stats[point.point_id],
            )
        )
    for point in candidate_dataset.points:
        candidate_features.append(
            _point_feature(
                point,
                ground_alt_m=candidate_ground_alt[point.point_id],
                display_alt_m=candidate_ground_alt[point.point_id] + args.target_height_m,
                role="candidate",
                properties=candidate_stats[point.point_id],
            )
        )

    demands_path = bundle_dir / "demands_3d.geojson"
    candidates_path = bundle_dir / "candidates_3d.geojson"
    terrain_traces_path = bundle_dir / "terrain_traces_3d.geojson"
    los_lines_path = bundle_dir / "los_lines_3d.geojson"
    summary_csv_path = bundle_dir / "los_summary.csv"
    solver_rows_path = bundle_dir / "solver_rows.csv"
    loader_path = bundle_dir / "load_los_bundle_qgis.py"
    manifest_path = bundle_dir / "bundle_manifest.json"
    readme_path = bundle_dir / "README.txt"

    _write_geojson(demands_path, demand_features)
    _write_geojson(candidates_path, candidate_features)
    _write_geojson(terrain_traces_path, terrain_features)
    _write_geojson(los_lines_path, los_features)
    write_rows(summary_csv_path, summary_rows, "csv")

    solver_rows = []
    if args.solver == "full-cover":
        solver_rows, _ = greedy_full_cover(
            demand_dataset.points,
            candidate_dataset.points,
            radius_m=radius_m,
            distance_mode=args.distance_mode,
            exclude_self=args.exclude_self,
        )
    elif args.solver == "max-cover":
        solver_rows, _ = greedy_budgeted_max_cover(
            demand_dataset.points,
            candidate_dataset.points,
            radius_m=radius_m,
            distance_mode=args.distance_mode,
            budget=args.budget,
            exclude_self=args.exclude_self,
        )
    write_rows(solver_rows_path, solver_rows, "csv")

    title = f"LOS bundle: {demand_dataset.path.stem} -> {candidate_dataset.path.stem}"
    loader_path.write_text(
        _make_qgis_loader_script(
            title,
            bundle_dir=bundle_dir,
            dem_path=dem_path,
            demands_path=demands_path,
            candidates_path=candidates_path,
            terrain_traces_path=terrain_traces_path,
            los_lines_path=los_lines_path,
            summary_csv_path=summary_csv_path,
            solver_rows_path=solver_rows_path,
        ),
        encoding="utf-8",
    )

    readme_path.write_text(
        "\n".join(
            [
                f"LOS bundle directory: {bundle_dir}",
                f"DEM: {dem_path}",
                "",
                "In QGIS Desktop:",
                "1. Open the Python Console.",
                f"2. Run: exec(open(r\"{loader_path.resolve()}\", encoding=\"utf-8\").read())",
                "3. Open View -> New 3D Map View.",
                "4. Use dc_dem as terrain and inspect los_lines over terrain_traces.",
                "",
                "What to look at:",
                "- bright cyan lines: visible selected LOS lines",
                "- pale cyan lines: visible LOS lines",
                "- red dashed lines: blocked LOS lines",
                "- white terrain traces: ground-following path for each LOS pair",
            ]
        ),
        encoding="utf-8",
    )

    selected_visible_count = sum(
        1 for row in summary_rows if row["visible"] and row["selected_candidate"] == 1
    )
    visible_count = sum(1 for row in summary_rows if row["visible"])
    blocked_count = sum(1 for row in summary_rows if not row["visible"])
    manifest = {
        "bundle_dir": str(bundle_dir),
        "demand_input": str(demand_dataset.path),
        "candidate_input": str(candidate_dataset.path),
        "dem_path": str(dem_path),
        "radius_m": round(radius_m, 3),
        "distance_mode": args.distance_mode,
        "observer_height_m": args.observer_height_m,
        "target_height_m": args.target_height_m,
        "sample_step_m": args.sample_step_m,
        "solver": args.solver,
        "budget": args.budget,
        "demand_count": len(demand_dataset.points),
        "candidate_count": len(candidate_dataset.points),
        "los_pair_count": len(summary_rows),
        "visible_los_count": visible_count,
        "blocked_los_count": blocked_count,
        "selected_visible_los_count": selected_visible_count,
        "selected_candidate_count": len(selected_rank_by_id),
        "uncovered_demand_ids": uncovered_ids,
        "files": {
            "demands_3d": str(demands_path),
            "candidates_3d": str(candidates_path),
            "terrain_traces_3d": str(terrain_traces_path),
            "los_lines_3d": str(los_lines_path),
            "los_summary": str(summary_csv_path),
            "solver_rows": str(solver_rows_path),
            "loader_script": str(loader_path),
            "readme": str(readme_path),
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"LOS bundle output: {bundle_dir}")
    print(f"LOS pairs written: {len(summary_rows)}")
    print(f"Visible LOS pairs: {visible_count}")
    print(f"Blocked LOS pairs: {blocked_count}")
    print(f"Selected visible LOS pairs: {selected_visible_count}")
    print(f"Loader script: {loader_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "los-bundle":
        return run_los_bundle(args)
    parser.error(f"Unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
