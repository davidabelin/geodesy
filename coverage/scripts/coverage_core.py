from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from pyproj import Geod

from coverage_solver import CoverCandidate, solve_networkx_full_cover


WGS84 = Geod(ellps="WGS84")
METERS_PER_MILE = 1609.344
FEET_PER_METER = 3.280839895013123

ID_FIELD_CANDIDATES = (
    "LOC",
    "loc",
    "id",
    "ID",
    "name",
    "NAME",
    "label",
    "LABEL",
)
LAT_FIELD_CANDIDATES = (
    "LAT",
    "lat",
    "latitude",
    "Latitude",
    "LAT_Orig",
    "lat_orig",
)
LON_FIELD_CANDIDATES = (
    "LON",
    "lon",
    "longitude",
    "Longitude",
    "LON_Orig",
    "lon_orig",
)
ALT_M_FIELD_CANDIDATES = (
    "ALT_M",
    "alt_m",
    "ALT",
    "alt",
    "altitude_m",
    "elev_m",
    "z_m",
)
ALT_FT_FIELD_CANDIDATES = (
    "ALT_FT",
    "alt_ft",
    "elev_ft",
    "z_ft",
)


@dataclass
class PointRecord:
    point_id: str
    lat: float
    lon: float
    alt_m: float
    source_index: int
    properties: dict[str, Any]
    has_altitude: bool = False


@dataclass
class Dataset:
    path: Path
    fmt: str
    points: list[PointRecord]
    id_field: str
    lat_field: str
    lon_field: str
    alt_field: str


def resolve_input_path(raw_path: str, repo_root: Path) -> Path:
    candidate = Path(raw_path)
    probes: list[Path] = []
    if candidate.is_absolute():
        probes.append(candidate)
    else:
        probes.extend(
            [
                candidate,
                repo_root / candidate,
                repo_root / "data" / candidate,
            ]
        )

    for probe in probes:
        if probe.exists():
            return probe.resolve()
    raise FileNotFoundError(f"Input file not found: {raw_path}")


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _first_present(header: Iterable[str], candidates: Iterable[str]) -> Optional[str]:
    header_set = set(header)
    for candidate in candidates:
        if candidate in header_set:
            return candidate
    return None


def _to_float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    return float(value)


def _has_value(value: Any) -> bool:
    return value not in (None, "")


def _normalize_id(value: Any, fallback_index: int) -> str:
    if value is None:
        return f"row_{fallback_index}"
    text = str(value).strip()
    return text or f"row_{fallback_index}"


def load_dataset(
    input_path: str | Path,
    repo_root: Path,
    *,
    id_field: Optional[str] = None,
    lat_field: Optional[str] = None,
    lon_field: Optional[str] = None,
    alt_field: Optional[str] = None,
) -> Dataset:
    path = resolve_input_path(str(input_path), repo_root)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _load_csv_dataset(
            path,
            id_field=id_field,
            lat_field=lat_field,
            lon_field=lon_field,
            alt_field=alt_field,
        )
    if suffix in (".geojson", ".json"):
        return _load_geojson_dataset(path, id_field=id_field, alt_field=alt_field)
    raise ValueError(f"Unsupported input format: {path.suffix}. Use CSV or GeoJSON.")


def _load_csv_dataset(
    path: Path,
    *,
    id_field: Optional[str],
    lat_field: Optional[str],
    lon_field: Optional[str],
    alt_field: Optional[str],
) -> Dataset:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header row: {path}")

        csv_id_field = id_field or _first_present(reader.fieldnames, ID_FIELD_CANDIDATES)
        csv_lat_field = lat_field or _first_present(reader.fieldnames, LAT_FIELD_CANDIDATES)
        csv_lon_field = lon_field or _first_present(reader.fieldnames, LON_FIELD_CANDIDATES)
        csv_alt_field = alt_field or _first_present(reader.fieldnames, ALT_M_FIELD_CANDIDATES)
        alt_ft_field = None if csv_alt_field else _first_present(
            reader.fieldnames, ALT_FT_FIELD_CANDIDATES
        )

        if not csv_lat_field or not csv_lon_field:
            raise ValueError(
                f"Could not detect LAT/LON fields in CSV {path.name}. "
                "Use --lat-field and --lon-field if needed."
            )

        points: list[PointRecord] = []
        for index, row in enumerate(reader, start=1):
            point_id = _normalize_id(row.get(csv_id_field) if csv_id_field else None, index)
            lat = _to_float(row.get(csv_lat_field))
            lon = _to_float(row.get(csv_lon_field))
            if csv_alt_field:
                has_altitude = _has_value(row.get(csv_alt_field))
                alt_m = _to_float(row.get(csv_alt_field), 0.0)
            elif alt_ft_field:
                has_altitude = _has_value(row.get(alt_ft_field))
                alt_m = _to_float(row.get(alt_ft_field), 0.0) / FEET_PER_METER
            else:
                has_altitude = False
                alt_m = 0.0

            points.append(
                PointRecord(
                    point_id=point_id,
                    lat=lat,
                    lon=lon,
                    alt_m=alt_m,
                    source_index=index,
                    properties=dict(row),
                    has_altitude=has_altitude,
                )
            )

    return Dataset(
        path=path,
        fmt="csv",
        points=points,
        id_field=csv_id_field or "__row_index__",
        lat_field=csv_lat_field,
        lon_field=csv_lon_field,
        alt_field=csv_alt_field or alt_ft_field or "__default_zero__",
    )


def _load_geojson_dataset(
    path: Path,
    *,
    id_field: Optional[str],
    alt_field: Optional[str],
) -> Dataset:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("type") != "FeatureCollection":
        raise ValueError(f"Expected FeatureCollection in {path}")

    points: list[PointRecord] = []
    geo_id_field = id_field or None
    geo_alt_field = alt_field or None

    for index, feature in enumerate(payload.get("features", []), start=1):
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "Point":
            continue

        coords = geometry.get("coordinates") or []
        if len(coords) < 2:
            continue

        lon = float(coords[0])
        lat = float(coords[1])
        props = feature.get("properties") or {}

        point_id = _normalize_id(
            props.get(geo_id_field) if geo_id_field else props.get("LOC", feature.get("id")),
            index,
        )

        if len(coords) >= 3:
            alt_m = _to_float(coords[2], 0.0)
            effective_alt_field = "__geometry_z__"
            has_altitude = _has_value(coords[2])
        else:
            geo_alt_field = geo_alt_field or _first_present(
                props.keys(), ALT_M_FIELD_CANDIDATES
            )
            alt_ft_field = None if geo_alt_field else _first_present(
                props.keys(), ALT_FT_FIELD_CANDIDATES
            )
            if geo_alt_field:
                has_altitude = _has_value(props.get(geo_alt_field))
                alt_m = _to_float(props.get(geo_alt_field), 0.0)
                effective_alt_field = geo_alt_field
            elif alt_ft_field:
                has_altitude = _has_value(props.get(alt_ft_field))
                alt_m = _to_float(props.get(alt_ft_field), 0.0) / FEET_PER_METER
                effective_alt_field = alt_ft_field
            else:
                has_altitude = False
                alt_m = 0.0
                effective_alt_field = "__default_zero__"

        points.append(
            PointRecord(
                point_id=point_id,
                lat=lat,
                lon=lon,
                alt_m=alt_m,
                source_index=index,
                properties=dict(props),
                has_altitude=has_altitude,
            )
        )

    return Dataset(
        path=path,
        fmt="geojson",
        points=points,
        id_field=geo_id_field or "LOC/id",
        lat_field="geometry.coordinates[1]",
        lon_field="geometry.coordinates[0]",
        alt_field=effective_alt_field if points else "__default_zero__",
    )


def dataset_summary(dataset: Dataset) -> dict[str, Any]:
    if not dataset.points:
        return {
            "input_path": str(dataset.path),
            "format": dataset.fmt,
            "record_count": 0,
            "id_field": dataset.id_field,
            "lat_field": dataset.lat_field,
            "lon_field": dataset.lon_field,
            "alt_field": dataset.alt_field,
            "min_lat": None,
            "max_lat": None,
            "min_lon": None,
            "max_lon": None,
            "min_alt_m": None,
            "max_alt_m": None,
        }

    lats = [point.lat for point in dataset.points]
    lons = [point.lon for point in dataset.points]
    alts = [point.alt_m for point in dataset.points]
    return {
        "input_path": str(dataset.path),
        "format": dataset.fmt,
        "record_count": len(dataset.points),
        "id_field": dataset.id_field,
        "lat_field": dataset.lat_field,
        "lon_field": dataset.lon_field,
        "alt_field": dataset.alt_field,
        "min_lat": min(lats),
        "max_lat": max(lats),
        "min_lon": min(lons),
        "max_lon": max(lons),
        "min_alt_m": min(alts),
        "max_alt_m": max(alts),
    }


def convert_distance_to_meters(distance: float, unit: str) -> float:
    normalized = unit.strip().lower()
    if normalized in ("meter", "meters", "m"):
        return distance
    if normalized in ("kilometer", "kilometers", "km"):
        return distance * 1000.0
    if normalized in ("mile", "miles", "mi"):
        return distance * METERS_PER_MILE
    if normalized in ("foot", "feet", "ft"):
        return distance / FEET_PER_METER
    raise ValueError(f"Unsupported distance unit: {unit}")


def _geodetic_to_ecef(lat: float, lon: float, alt_m: float) -> tuple[float, float, float]:
    a = 6378137.0
    f = 1 / 298.257223563
    e2 = 2 * f - f**2

    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    n = a / math.sqrt(1 - e2 * math.sin(lat_rad) ** 2)

    x = (n + alt_m) * math.cos(lat_rad) * math.cos(lon_rad)
    y = (n + alt_m) * math.cos(lat_rad) * math.sin(lon_rad)
    z = (n * (1 - e2) + alt_m) * math.sin(lat_rad)
    return x, y, z


def distance_m(
    point_a: PointRecord,
    point_b: PointRecord,
    *,
    mode: str = "surface",
) -> float:
    if mode == "surface":
        _, _, dist = WGS84.inv(point_a.lon, point_a.lat, point_b.lon, point_b.lat)
        return float(dist)
    if mode == "ecef-3d":
        ax, ay, az = _geodetic_to_ecef(point_a.lat, point_a.lon, point_a.alt_m)
        bx, by, bz = _geodetic_to_ecef(point_b.lat, point_b.lon, point_b.alt_m)
        return math.sqrt((ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2)
    raise ValueError(f"Unsupported distance mode: {mode}")


def build_matrix_rows(
    demand_points: list[PointRecord],
    candidate_points: list[PointRecord],
    *,
    radius_m: float,
    distance_mode: str,
    covered_only: bool = False,
    exclude_self: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for demand in demand_points:
        for candidate in candidate_points:
            if exclude_self and demand.point_id == candidate.point_id:
                continue
            dist = distance_m(demand, candidate, mode=distance_mode)
            covered = dist <= radius_m
            if covered_only and not covered:
                continue
            rows.append(
                {
                    "demand_id": demand.point_id,
                    "candidate_id": candidate.point_id,
                    "covered": covered,
                    "distance_m": round(dist, 6),
                    "radius_m": round(radius_m, 6),
                    "distance_mode": distance_mode,
                    "demand_lat": demand.lat,
                    "demand_lon": demand.lon,
                    "candidate_lat": candidate.lat,
                    "candidate_lon": candidate.lon,
                }
            )
    return rows


def build_coverage_lookup(
    demand_points: list[PointRecord],
    candidate_points: list[PointRecord],
    *,
    radius_m: float,
    distance_mode: str,
    exclude_self: bool = False,
) -> tuple[list[set[int]], list[set[int]]]:
    candidate_covers: list[set[int]] = []
    demand_covered_by: list[set[int]] = [set() for _ in demand_points]

    for cand_index, candidate in enumerate(candidate_points):
        covered_demands: set[int] = set()
        for dem_index, demand in enumerate(demand_points):
            if exclude_self and demand.point_id == candidate.point_id:
                continue
            if distance_m(demand, candidate, mode=distance_mode) <= radius_m:
                covered_demands.add(dem_index)
                demand_covered_by[dem_index].add(cand_index)
        candidate_covers.append(covered_demands)
    return candidate_covers, demand_covered_by


def greedy_full_cover(
    demand_points: list[PointRecord],
    candidate_points: list[PointRecord],
    *,
    radius_m: float,
    distance_mode: str,
    exclude_self: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    candidate_covers, _ = build_coverage_lookup(
        demand_points,
        candidate_points,
        radius_m=radius_m,
        distance_mode=distance_mode,
        exclude_self=exclude_self,
    )

    uncovered = set(range(len(demand_points)))
    available = set(range(len(candidate_points)))
    rows: list[dict[str, Any]] = []
    rank = 1

    while uncovered:
        best_index = None
        best_new_cover: set[int] = set()

        for cand_index in sorted(available):
            new_cover = candidate_covers[cand_index] & uncovered
            if len(new_cover) > len(best_new_cover):
                best_index = cand_index
                best_new_cover = new_cover

        if best_index is None or not best_new_cover:
            break

        uncovered -= best_new_cover
        available.remove(best_index)
        candidate = candidate_points[best_index]
        rows.append(
            {
                "rank": rank,
                "candidate_id": candidate.point_id,
                "newly_covered_count": len(best_new_cover),
                "total_covered_count": len(demand_points) - len(uncovered),
                "remaining_uncovered_count": len(uncovered),
                "covered_demand_ids": "|".join(
                    demand_points[index].point_id for index in sorted(best_new_cover)
                ),
            }
        )
        rank += 1

    uncovered_ids = [demand_points[index].point_id for index in sorted(uncovered)]
    return rows, uncovered_ids


def networkx_full_cover(
    demand_points: list[PointRecord],
    candidate_points: list[PointRecord],
    *,
    radius_m: float,
    distance_mode: str,
    exclude_self: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    candidate_covers, _ = build_coverage_lookup(
        demand_points,
        candidate_points,
        radius_m=radius_m,
        distance_mode=distance_mode,
        exclude_self=exclude_self,
    )
    universe_ids = [point.point_id for point in demand_points]
    cover_candidates = [
        CoverCandidate(
            candidate_id=candidate_points[index].point_id,
            covered_ids=frozenset(
                demand_points[demand_index].point_id
                for demand_index in sorted(covered_indexes)
            ),
            weight=_networkx_candidate_weight(index, covered_indexes),
            sort_key=(-len(covered_indexes), index, candidate_points[index].point_id),
            metadata={"candidate_index": index},
        )
        for index, covered_indexes in enumerate(candidate_covers)
        if covered_indexes
    ]

    solution = solve_networkx_full_cover(universe_ids, cover_candidates)
    selected_indexes = {
        int(candidate.metadata["candidate_index"])
        for candidate in cover_candidates
        if candidate.candidate_id in solution.selected_ids
    }
    rows, uncovered_indexes = _rank_selected_candidate_indexes(
        demand_points,
        candidate_points,
        candidate_covers,
        selected_indexes,
    )
    uncovered_ids = [demand_points[index].point_id for index in sorted(uncovered_indexes)]
    return rows, uncovered_ids


def greedy_budgeted_max_cover(
    demand_points: list[PointRecord],
    candidate_points: list[PointRecord],
    *,
    radius_m: float,
    distance_mode: str,
    budget: int,
    exclude_self: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    candidate_covers, _ = build_coverage_lookup(
        demand_points,
        candidate_points,
        radius_m=radius_m,
        distance_mode=distance_mode,
        exclude_self=exclude_self,
    )

    uncovered = set(range(len(demand_points)))
    available = set(range(len(candidate_points)))
    rows: list[dict[str, Any]] = []

    for rank in range(1, budget + 1):
        best_index = None
        best_new_cover: set[int] = set()

        for cand_index in sorted(available):
            new_cover = candidate_covers[cand_index] & uncovered
            if len(new_cover) > len(best_new_cover):
                best_index = cand_index
                best_new_cover = new_cover

        if best_index is None or not best_new_cover:
            break

        uncovered -= best_new_cover
        available.remove(best_index)
        candidate = candidate_points[best_index]
        rows.append(
            {
                "rank": rank,
                "candidate_id": candidate.point_id,
                "newly_covered_count": len(best_new_cover),
                "total_covered_count": len(demand_points) - len(uncovered),
                "remaining_uncovered_count": len(uncovered),
                "covered_demand_ids": "|".join(
                    demand_points[index].point_id for index in sorted(best_new_cover)
                ),
            }
        )

        if not uncovered:
            break

    uncovered_ids = [demand_points[index].point_id for index in sorted(uncovered)]
    return rows, uncovered_ids


def _rank_selected_candidate_indexes(
    demand_points: list[PointRecord],
    candidate_points: list[PointRecord],
    candidate_covers: list[set[int]],
    selected_indexes: set[int],
) -> tuple[list[dict[str, Any]], set[int]]:
    uncovered = set(range(len(demand_points)))
    remaining = set(selected_indexes)
    rows: list[dict[str, Any]] = []
    rank = 1

    while remaining:
        best_index = None
        best_new_cover: set[int] = set()
        for candidate_index in sorted(remaining):
            new_cover = candidate_covers[candidate_index] & uncovered
            if (
                best_index is None
                or len(new_cover) > len(best_new_cover)
                or (len(new_cover) == len(best_new_cover) and candidate_index < best_index)
            ):
                best_index = candidate_index
                best_new_cover = new_cover

        if best_index is None:
            break

        uncovered -= best_new_cover
        remaining.remove(best_index)
        candidate = candidate_points[best_index]
        rows.append(
            {
                "rank": rank,
                "candidate_id": candidate.point_id,
                "newly_covered_count": len(best_new_cover),
                "total_covered_count": len(demand_points) - len(uncovered),
                "remaining_uncovered_count": len(uncovered),
                "covered_demand_ids": "|".join(
                    demand_points[index].point_id for index in sorted(best_new_cover)
                ),
            }
        )
        rank += 1

    return rows, uncovered


def _networkx_candidate_weight(candidate_index: int, covered_indexes: set[int]) -> float:
    coverage_bonus = 1e-6 * len(covered_indexes)
    index_tiebreaker = candidate_index * 1e-12
    return 1.0 - coverage_bonus + index_tiebreaker


def write_rows(path: Path, rows: list[dict[str, Any]], output_format: str) -> None:
    ensure_parent_dir(path)
    if output_format == "json":
        path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        return

    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)

    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


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


def _geodesic_buffer_ring(
    lat: float,
    lon: float,
    radius_m: float,
    *,
    segments: int = 72,
) -> list[list[float]]:
    ring: list[list[float]] = []
    for step in range(segments):
        azimuth = (360.0 * step) / segments
        out_lon, out_lat, _ = WGS84.fwd(lon, lat, azimuth, radius_m)
        ring.append([round(out_lon, 9), round(out_lat, 9)])
    if ring:
        ring.append(ring[0])
    return ring


def _link_feature(
    demand: PointRecord,
    candidate: PointRecord,
    *,
    distance_value_m: float,
    radius_m: float,
    distance_mode: str,
    selected_candidate: int,
) -> dict[str, Any]:
    return _feature(
        {
            "type": "LineString",
            "coordinates": [
                [round(demand.lon, 9), round(demand.lat, 9)],
                [round(candidate.lon, 9), round(candidate.lat, 9)],
            ],
        },
        {
            "demand_id": demand.point_id,
            "candidate_id": candidate.point_id,
            "distance_m": round(distance_value_m, 6),
            "radius_m": round(radius_m, 6),
            "distance_mode": distance_mode,
            "selected_candidate": selected_candidate,
        },
    )


def _point_feature(
    point: PointRecord,
    *,
    role: str,
    properties: dict[str, Any],
) -> dict[str, Any]:
    return _feature(
        {
            "type": "Point",
            "coordinates": [round(point.lon, 9), round(point.lat, 9)],
        },
        {
            "point_id": point.point_id,
            "lat": point.lat,
            "lon": point.lon,
            "alt_m": point.alt_m,
            "role": role,
            **properties,
        },
    )


def _zone_feature(
    point: PointRecord,
    *,
    radius_m: float,
    properties: dict[str, Any],
    segments: int = 72,
) -> dict[str, Any]:
    return _feature(
        {
            "type": "Polygon",
            "coordinates": [_geodesic_buffer_ring(point.lat, point.lon, radius_m, segments=segments)],
        },
        {
            "point_id": point.point_id,
            "lat": point.lat,
            "lon": point.lon,
            "alt_m": point.alt_m,
            "radius_m": round(radius_m, 6),
            **properties,
        },
    )


def _make_qgis_loader_script(
    bundle_dir: Path,
    title: str,
    *,
    demands_path: Path,
    candidates_path: Path,
    zones_path: Path,
    links_path: Path,
) -> str:
    payload = {
        "title": title,
        "bundle_dir": str(bundle_dir),
        "demands": str(demands_path),
        "candidates": str(candidates_path),
        "zones": str(zones_path),
        "links": str(links_path),
    }
    return f"""from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsFillSymbol,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsProject,
    QgsRendererCategory,
    QgsTextFormat,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtGui import QColor

BUNDLE = {json.dumps(payload, indent=4)}


def add_layer(label, path):
    layer = QgsVectorLayer(path, label, "ogr")
    if not layer.isValid():
        raise RuntimeError(f"Failed to load {{label}} from {{path}}")
    QgsProject.instance().addMapLayer(layer, False)
    return layer


def add_to_group(group_name, layer):
    root = QgsProject.instance().layerTreeRoot()
    group = root.findGroup(group_name)
    if group is None:
        group = root.insertGroup(0, group_name)
    group.addLayer(layer)


def set_labels(layer, field_name, color, size=9):
    settings = QgsPalLayerSettings()
    settings.fieldName = field_name
    settings.enabled = True
    text_format = QgsTextFormat()
    text_format.setSize(size)
    text_format.setColor(QColor(color))
    settings.setFormat(text_format)
    layer.setLabelsEnabled(True)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))


def style_demands(layer):
    covered_symbol = QgsMarkerSymbol.createSimple({{
        "name": "circle",
        "color": "36,123,160,220",
        "outline_color": "255,255,255,200",
        "size": "3.6",
    }})
    uncovered_symbol = QgsMarkerSymbol.createSimple({{
        "name": "circle",
        "color": "196,48,43,230",
        "outline_color": "255,255,255,200",
        "size": "3.6",
    }})
    renderer = QgsCategorizedSymbolRenderer(
        "covered",
        [
            QgsRendererCategory(1, covered_symbol, "Covered demand"),
            QgsRendererCategory(0, uncovered_symbol, "Uncovered demand"),
        ],
    )
    layer.setRenderer(renderer)
    set_labels(layer, "point_id", "#ffffff", 8)
    layer.triggerRepaint()


def style_candidates(layer):
    selected_symbol = QgsMarkerSymbol.createSimple({{
        "name": "square",
        "color": "255,183,3,240",
        "outline_color": "48,48,48,230",
        "size": "4.2",
    }})
    other_symbol = QgsMarkerSymbol.createSimple({{
        "name": "circle",
        "color": "82,82,82,220",
        "outline_color": "255,255,255,180",
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
    set_labels(layer, "point_id", "#f6f6f6", 8)
    layer.triggerRepaint()


def style_zones(layer):
    selected_symbol = QgsFillSymbol.createSimple({{
        "color": "255,183,3,40",
        "outline_color": "255,183,3,210",
        "outline_width": "0.8",
    }})
    other_symbol = QgsFillSymbol.createSimple({{
        "color": "66,165,245,18",
        "outline_color": "66,165,245,130",
        "outline_width": "0.4",
    }})
    renderer = QgsCategorizedSymbolRenderer(
        "selected",
        [
            QgsRendererCategory(1, selected_symbol, "Selected coverage zone"),
            QgsRendererCategory(0, other_symbol, "Coverage zone"),
        ],
    )
    layer.setRenderer(renderer)
    layer.setOpacity(0.72)
    layer.triggerRepaint()


def style_links(layer):
    selected_symbol = QgsLineSymbol.createSimple({{
        "line_color": "255,183,3,210",
        "line_width": "0.6",
    }})
    other_symbol = QgsLineSymbol.createSimple({{
        "line_color": "120,120,120,120",
        "line_width": "0.26",
    }})
    renderer = QgsCategorizedSymbolRenderer(
        "selected_candidate",
        [
            QgsRendererCategory(1, selected_symbol, "Link to selected candidate"),
            QgsRendererCategory(0, other_symbol, "Covered link"),
        ],
    )
    layer.setRenderer(renderer)
    layer.setOpacity(0.8)
    layer.triggerRepaint()


group_name = BUNDLE["title"]
zones = add_layer("coverage_zones", BUNDLE["zones"])
links = add_layer("coverage_links", BUNDLE["links"])
candidates = add_layer("candidates", BUNDLE["candidates"])
demands = add_layer("demands", BUNDLE["demands"])

add_to_group(group_name, zones)
add_to_group(group_name, links)
add_to_group(group_name, candidates)
add_to_group(group_name, demands)

style_zones(zones)
style_links(links)
style_candidates(candidates)
style_demands(demands)

print("Loaded coverage bundle:", BUNDLE["bundle_dir"])
print("Group:", group_name)
"""


def write_qgis_bundle(
    bundle_dir: Path,
    *,
    demand_dataset: Dataset,
    candidate_dataset: Dataset,
    radius_m: float,
    distance_mode: str,
    exclude_self: bool = False,
    solver: str = "full-cover",
    budget: Optional[int] = None,
) -> dict[str, Any]:
    bundle_dir.mkdir(parents=True, exist_ok=True)

    matrix_rows = build_matrix_rows(
        demand_dataset.points,
        candidate_dataset.points,
        radius_m=radius_m,
        distance_mode=distance_mode,
        covered_only=True,
        exclude_self=exclude_self,
    )

    selected_rank_by_id: dict[str, int] = {}
    selected_new_cover_by_id: dict[str, int] = {}
    uncovered_ids: list[str] = []
    solver_rows: list[dict[str, Any]] = []
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
    elif solver == "networkx-full-cover":
        solver_rows, uncovered_ids = networkx_full_cover(
            demand_dataset.points,
            candidate_dataset.points,
            radius_m=radius_m,
            distance_mode=distance_mode,
            exclude_self=exclude_self,
        )
    elif solver != "none":
        raise ValueError(f"Unsupported solver: {solver}")

    for row in solver_rows:
        candidate_id = str(row["candidate_id"])
        selected_rank_by_id[candidate_id] = int(row["rank"])
        selected_new_cover_by_id[candidate_id] = int(row["newly_covered_count"])

    demand_index = {point.point_id: point for point in demand_dataset.points}
    candidate_index = {point.point_id: point for point in candidate_dataset.points}

    demand_stats: dict[str, dict[str, Any]] = {
        point.point_id: {
            "coverage_count": 0,
            "covered": 0,
            "nearest_candidate_id": None,
            "nearest_distance_m": None,
            "selected_coverage_count": 0,
        }
        for point in demand_dataset.points
    }
    candidate_stats: dict[str, dict[str, Any]] = {
        point.point_id: {
            "cover_count": 0,
            "selected": 1 if point.point_id in selected_rank_by_id else 0,
            "selected_rank": selected_rank_by_id.get(point.point_id, 0),
            "selected_newly_covered_count": selected_new_cover_by_id.get(point.point_id, 0),
        }
        for point in candidate_dataset.points
    }

    link_features: list[dict[str, Any]] = []
    for row in matrix_rows:
        demand = demand_index[row["demand_id"]]
        candidate = candidate_index[row["candidate_id"]]
        selected_candidate = 1 if candidate.point_id in selected_rank_by_id else 0

        demand_stat = demand_stats[demand.point_id]
        demand_stat["coverage_count"] += 1
        demand_stat["covered"] = 1
        if (
            demand_stat["nearest_distance_m"] is None
            or row["distance_m"] < demand_stat["nearest_distance_m"]
        ):
            demand_stat["nearest_distance_m"] = row["distance_m"]
            demand_stat["nearest_candidate_id"] = candidate.point_id
        if selected_candidate:
            demand_stat["selected_coverage_count"] += 1

        candidate_stats[candidate.point_id]["cover_count"] += 1

        link_features.append(
            _link_feature(
                demand,
                candidate,
                distance_value_m=row["distance_m"],
                radius_m=radius_m,
                distance_mode=distance_mode,
                selected_candidate=selected_candidate,
            )
        )

    demand_features = [
        _point_feature(point, role="demand", properties=demand_stats[point.point_id])
        for point in demand_dataset.points
    ]
    candidate_features = [
        _point_feature(point, role="candidate", properties=candidate_stats[point.point_id])
        for point in candidate_dataset.points
    ]

    zone_features = [
        _zone_feature(
            point,
            radius_m=radius_m,
            properties={
                **candidate_stats[point.point_id],
                "cover_count": candidate_stats[point.point_id]["cover_count"],
                "zone_is_approximate": 1 if distance_mode == "ecef-3d" else 0,
            },
        )
        for point in candidate_dataset.points
        if candidate_stats[point.point_id]["cover_count"] > 0
    ]

    demands_path = bundle_dir / "demands.geojson"
    candidates_path = bundle_dir / "candidates.geojson"
    links_path = bundle_dir / "coverage_links.geojson"
    zones_path = bundle_dir / "coverage_zones.geojson"
    manifest_path = bundle_dir / "bundle_manifest.json"
    solver_rows_path = bundle_dir / "solver_rows.csv"
    loader_path = bundle_dir / "load_bundle_qgis.py"

    _write_geojson(demands_path, demand_features)
    _write_geojson(candidates_path, candidate_features)
    _write_geojson(links_path, link_features)
    _write_geojson(zones_path, zone_features)
    write_rows(solver_rows_path, solver_rows, "csv")

    title = f"coverage bundle: {demand_dataset.path.stem} -> {candidate_dataset.path.stem}"
    loader_path.write_text(
        _make_qgis_loader_script(
            bundle_dir,
            title,
            demands_path=demands_path,
            candidates_path=candidates_path,
            zones_path=zones_path,
            links_path=links_path,
        ),
        encoding="utf-8",
    )

    covered_demands = sum(1 for stats in demand_stats.values() if stats["covered"])
    selected_covered_demands = sum(
        1 for stats in demand_stats.values() if stats["selected_coverage_count"] > 0
    )
    selected_candidates = sum(1 for stats in candidate_stats.values() if stats["selected"])
    manifest = {
        "bundle_dir": str(bundle_dir),
        "demand_input": str(demand_dataset.path),
        "candidate_input": str(candidate_dataset.path),
        "radius_m": round(radius_m, 6),
        "distance_mode": distance_mode,
        "solver": solver,
        "budget": budget,
        "demand_count": len(demand_dataset.points),
        "candidate_count": len(candidate_dataset.points),
        "covered_demand_count": covered_demands,
        "uncovered_demand_count": len(demand_dataset.points) - covered_demands,
        "selected_covered_demand_count": selected_covered_demands,
        "selected_candidate_count": selected_candidates,
        "covered_pair_count": len(link_features),
        "selected_link_count": sum(
            1 for feature in link_features if feature["properties"]["selected_candidate"] == 1
        ),
        "files": {
            "demands": str(demands_path),
            "candidates": str(candidates_path),
            "coverage_links": str(links_path),
            "coverage_zones": str(zones_path),
            "solver_rows": str(solver_rows_path),
            "loader_script": str(loader_path),
        },
        "uncovered_demand_ids": uncovered_ids,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
