from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from pyproj import Geod


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
                alt_m = _to_float(row.get(csv_alt_field), 0.0)
            elif alt_ft_field:
                alt_m = _to_float(row.get(alt_ft_field), 0.0) / FEET_PER_METER
            else:
                alt_m = 0.0

            points.append(
                PointRecord(
                    point_id=point_id,
                    lat=lat,
                    lon=lon,
                    alt_m=alt_m,
                    source_index=index,
                    properties=dict(row),
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
        else:
            geo_alt_field = geo_alt_field or _first_present(
                props.keys(), ALT_M_FIELD_CANDIDATES
            )
            alt_ft_field = None if geo_alt_field else _first_present(
                props.keys(), ALT_FT_FIELD_CANDIDATES
            )
            if geo_alt_field:
                alt_m = _to_float(props.get(geo_alt_field), 0.0)
                effective_alt_field = geo_alt_field
            elif alt_ft_field:
                alt_m = _to_float(props.get(alt_ft_field), 0.0) / FEET_PER_METER
                effective_alt_field = alt_ft_field
            else:
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

