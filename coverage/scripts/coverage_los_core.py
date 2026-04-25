from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np
from pyproj import CRS, Transformer

from coverage_core import (
    Dataset,
    FEET_PER_METER,
    WGS84,
    ensure_parent_dir,
    load_dataset,
    resolve_input_path,
    write_rows,
)
from coverage_solver import CoverCandidate, solve_networkx_full_cover


try:
    from scipy.optimize import Bounds, LinearConstraint, milp

    MILP_AVAILABLE = True
except Exception:
    Bounds = None
    LinearConstraint = None
    milp = None
    MILP_AVAILABLE = False


LOS_SOLVER_VERSION = "los-segment-cover-v2-pairwise"
WGS84_3D = CRS.from_epsg(4979)
WGS84_2D = CRS.from_epsg(4326)
ECEF_CRS = CRS.from_epsg(4978)
GEODETIC_TO_ECEF = Transformer.from_crs(WGS84_3D, ECEF_CRS, always_xy=True)
ECEF_TO_GEODETIC = Transformer.from_crs(ECEF_CRS, WGS84_3D, always_xy=True)


@dataclass(frozen=True)
class OutputSettings:
    crs: CRS
    crs_authid: str
    epsg: int
    unit: str
    unit_suffix: str
    unit_factor: float
    transformer: Transformer


def normalize_length_unit(unit: str) -> tuple[str, str, float]:
    normalized = unit.strip().lower()
    if normalized in {"meters", "meter", "metres", "metre", "m"}:
        return "meters", "m", 1.0
    if normalized in {"feet", "foot", "ft"}:
        return "feet", "ft", FEET_PER_METER
    raise ValueError(f"Unsupported length unit: {unit}")


def length_to_meters(value: float, unit: str) -> float:
    normalized, _suffix, _factor = normalize_length_unit(unit)
    if normalized == "meters":
        return float(value)
    return float(value) / FEET_PER_METER


def normalize_output_crs(output_crs: str) -> tuple[CRS, str, int]:
    normalized = output_crs.strip().upper()
    aliases = {
        "WGS84": "EPSG:4326",
        "WGS 84": "EPSG:4326",
        "EPSG:4326": "EPSG:4326",
        "4326": "EPSG:4326",
        "NAD83": "EPSG:4269",
        "NAD 83": "EPSG:4269",
        "EPSG:4269": "EPSG:4269",
        "4269": "EPSG:4269",
    }
    crs = CRS.from_user_input(aliases.get(normalized, output_crs))
    if not crs.is_geographic:
        raise ValueError(
            "los-cover currently supports only geographic output CRS values "
            "such as WGS84/EPSG:4326 or NAD83/EPSG:4269."
        )
    epsg = crs.to_epsg()
    if epsg is None:
        raise ValueError(f"Output CRS must resolve to an EPSG code: {output_crs}")
    return crs, f"EPSG:{epsg}", int(epsg)


def build_output_settings(output_crs: str, output_unit: str) -> OutputSettings:
    unit, suffix, factor = normalize_length_unit(output_unit)
    crs, authid, epsg = normalize_output_crs(output_crs)
    transformer = Transformer.from_crs(WGS84_2D, crs, always_xy=True)
    return OutputSettings(
        crs=crs,
        crs_authid=authid,
        epsg=epsg,
        unit=unit,
        unit_suffix=suffix,
        unit_factor=factor,
        transformer=transformer,
    )


@dataclass(frozen=True)
class PreparedPoint:
    point_id: str
    lat: float
    lon: float
    source_index: int
    ground_alt_m: float
    absolute_alt_m: float
    has_altitude: bool
    altitude_source: str


@dataclass(frozen=True)
class ResolvedPoint:
    point_id: str
    lat: float
    lon: float
    source_index: int
    ground_alt_m: float
    absolute_alt_m: float
    display_alt_m: float
    offset_m: float
    altitude_source: str
    ecef: tuple[float, float, float]


@dataclass(frozen=True)
class LosCheck:
    visible: bool
    min_clearance_m: float
    sample_count: int
    blocked_fraction: Optional[float]


@dataclass
class CandidateSegment:
    candidate_id: str
    anchor_index: int
    anchor_id: str
    coverage_mask: int
    coverage_count: int
    residual_sum_m: float
    segment_length_m: float
    endpoint_index: Optional[int] = None
    endpoint_id: str = ""
    anchor_lon: float = 0.0
    anchor_lat: float = 0.0
    anchor_display_alt_m: float = 0.0
    endpoint_lon: float = 0.0
    endpoint_lat: float = 0.0
    endpoint_ground_alt_m: float = 0.0
    endpoint_display_alt_m: float = 0.0
    surface_distance_m: float = 0.0
    azimuth_deg: Optional[float] = None
    visible_point_mask: int = 0
    point_distances_m: dict[int, float] = field(default_factory=dict)
    projection_fractions: dict[int, float] = field(default_factory=dict)
    generation_kind: str = "radial"
    min_clearance_m: Optional[float] = None

    @property
    def residual_mean_m(self) -> float:
        if self.coverage_count <= 0:
            return 0.0
        return self.residual_sum_m / self.coverage_count

    @property
    def is_meaningful(self) -> bool:
        return self.coverage_count >= 3


@dataclass(frozen=True)
class CandidateBuildStats:
    raw_pair_count: int
    deduped_family_count: int
    meaningful_family_count: int
    reachable_mask: int


@dataclass(frozen=True)
class SelectionSummary:
    selected_candidates: list[CandidateSegment]
    reachable_mask: int
    exact_pool_ids: set[str]
    exact_status: str
    stage: str


class ElevationProvider:
    def sample_ground_m(self, lon: float, lat: float) -> float:  # pragma: no cover - interface
        raise NotImplementedError


class UnitScaledElevationProvider(ElevationProvider):
    def __init__(self, provider: ElevationProvider, *, source_unit: str):
        self.provider = provider
        self.source_unit, _suffix, _factor = normalize_length_unit(source_unit)

    def sample_ground_m(self, lon: float, lat: float) -> float:
        value = self.provider.sample_ground_m(lon, lat)
        if self.source_unit == "feet":
            return value / FEET_PER_METER
        return value


class GDALDemSampler(ElevationProvider):
    def __init__(self, dem_path: Path):
        try:
            from osgeo import gdal
            from pyproj import CRS as LocalCRS, Transformer as LocalTransformer
        except Exception as exc:  # pragma: no cover - exercised via factory
            raise RuntimeError(
                "GDAL-backed DEM sampling is unavailable in this Python runtime."
            ) from exc

        gdal.UseExceptions()
        self.path = dem_path.resolve()
        self.dataset = gdal.Open(str(self.path))
        if self.dataset is None:
            raise RuntimeError(f"Could not open DEM: {self.path}")

        self.band = self.dataset.GetRasterBand(1)
        self.nodata = self.band.GetNoDataValue()
        self.geotransform = self.dataset.GetGeoTransform()
        projection = self.dataset.GetProjection()
        self.crs = LocalCRS.from_wkt(projection) if projection else LocalCRS.from_epsg(4326)
        self.transformer = LocalTransformer.from_crs("EPSG:4326", self.crs, always_xy=True)

        gt = self.geotransform
        if abs(gt[2]) > 1e-12 or abs(gt[4]) > 1e-12:
            raise RuntimeError(
                "DEM has rotated geotransform; LOS segment cover expects north-up rasters."
            )

    def sample_ground_m(self, lon: float, lat: float) -> float:
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


class RasterioDemSampler(ElevationProvider):
    def __init__(self, dem_path: Path):
        try:
            import rasterio
            from pyproj import CRS as LocalCRS, Transformer as LocalTransformer
        except Exception as exc:  # pragma: no cover - exercised via factory
            raise RuntimeError(
                "Rasterio-backed DEM sampling is unavailable in this Python runtime."
            ) from exc

        self.path = dem_path.resolve()
        self.dataset = rasterio.open(self.path)
        self.nodata = self.dataset.nodata
        raster_crs = self.dataset.crs
        self.crs = LocalCRS.from_user_input(raster_crs) if raster_crs else LocalCRS.from_epsg(4326)
        self.transformer = LocalTransformer.from_crs("EPSG:4326", self.crs, always_xy=True)

        affine = self.dataset.transform
        if abs(affine.b) > 1e-12 or abs(affine.d) > 1e-12:
            raise RuntimeError(
                "DEM has rotated geotransform; LOS segment cover expects north-up rasters."
            )

    def sample_ground_m(self, lon: float, lat: float) -> float:
        x, y = self.transformer.transform(lon, lat)
        row, col = self.dataset.index(x, y)

        if col < 0 or row < 0 or col >= self.dataset.width or row >= self.dataset.height:
            raise ValueError(f"Point outside DEM extent: lon={lon}, lat={lat}")

        array = self.dataset.read(1, window=((row, row + 1), (col, col + 1)), masked=True)
        value = array[0][0]
        if np.ma.is_masked(value):
            raise ValueError(f"DEM nodata at lon={lon}, lat={lat}")
        numeric_value = float(value)
        if self.nodata is not None and numeric_value == self.nodata:
            raise ValueError(f"DEM nodata at lon={lon}, lat={lat}")
        if math.isnan(numeric_value):
            raise ValueError(f"DEM nodata at lon={lon}, lat={lat}")
        return numeric_value


def has_rasterio_backend() -> bool:
    try:
        import rasterio as _rasterio  # noqa: F401
    except Exception:
        return False
    return True


def has_gdal_backend() -> bool:
    try:
        from osgeo import gdal as _gdal  # noqa: F401
    except Exception:
        return False
    return True


def has_dem_backend() -> bool:
    return has_rasterio_backend() or has_gdal_backend()


def create_elevation_provider(dem_path: str | Path, repo_root: Path) -> ElevationProvider:
    path = resolve_input_path(str(dem_path), repo_root)
    if has_rasterio_backend():
        return RasterioDemSampler(path)
    if has_gdal_backend():
        return GDALDemSampler(path)
    if not has_dem_backend():
        raise RuntimeError(
            "No local DEM sampler is available in this Python runtime. "
            "Install rasterio for normal Python runs, or run `los-cover` through the "
            "OSGeo4W QGIS runtime (for example via `cvr.bat`)."
        )
    raise RuntimeError("No local DEM sampler is available in this Python runtime.")


def geodetic_to_ecef(lon: float, lat: float, alt_m: float) -> tuple[float, float, float]:
    x, y, z = GEODETIC_TO_ECEF.transform(lon, lat, alt_m)
    return float(x), float(y), float(z)


def ecef_to_geodetic(x: float, y: float, z: float) -> tuple[float, float, float]:
    lon, lat, alt_m = ECEF_TO_GEODETIC.transform(x, y, z)
    return float(lon), float(lat), float(alt_m)


def chord_waypoint(
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    fraction: float,
) -> tuple[float, float, float]:
    sx, sy, sz = geodetic_to_ecef(*start)
    ex, ey, ez = geodetic_to_ecef(*end)
    x = sx + ((ex - sx) * fraction)
    y = sy + ((ey - sy) * fraction)
    z = sz + ((ez - sz) * fraction)
    return ecef_to_geodetic(x, y, z)


def geodesic_linear_waypoint(
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    fraction: float,
) -> tuple[float, float, float]:
    start_lon, start_lat, start_alt = start
    end_lon, end_lat, end_alt = end
    az12, _, geodesic_distance_m = WGS84.inv(start_lon, start_lat, end_lon, end_lat)
    lon, lat, _ = WGS84.fwd(start_lon, start_lat, az12, geodesic_distance_m * fraction)
    alt_m = start_alt + ((end_alt - start_alt) * fraction)
    return float(lon), float(lat), float(alt_m)


def point_to_segment_distance_m(
    point_xyz: tuple[float, float, float],
    start_xyz: tuple[float, float, float],
    end_xyz: tuple[float, float, float],
) -> tuple[float, float]:
    px = np.asarray(point_xyz, dtype=float)
    sx = np.asarray(start_xyz, dtype=float)
    ex = np.asarray(end_xyz, dtype=float)
    segment = ex - sx
    seg_len_sq = float(np.dot(segment, segment))
    if seg_len_sq <= 1e-12:
        return float(np.linalg.norm(px - sx)), 0.0

    projection = float(np.dot(px - sx, segment) / seg_len_sq)
    projection = max(0.0, min(1.0, projection))
    nearest = sx + (segment * projection)
    return float(np.linalg.norm(px - nearest)), projection


def chord_los_clear(
    start_xyz: tuple[float, float, float],
    end_xyz: tuple[float, float, float],
    provider: ElevationProvider,
    *,
    sample_step_m: float,
    start_ground_m: Optional[float] = None,
    end_ground_m: Optional[float] = None,
) -> LosCheck:
    if sample_step_m <= 0:
        raise ValueError("sample_step_m must be positive")

    start_vec = np.asarray(start_xyz, dtype=float)
    end_vec = np.asarray(end_xyz, dtype=float)
    chord_length_m = float(np.linalg.norm(end_vec - start_vec))

    if chord_length_m <= 1e-9:
        lon, lat, alt_m = ecef_to_geodetic(*start_xyz)
        ground_m = start_ground_m if start_ground_m is not None else provider.sample_ground_m(lon, lat)
        return LosCheck(True, float(alt_m - ground_m), 2, None)

    step_count = max(1, int(math.ceil(chord_length_m / sample_step_m)))
    start_lon, start_lat, start_alt_m = ecef_to_geodetic(*start_xyz)
    end_lon, end_lat, end_alt_m = ecef_to_geodetic(*end_xyz)
    start_clearance = start_alt_m - (
        start_ground_m if start_ground_m is not None else provider.sample_ground_m(start_lon, start_lat)
    )
    end_clearance = end_alt_m - (
        end_ground_m if end_ground_m is not None else provider.sample_ground_m(end_lon, end_lat)
    )
    min_clearance_m = min(start_clearance, end_clearance)

    for sample_index in range(1, step_count):
        fraction = sample_index / step_count
        sample = start_vec + ((end_vec - start_vec) * fraction)
        lon, lat, alt_m = ecef_to_geodetic(*sample)
        ground_m = provider.sample_ground_m(lon, lat)
        clearance_m = alt_m - ground_m
        if clearance_m < min_clearance_m:
            min_clearance_m = clearance_m
        if clearance_m <= 0.0:
            return LosCheck(False, float(min_clearance_m), step_count + 1, fraction)

    return LosCheck(True, float(min_clearance_m), step_count + 1, None)


def prepare_points(dataset: Dataset, provider: ElevationProvider) -> list[PreparedPoint]:
    prepared: list[PreparedPoint] = []
    for point in dataset.points:
        ground_alt_m = provider.sample_ground_m(point.lon, point.lat)
        absolute_alt_m = point.alt_m if point.has_altitude else ground_alt_m
        prepared.append(
            PreparedPoint(
                point_id=point.point_id,
                lat=point.lat,
                lon=point.lon,
                source_index=point.source_index,
                ground_alt_m=ground_alt_m,
                absolute_alt_m=absolute_alt_m,
                has_altitude=point.has_altitude,
                altitude_source="dataset" if point.has_altitude else "dem",
            )
        )
    return prepared


def resolve_points(prepared_points: Iterable[PreparedPoint], *, offset_m: float) -> list[ResolvedPoint]:
    resolved: list[ResolvedPoint] = []
    for point in prepared_points:
        display_alt_m = point.absolute_alt_m + offset_m
        resolved.append(
            ResolvedPoint(
                point_id=point.point_id,
                lat=point.lat,
                lon=point.lon,
                source_index=point.source_index,
                ground_alt_m=point.ground_alt_m,
                absolute_alt_m=point.absolute_alt_m,
                display_alt_m=display_alt_m,
                offset_m=offset_m,
                altitude_source=point.altitude_source,
                ecef=geodetic_to_ecef(point.lon, point.lat, display_alt_m),
            )
        )
    return resolved


def _mask_from_indexes(indexes: Iterable[int]) -> int:
    mask = 0
    for index in indexes:
        mask |= 1 << index
    return mask


def _mask_indexes(mask: int, point_count: int) -> list[int]:
    return [index for index in range(point_count) if mask & (1 << index)]


def _mask_to_point_ids(mask: int, points: list[PreparedPoint]) -> list[str]:
    return [points[index].point_id for index in _mask_indexes(mask, len(points))]


def _candidate_sort_key(candidate: CandidateSegment) -> tuple[float, float, float, str]:
    return (
        round(candidate.residual_sum_m, 6),
        -round(_covered_endpoint_separation_m(candidate), 6),
        -round(candidate.segment_length_m, 6),
        candidate.candidate_id,
    )


def _covered_endpoint_separation_m(candidate: CandidateSegment) -> float:
    """Return how far apart the representative endpoints are within the family.

    The current LOS model creates candidates only from original point pairs, and
    those endpoints are always members of the candidate's covered set. For line
    families with the same covered mask, preferring the largest endpoint
    separation keeps the visible representative closer to the family's full
    extent. This helper keeps that intent separate from generic segment length
    in case future candidate types need a richer covered-point span metric.
    """

    if candidate.endpoint_index is None:
        return 0.0
    anchor_bit = 1 << candidate.anchor_index
    endpoint_bit = 1 << candidate.endpoint_index
    if candidate.coverage_mask & anchor_bit and candidate.coverage_mask & endpoint_bit:
        return candidate.segment_length_m
    return 0.0


def _build_candidate(
    *,
    candidate_id: str,
    anchor_index: int,
    anchor: ResolvedPoint,
    cover_points: list[ResolvedPoint],
    endpoint_index: int,
    endpoint: ResolvedPoint,
    endpoint_lon: float,
    endpoint_lat: float,
    endpoint_ground_alt_m: float,
    endpoint_display_alt_m: float,
    surface_distance_m: float,
    generation_kind: str,
    min_clearance_m: Optional[float],
    line_tolerance_m: float,
) -> CandidateSegment:
    end_xyz = geodetic_to_ecef(endpoint_lon, endpoint_lat, endpoint_display_alt_m)
    point_distances_m: dict[int, float] = {}
    projection_fractions: dict[int, float] = {}
    coverage_mask = 0

    for point_index, point in enumerate(cover_points):
        point = cover_points[point_index]
        residual_m, fraction = point_to_segment_distance_m(point.ecef, anchor.ecef, end_xyz)
        if residual_m <= line_tolerance_m:
            coverage_mask |= 1 << point_index
            point_distances_m[point_index] = residual_m
            projection_fractions[point_index] = fraction

    coverage_count = coverage_mask.bit_count()
    return CandidateSegment(
        candidate_id=candidate_id,
        anchor_index=anchor_index,
        anchor_id=anchor.point_id,
        endpoint_index=endpoint_index,
        endpoint_id=endpoint.point_id,
        coverage_mask=coverage_mask,
        coverage_count=coverage_count,
        residual_sum_m=float(sum(point_distances_m.values())),
        segment_length_m=float(np.linalg.norm(np.asarray(end_xyz) - np.asarray(anchor.ecef))),
        anchor_lon=anchor.lon,
        anchor_lat=anchor.lat,
        anchor_display_alt_m=anchor.display_alt_m,
        endpoint_lon=endpoint_lon,
        endpoint_lat=endpoint_lat,
        endpoint_ground_alt_m=endpoint_ground_alt_m,
        endpoint_display_alt_m=endpoint_display_alt_m,
        surface_distance_m=surface_distance_m,
        azimuth_deg=None,
        visible_point_mask=coverage_mask,
        point_distances_m=point_distances_m,
        projection_fractions=projection_fractions,
        generation_kind=generation_kind,
        min_clearance_m=min_clearance_m,
    )


def deduplicate_family_candidates(
    candidates: list[CandidateSegment],
) -> list[CandidateSegment]:
    best_by_mask: dict[int, CandidateSegment] = {}

    for candidate in candidates:
        if candidate.coverage_count < 2:
            continue
        current = best_by_mask.get(candidate.coverage_mask)
        if current is None or _candidate_sort_key(candidate) < _candidate_sort_key(current):
            best_by_mask[candidate.coverage_mask] = candidate

    families = [
        replace(candidate, generation_kind="pair-family")
        for candidate in best_by_mask.values()
    ]
    return sorted(families, key=lambda item: item.candidate_id)


def deduplicate_anchor_candidates(
    candidates: list[CandidateSegment],
    *,
    anchor_index: int,
) -> list[CandidateSegment]:
    return deduplicate_family_candidates(
        [candidate for candidate in candidates if candidate.anchor_index == anchor_index]
    )


def generate_candidates(
    prepared_points: list[PreparedPoint],
    provider: ElevationProvider,
    *,
    point_height_m: float,
    max_segment_length_m: Optional[float],
    line_tolerance_m: float,
    sample_step_m: float,
) -> tuple[list[CandidateSegment], list[CandidateSegment], CandidateBuildStats]:
    pair_points = resolve_points(prepared_points, offset_m=point_height_m)
    raw_pair_candidates: list[CandidateSegment] = []
    reachable_mask = 0

    for anchor_index, anchor in enumerate(pair_points):
        for endpoint_index in range(anchor_index + 1, len(pair_points)):
            endpoint = pair_points[endpoint_index]
            _, _, surface_distance_m = WGS84.inv(anchor.lon, anchor.lat, endpoint.lon, endpoint.lat)
            if max_segment_length_m is not None and surface_distance_m > max_segment_length_m:
                continue

            los_check = chord_los_clear(
                anchor.ecef,
                endpoint.ecef,
                provider,
                sample_step_m=sample_step_m,
                start_ground_m=prepared_points[anchor_index].ground_alt_m,
                end_ground_m=prepared_points[endpoint_index].ground_alt_m,
            )

            if not los_check.visible:
                continue

            candidate = _build_candidate(
                candidate_id=f"{anchor.point_id}__{endpoint.point_id}",
                anchor_index=anchor_index,
                anchor=anchor,
                cover_points=pair_points,
                endpoint_index=endpoint_index,
                endpoint=endpoint,
                endpoint_lon=endpoint.lon,
                endpoint_lat=endpoint.lat,
                endpoint_ground_alt_m=prepared_points[endpoint_index].ground_alt_m,
                endpoint_display_alt_m=endpoint.display_alt_m,
                surface_distance_m=float(surface_distance_m),
                generation_kind="pair",
                min_clearance_m=los_check.min_clearance_m,
                line_tolerance_m=line_tolerance_m,
            )
            if candidate.coverage_count < 2:
                continue
            raw_pair_candidates.append(candidate)
            reachable_mask |= candidate.coverage_mask

    family_candidates = deduplicate_family_candidates(raw_pair_candidates)
    meaningful_family_count = sum(1 for candidate in family_candidates if candidate.is_meaningful)

    return raw_pair_candidates, family_candidates, CandidateBuildStats(
        raw_pair_count=len(raw_pair_candidates),
        deduped_family_count=len(family_candidates),
        meaningful_family_count=meaningful_family_count,
        reachable_mask=reachable_mask,
    )


def _selection_assignment_metrics(
    selected_candidates: list[CandidateSegment],
    point_count: int,
) -> tuple[int, float]:
    coverage_mask = 0
    residual_total = 0.0
    for point_index in range(point_count):
        best_residual: Optional[float] = None
        for candidate in selected_candidates:
            if not (candidate.coverage_mask & (1 << point_index)):
                continue
            residual_m = candidate.point_distances_m[point_index]
            if best_residual is None or residual_m < best_residual:
                best_residual = residual_m
        if best_residual is None:
            continue
        coverage_mask |= 1 << point_index
        residual_total += best_residual
    return coverage_mask, residual_total


def _selection_objective(
    selected_candidates: list[CandidateSegment],
    point_count: int,
    target_mask: int,
) -> tuple[int, float, float]:
    coverage_mask, residual_total = _selection_assignment_metrics(selected_candidates, point_count)
    uncovered_count = (target_mask & ~coverage_mask).bit_count()
    if uncovered_count:
        residual_total += uncovered_count * 1_000_000.0
    total_length = sum(candidate.segment_length_m for candidate in selected_candidates)
    return len(selected_candidates), residual_total, total_length


def greedy_select_candidates(
    candidates: list[CandidateSegment],
    *,
    point_count: int,
    target_mask: int,
) -> list[CandidateSegment]:
    uncovered_mask = target_mask
    available = list(candidates)
    selected: list[CandidateSegment] = []

    while uncovered_mask:
        best_candidate: Optional[CandidateSegment] = None
        best_key: Optional[tuple[int, float, float, str]] = None
        for candidate in available:
            new_mask = candidate.coverage_mask & uncovered_mask
            if new_mask == 0:
                continue
            new_count = new_mask.bit_count()
            new_residual = sum(
                candidate.point_distances_m[index]
                for index in _mask_indexes(new_mask, point_count)
            )
            candidate_key = (
                new_count,
                -new_residual,
                -candidate.segment_length_m,
                candidate.candidate_id,
            )
            if best_key is None or candidate_key > best_key:
                best_candidate = candidate
                best_key = candidate_key

        if best_candidate is None:
            break

        selected.append(best_candidate)
        uncovered_mask &= ~best_candidate.coverage_mask
        available = [
            candidate
            for candidate in available
            if candidate.candidate_id != best_candidate.candidate_id
        ]

    return selected


def drop_redundant_candidates(
    selected_candidates: list[CandidateSegment],
    *,
    point_count: int,
    target_mask: int,
) -> list[CandidateSegment]:
    refined = list(selected_candidates)
    changed = True
    while changed:
        changed = False
        for candidate in list(refined):
            proposal = [
                item
                for item in refined
                if item.candidate_id != candidate.candidate_id
            ]
            coverage_mask, _ = _selection_assignment_metrics(proposal, point_count)
            if (coverage_mask & target_mask) == target_mask:
                refined = proposal
                changed = True
                break
    return refined


def _try_two_for_one_collapse(
    selected_candidates: list[CandidateSegment],
    all_candidates: list[CandidateSegment],
    *,
    point_count: int,
    target_mask: int,
) -> list[CandidateSegment]:
    improved = list(selected_candidates)

    while True:
        selected_ids = {candidate.candidate_id for candidate in improved}
        best_replacement: Optional[list[CandidateSegment]] = None
        best_objective = _selection_objective(improved, point_count, target_mask)

        for left_index in range(len(improved)):
            for right_index in range(left_index + 1, len(improved)):
                keep = [
                    candidate
                    for idx, candidate in enumerate(improved)
                    if idx not in (left_index, right_index)
                ]
                keep_mask, _ = _selection_assignment_metrics(keep, point_count)
                missing_mask = target_mask & ~keep_mask
                if missing_mask == 0:
                    continue

                for candidate in all_candidates:
                    if candidate.candidate_id in selected_ids:
                        continue
                    if (candidate.coverage_mask & missing_mask) != missing_mask:
                        continue
                    proposal = keep + [candidate]
                    objective = _selection_objective(proposal, point_count, target_mask)
                    if objective < best_objective:
                        best_objective = objective
                        best_replacement = proposal

        if best_replacement is None:
            return improved

        improved = drop_redundant_candidates(
            best_replacement,
            point_count=point_count,
            target_mask=target_mask,
        )


def _try_equal_cardinality_improvement(
    selected_candidates: list[CandidateSegment],
    all_candidates: list[CandidateSegment],
    *,
    point_count: int,
    target_mask: int,
) -> list[CandidateSegment]:
    improved = list(selected_candidates)
    current_objective = _selection_objective(improved, point_count, target_mask)

    while True:
        selected_ids = {candidate.candidate_id for candidate in improved}
        best_replacement: Optional[list[CandidateSegment]] = None
        best_objective = current_objective

        for selected_index, _selected_candidate in enumerate(improved):
            keep = [
                candidate
                for idx, candidate in enumerate(improved)
                if idx != selected_index
            ]
            keep_mask, _ = _selection_assignment_metrics(keep, point_count)
            missing_mask = target_mask & ~keep_mask
            if missing_mask == 0:
                continue

            for candidate in all_candidates:
                if candidate.candidate_id in selected_ids:
                    continue
                if (candidate.coverage_mask & missing_mask) != missing_mask:
                    continue
                proposal = keep + [candidate]
                objective = _selection_objective(proposal, point_count, target_mask)
                if objective < best_objective:
                    best_objective = objective
                    best_replacement = proposal

        if best_replacement is None:
            return improved

        improved = best_replacement
        current_objective = best_objective


def build_exact_refinement_pool(
    candidates: list[CandidateSegment],
    selected_candidates: list[CandidateSegment],
    *,
    point_count: int,
    top_k_per_point: int = 15,
) -> list[CandidateSegment]:
    pool_by_id = {candidate.candidate_id: candidate for candidate in selected_candidates}

    for point_index in range(point_count):
        covering_candidates = [
            candidate
            for candidate in candidates
            if candidate.coverage_mask & (1 << point_index)
        ]
        covering_candidates.sort(
            key=lambda candidate: (
                round(candidate.point_distances_m[point_index], 6),
                round(candidate.segment_length_m, 6),
                candidate.candidate_id,
            )
        )
        for candidate in covering_candidates[:top_k_per_point]:
            pool_by_id[candidate.candidate_id] = candidate

    return sorted(pool_by_id.values(), key=lambda item: item.candidate_id)


def run_exact_refinement(
    candidates: list[CandidateSegment],
    *,
    point_count: int,
    target_mask: int,
) -> Optional[list[CandidateSegment]]:
    if not MILP_AVAILABLE or milp is None or Bounds is None or LinearConstraint is None:
        return None
    if not candidates:
        return []

    point_indexes = _mask_indexes(target_mask, point_count)
    if not point_indexes:
        return []

    incidence = np.zeros((len(point_indexes), len(candidates)), dtype=float)
    for row_index, point_index in enumerate(point_indexes):
        for col_index, candidate in enumerate(candidates):
            if candidate.coverage_mask & (1 << point_index):
                incidence[row_index, col_index] = 1.0

    max_length = max((candidate.segment_length_m for candidate in candidates), default=1.0)
    objective = np.array(
        [
            1.0 + ((candidate.segment_length_m / max(max_length, 1.0)) * 1e-4)
            for candidate in candidates
        ],
        dtype=float,
    )
    bounds = Bounds(lb=np.zeros(len(candidates)), ub=np.ones(len(candidates)))
    constraints = LinearConstraint(
        incidence,
        lb=np.ones(len(point_indexes)),
        ub=np.full(len(point_indexes), np.inf),
    )
    result = milp(
        c=objective,
        integrality=np.ones(len(candidates), dtype=int),
        bounds=bounds,
        constraints=constraints,
    )
    if not getattr(result, "success", False):
        return None

    selected_indexes = [index for index, value in enumerate(result.x) if value >= 0.5]
    return [candidates[index] for index in selected_indexes]


def networkx_select_candidates(
    candidates: list[CandidateSegment],
    *,
    point_count: int,
    target_mask: int,
) -> list[CandidateSegment]:
    target_indexes = _mask_indexes(target_mask, point_count)
    candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    cover_candidates = [
        CoverCandidate(
            candidate_id=candidate.candidate_id,
            covered_ids=frozenset(
                str(index)
                for index in _mask_indexes(candidate.coverage_mask & target_mask, point_count)
            ),
            weight=_networkx_candidate_weight(candidate),
            sort_key=(
                -candidate.coverage_count,
                round(candidate.residual_sum_m, 6),
                round(candidate.segment_length_m, 6),
                candidate.candidate_id,
            ),
        )
        for candidate in candidates
        if candidate.coverage_mask & target_mask
    ]
    solution = solve_networkx_full_cover(
        (str(index) for index in target_indexes),
        cover_candidates,
    )
    selected = [
        candidate_by_id[candidate_id]
        for candidate_id in solution.selected_ids
        if candidate_id in candidate_by_id
    ]
    return drop_redundant_candidates(
        selected,
        point_count=point_count,
        target_mask=target_mask,
    )


def rank_selected_candidates(
    selected_candidates: list[CandidateSegment],
    *,
    point_count: int,
    target_mask: int,
) -> list[dict[str, Any]]:
    remaining = list(selected_candidates)
    uncovered_mask = target_mask
    rows: list[dict[str, Any]] = []
    rank = 1

    while remaining:
        best_candidate: Optional[CandidateSegment] = None
        best_key: Optional[tuple[int, float, float, str]] = None
        for candidate in remaining:
            new_mask = candidate.coverage_mask & uncovered_mask
            new_count = new_mask.bit_count()
            candidate_key = (
                new_count,
                -candidate.residual_sum_m,
                -candidate.segment_length_m,
                candidate.candidate_id,
            )
            if best_key is None or candidate_key > best_key:
                best_candidate = candidate
                best_key = candidate_key

        if best_candidate is None:
            break

        newly_covered_mask = best_candidate.coverage_mask & uncovered_mask
        uncovered_mask &= ~best_candidate.coverage_mask
        rows.append(
            {
                "rank": rank,
                "candidate_id": best_candidate.candidate_id,
                "anchor_id": best_candidate.anchor_id,
                "endpoint_id": best_candidate.endpoint_id,
                "coverage_count": best_candidate.coverage_count,
                "meaningful": 1 if best_candidate.is_meaningful else 0,
                "newly_covered_count": newly_covered_mask.bit_count(),
                "total_covered_count": target_mask.bit_count() - uncovered_mask.bit_count(),
                "remaining_uncovered_count": uncovered_mask.bit_count(),
            }
        )
        remaining = [
            candidate
            for candidate in remaining
            if candidate.candidate_id != best_candidate.candidate_id
        ]
        rank += 1

    return rows


def solve_candidate_cover(
    candidates: list[CandidateSegment],
    point_count: int,
    *,
    solver: str = "hybrid",
    reachable_mask: Optional[int] = None,
) -> SelectionSummary:
    target_mask = reachable_mask if reachable_mask is not None else 0
    if reachable_mask is None:
        for candidate in candidates:
            target_mask |= candidate.coverage_mask

    if solver == "networkx":
        selected = networkx_select_candidates(
            candidates,
            point_count=point_count,
            target_mask=target_mask,
        )
        return SelectionSummary(selected, target_mask, set(), "not-requested", "networkx")

    selected = greedy_select_candidates(candidates, point_count=point_count, target_mask=target_mask)
    selected = drop_redundant_candidates(selected, point_count=point_count, target_mask=target_mask)
    stage = "greedy"
    exact_pool_ids: set[str] = set()
    exact_status = "not-requested"

    if solver == "greedy":
        return SelectionSummary(selected, target_mask, exact_pool_ids, exact_status, stage)
    if solver != "hybrid":
        raise ValueError(f"Unsupported solver: {solver}")

    selected = _try_two_for_one_collapse(
        selected,
        candidates,
        point_count=point_count,
        target_mask=target_mask,
    )
    selected = drop_redundant_candidates(selected, point_count=point_count, target_mask=target_mask)
    selected = _try_equal_cardinality_improvement(
        selected,
        candidates,
        point_count=point_count,
        target_mask=target_mask,
    )
    selected = drop_redundant_candidates(selected, point_count=point_count, target_mask=target_mask)
    stage = "local-search"

    exact_pool = build_exact_refinement_pool(candidates, selected, point_count=point_count, top_k_per_point=15)
    exact_pool_ids = {candidate.candidate_id for candidate in exact_pool}
    if len(exact_pool) > 500:
        return SelectionSummary(selected, target_mask, exact_pool_ids, "skipped-pool-limit", stage)

    exact_candidate_selection = run_exact_refinement(
        exact_pool,
        point_count=point_count,
        target_mask=target_mask,
    )
    if exact_candidate_selection is None:
        exact_status = "skipped-no-scipy" if not MILP_AVAILABLE else "skipped-no-improvement"
        return SelectionSummary(selected, target_mask, exact_pool_ids, exact_status, stage)

    exact_candidate_selection = drop_redundant_candidates(
        exact_candidate_selection,
        point_count=point_count,
        target_mask=target_mask,
    )
    current_objective = _selection_objective(selected, point_count, target_mask)
    exact_objective = _selection_objective(exact_candidate_selection, point_count, target_mask)
    if exact_objective < current_objective:
        return SelectionSummary(
            exact_candidate_selection,
            target_mask,
            exact_pool_ids,
            "applied",
            "exact-refined",
        )

    return SelectionSummary(selected, target_mask, exact_pool_ids, "no-better-solution", stage)


def _networkx_candidate_weight(candidate: CandidateSegment) -> float:
    coverage_bonus = 1e-6 * candidate.coverage_count
    residual_tiebreaker = min(candidate.residual_sum_m, 1_000_000.0) * 1e-12
    length_tiebreaker = min(candidate.segment_length_m, 1_000_000.0) * 1e-12
    return 1.0 - coverage_bonus + residual_tiebreaker + length_tiebreaker


def _projected_point_from_candidate(
    candidate: CandidateSegment,
    point_index: int,
) -> tuple[float, float, float]:
    fraction = candidate.projection_fractions[point_index]
    return chord_waypoint(
        (candidate.anchor_lon, candidate.anchor_lat, candidate.anchor_display_alt_m),
        (candidate.endpoint_lon, candidate.endpoint_lat, candidate.endpoint_display_alt_m),
        fraction,
    )


def _feature(geometry: dict[str, Any], properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "geometry": geometry, "properties": properties}


def _feature_collection(features: list[dict[str, Any]], settings: OutputSettings) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "crs": {
            "type": "name",
            "properties": {"name": settings.crs_authid},
        },
        "features": features,
    }


def write_geojson(path: Path, features: list[dict[str, Any]], settings: OutputSettings) -> None:
    ensure_parent_dir(path)
    path.write_text(json.dumps(_feature_collection(features, settings), indent=2), encoding="utf-8")


def _output_xy(lon: float, lat: float, settings: OutputSettings) -> tuple[float, float]:
    x, y = settings.transformer.transform(lon, lat)
    return float(x), float(y)


def _rounded_xyz(lon: float, lat: float, z_m: float, settings: OutputSettings) -> list[float]:
    x, y = _output_xy(lon, lat, settings)
    return [round(x, 9), round(y, 9), round(z_m, 3)]


def _rounded_length(value_m: Optional[float], settings: OutputSettings) -> Optional[float]:
    if value_m is None:
        return None
    return round(float(value_m) * settings.unit_factor, 6)


def _length_fields(base_name: str, value_m: Optional[float], settings: OutputSettings) -> dict[str, Any]:
    fields: dict[str, Any] = {
        f"{base_name}_{settings.unit_suffix}": _rounded_length(value_m, settings)
    }
    if settings.unit_suffix != "m":
        fields[f"{base_name}_m"] = None if value_m is None else round(float(value_m), 6)
    return fields


def _coverage_point_ids(candidate: CandidateSegment, points: list[PreparedPoint]) -> list[str]:
    return _mask_to_point_ids(candidate.coverage_mask, points)


def _candidate_summary_rows(
    candidates: list[CandidateSegment],
    *,
    points: list[PreparedPoint],
    selected_ids: set[str],
    exact_pool_ids: set[str],
    settings: OutputSettings,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        row: dict[str, Any] = {
            "candidate_id": candidate.candidate_id,
            "anchor_id": candidate.anchor_id,
            "endpoint_id": candidate.endpoint_id,
            "generation_kind": candidate.generation_kind,
        }
        row.update(_length_fields("surface_distance", candidate.surface_distance_m, settings))
        row.update(_length_fields("segment_length", candidate.segment_length_m, settings))
        row.update(
            {
                "coverage_count": candidate.coverage_count,
                "meaningful": 1 if candidate.is_meaningful else 0,
            }
        )
        row.update(_length_fields("residual_sum", candidate.residual_sum_m, settings))
        row.update(_length_fields("residual_mean", candidate.residual_mean_m, settings))
        row.update(
            {
                "covered_point_ids": "|".join(_coverage_point_ids(candidate, points)),
                "selected": 1 if candidate.candidate_id in selected_ids else 0,
                "in_exact_pool": 1 if candidate.candidate_id in exact_pool_ids else 0,
            }
        )
        row.update(_length_fields("min_clearance", candidate.min_clearance_m, settings))
        rows.append(row)
    return rows


def _candidate_line_features(
    candidates: list[CandidateSegment],
    *,
    points: list[PreparedPoint],
    settings: OutputSettings,
    selected_ids: Optional[set[str]] = None,
    rank_by_candidate_id: Optional[dict[str, int]] = None,
) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    selected_ids = selected_ids or set()
    rank_by_candidate_id = rank_by_candidate_id or {}

    for candidate in candidates:
        features.append(
            _feature(
                {
                    "type": "LineString",
                    "coordinates": [
                        _rounded_xyz(
                            candidate.anchor_lon,
                            candidate.anchor_lat,
                            candidate.anchor_display_alt_m,
                            settings,
                        ),
                        _rounded_xyz(
                            candidate.endpoint_lon,
                            candidate.endpoint_lat,
                            candidate.endpoint_display_alt_m,
                            settings,
                        ),
                    ],
                },
                _line_properties(
                    candidate,
                    points=points,
                    settings=settings,
                    selected=1 if candidate.candidate_id in selected_ids else 0,
                    selected_rank=rank_by_candidate_id.get(candidate.candidate_id, 0),
                ),
            )
        )
    return features


def _line_properties(
    candidate: CandidateSegment,
    *,
    points: list[PreparedPoint],
    settings: OutputSettings,
    selected: int,
    selected_rank: int,
) -> dict[str, Any]:
    props: dict[str, Any] = {
                    "candidate_id": candidate.candidate_id,
                    "anchor_id": candidate.anchor_id,
                    "endpoint_id": candidate.endpoint_id,
                    "selected": selected,
                    "selected_rank": selected_rank,
                    "coverage_count": candidate.coverage_count,
                    "meaningful": 1 if candidate.is_meaningful else 0,
                    "generation_kind": candidate.generation_kind,
                    "covered_point_ids": "|".join(_coverage_point_ids(candidate, points)),
    }
    props.update(_length_fields("segment_length", candidate.segment_length_m, settings))
    props.update(_length_fields("surface_distance", candidate.surface_distance_m, settings))
    props.update(_length_fields("residual_sum", candidate.residual_sum_m, settings))
    props.update(_length_fields("residual_mean", candidate.residual_mean_m, settings))
    return props


def _build_point_status_rows(
    prepared_points: list[PreparedPoint],
    display_points: list[ResolvedPoint],
    selected_candidates: list[CandidateSegment],
    *,
    point_count: int,
    reachable_mask: int,
    settings: OutputSettings,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    offset_features: list[dict[str, Any]] = []

    for point_index, (prepared, display_point) in enumerate(zip(prepared_points, display_points)):
        assignments = [
            candidate
            for candidate in selected_candidates
            if candidate.coverage_mask & (1 << point_index)
        ]
        assignments.sort(
            key=lambda candidate: (
                round(candidate.point_distances_m[point_index], 6),
                round(candidate.segment_length_m, 6),
                candidate.candidate_id,
            )
        )

        if assignments:
            candidate = assignments[0]
            projected_lon, projected_lat, projected_alt_m = _projected_point_from_candidate(
                candidate,
                point_index,
            )
            residual_distance_m = candidate.point_distances_m[point_index]
            reachable = True
            covered = True
            assigned_candidate_id = candidate.candidate_id
            assigned_anchor_id = candidate.anchor_id
            assigned_endpoint_id = candidate.endpoint_id
            offset_features.append(
                _feature(
                    {
                        "type": "LineString",
                        "coordinates": [
                            _rounded_xyz(
                                display_point.lon,
                                display_point.lat,
                                display_point.display_alt_m,
                                settings,
                            ),
                            _rounded_xyz(projected_lon, projected_lat, projected_alt_m, settings),
                        ],
                    },
                    _offset_properties(
                        prepared,
                        candidate=candidate,
                        residual_distance_m=residual_distance_m,
                        settings=settings,
                    ),
                )
            )
        else:
            projected_lon = None
            projected_lat = None
            projected_alt_m = None
            residual_distance_m = None
            reachable = bool(reachable_mask & (1 << point_index))
            covered = False
            assigned_candidate_id = None
            assigned_anchor_id = None
            assigned_endpoint_id = None

        point_x, point_y = _output_xy(display_point.lon, display_point.lat, settings)
        if projected_lon is None or projected_lat is None:
            projected_x = None
            projected_y = None
        else:
            projected_x, projected_y = _output_xy(projected_lon, projected_lat, settings)

        row: dict[str, Any] = {
                "point_id": prepared.point_id,
                "reachable": 1 if reachable else 0,
                "covered": 1 if covered else 0,
                "assigned_candidate_id": assigned_candidate_id,
                "assigned_anchor_id": assigned_anchor_id,
                "assigned_endpoint_id": assigned_endpoint_id,
            }
        row.update(_length_fields("residual_distance", residual_distance_m, settings))
        row.update(
            {
                "point_lat": round(point_y, 9),
                "point_lon": round(point_x, 9),
            }
        )
        row.update(_length_fields("ground_alt", prepared.ground_alt_m, settings))
        row.update(_length_fields("absolute_alt", prepared.absolute_alt_m, settings))
        row.update(_length_fields("display_alt", display_point.display_alt_m, settings))
        row.update(
            {
                "altitude_source": prepared.altitude_source,
                "has_altitude": 1 if prepared.has_altitude else 0,
                "projected_lon": None if projected_x is None else round(projected_x, 9),
                "projected_lat": None if projected_y is None else round(projected_y, 9),
            }
        )
        row.update(_length_fields("projected_alt", projected_alt_m, settings))
        rows.append(row)

    return rows, offset_features


def _offset_properties(
    point: PreparedPoint,
    *,
    candidate: CandidateSegment,
    residual_distance_m: float,
    settings: OutputSettings,
) -> dict[str, Any]:
    props: dict[str, Any] = {
        "point_id": point.point_id,
        "candidate_id": candidate.candidate_id,
        "anchor_id": candidate.anchor_id,
        "endpoint_id": candidate.endpoint_id,
    }
    props.update(_length_fields("residual_distance", residual_distance_m, settings))
    return props


def run_los_cover(
    *,
    input_path: str | Path,
    dem_path: str | Path,
    output_dir: str | Path,
    repo_root: Path,
    id_field: Optional[str] = None,
    lat_field: Optional[str] = None,
    lon_field: Optional[str] = None,
    alt_field: Optional[str] = None,
    max_segment_length_m: Optional[float],
    line_tolerance_m: float = 5.0,
    anchor_height_m: float = 2.0,
    point_height_m: float = 2.0,
    endpoint_height_m: float = 2.0,
    azimuth_step_deg: float = 10.0,
    endpoint_step_m: float = 25.0,
    sample_step_m: float = 10.0,
    solver: str = "hybrid",
    output_crs: str = "WGS84",
    output_unit: str = "meters",
    dem_unit: str = "meters",
) -> dict[str, Any]:
    output_settings = build_output_settings(output_crs, output_unit)
    dataset = load_dataset(
        input_path,
        repo_root,
        id_field=id_field,
        lat_field=lat_field,
        lon_field=lon_field,
        alt_field=alt_field,
    )
    resolved_dem_path = resolve_input_path(str(dem_path), repo_root)
    provider: ElevationProvider = create_elevation_provider(resolved_dem_path, repo_root)
    provider = UnitScaledElevationProvider(provider, source_unit=dem_unit)
    prepared_points = prepare_points(dataset, provider)
    display_points = resolve_points(prepared_points, offset_m=point_height_m)
    raw_pair_candidates, family_candidates, candidate_stats = generate_candidates(
        prepared_points,
        provider,
        point_height_m=point_height_m,
        max_segment_length_m=max_segment_length_m,
        line_tolerance_m=line_tolerance_m,
        sample_step_m=sample_step_m,
    )
    selection = solve_candidate_cover(
        family_candidates,
        len(prepared_points),
        solver=solver,
        reachable_mask=candidate_stats.reachable_mask,
    )
    selected_candidates = selection.selected_candidates
    selected_ids = {candidate.candidate_id for candidate in selected_candidates}

    point_status_rows, coverage_offset_features = _build_point_status_rows(
        prepared_points,
        display_points,
        selected_candidates,
        point_count=len(prepared_points),
        reachable_mask=selection.reachable_mask,
        settings=output_settings,
    )
    selected_rows = rank_selected_candidates(
        selected_candidates,
        point_count=len(prepared_points),
        target_mask=selection.reachable_mask,
    )
    rank_by_candidate_id = {str(row["candidate_id"]): int(row["rank"]) for row in selected_rows}
    pair_summary_rows = _candidate_summary_rows(
        raw_pair_candidates,
        selected_ids=selected_ids,
        exact_pool_ids=selection.exact_pool_ids,
        points=prepared_points,
        settings=output_settings,
    )
    candidate_summary_rows = _candidate_summary_rows(
        family_candidates,
        selected_ids=selected_ids,
        exact_pool_ids=selection.exact_pool_ids,
        points=prepared_points,
        settings=output_settings,
    )
    pair_features = _candidate_line_features(
        raw_pair_candidates,
        points=prepared_points,
        settings=output_settings,
        selected_ids=selected_ids,
    )
    meaningful_candidates = [candidate for candidate in family_candidates if candidate.is_meaningful]
    meaningful_features = _candidate_line_features(
        meaningful_candidates,
        points=prepared_points,
        settings=output_settings,
        selected_ids=selected_ids,
        rank_by_candidate_id=rank_by_candidate_id,
    )
    selected_features = _candidate_line_features(
        selected_candidates,
        points=prepared_points,
        settings=output_settings,
        selected_ids=selected_ids,
        rank_by_candidate_id=rank_by_candidate_id,
    )

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    pair_segments_path = output_root / "pair_segments.geojson"
    meaningful_lines_path = output_root / "meaningful_lines.geojson"
    selected_segments_path = output_root / "selected_segments.geojson"
    point_status_path = output_root / "point_status.csv"
    pair_summary_path = output_root / "pair_summary.csv"
    candidate_summary_path = output_root / "candidate_summary.csv"
    selected_rows_path = output_root / "selected_rows.csv"
    coverage_offsets_path = output_root / "coverage_offsets.geojson"
    manifest_path = output_root / "manifest.json"
    readme_path = output_root / "README.md"

    write_geojson(pair_segments_path, pair_features, output_settings)
    write_geojson(meaningful_lines_path, meaningful_features, output_settings)
    write_geojson(selected_segments_path, selected_features, output_settings)
    write_geojson(coverage_offsets_path, coverage_offset_features, output_settings)
    write_rows(point_status_path, point_status_rows, "csv")
    write_rows(pair_summary_path, pair_summary_rows, "csv")
    write_rows(candidate_summary_path, candidate_summary_rows, "csv")
    write_rows(selected_rows_path, selected_rows, "csv")

    reachable_ids = _mask_to_point_ids(selection.reachable_mask, prepared_points)
    uncovered_ids = [row["point_id"] for row in point_status_rows if not row["covered"]]
    covered_ids = [row["point_id"] for row in point_status_rows if row["covered"]]
    meaningful_ids = [
        candidate.candidate_id for candidate in selected_candidates if candidate.is_meaningful
    ]
    manifest = {
        "solver_version": LOS_SOLVER_VERSION,
        "input_path": str(dataset.path),
        "dem_path": str(resolved_dem_path),
        "output_dir": str(output_root.resolve()),
        "output_crs": output_settings.crs_authid,
        "output_epsg": output_settings.epsg,
        "output_unit": output_settings.unit,
        "output_unit_suffix": output_settings.unit_suffix,
        "geometry_z_unit": "meters",
        "dem_unit": normalize_length_unit(dem_unit)[0],
        "point_count": len(prepared_points),
        "pair_segment_count": len(raw_pair_candidates),
        "line_family_count": len(family_candidates),
        "meaningful_line_count": len(meaningful_candidates),
        "reachable_point_count": len(reachable_ids),
        "covered_point_count": len(covered_ids),
        "selected_segment_count": len(selected_candidates),
        "selected_meaningful_line_count": len(meaningful_ids),
        "uncovered_point_ids": uncovered_ids,
        "reachable_point_ids": reachable_ids,
        "selected_candidate_ids": [candidate.candidate_id for candidate in selected_candidates],
        "selected_meaningful_line_ids": meaningful_ids,
        "config": {
            "line_tolerance_m": line_tolerance_m,
            **_length_fields("line_tolerance", line_tolerance_m, output_settings),
            "point_height_m": point_height_m,
            **_length_fields("point_height", point_height_m, output_settings),
            "max_segment_length_m": max_segment_length_m,
            **_length_fields("max_segment_length", max_segment_length_m, output_settings),
            "sample_step_m": sample_step_m,
            **_length_fields("sample_step", sample_step_m, output_settings),
            "solver": solver,
            "output_crs": output_settings.crs_authid,
            "output_unit": output_settings.unit,
            "dem_unit": normalize_length_unit(dem_unit)[0],
            "legacy_unused_options": {
                "anchor_height_m": anchor_height_m,
                "endpoint_height_m": endpoint_height_m,
                "azimuth_step_deg": azimuth_step_deg,
                "endpoint_step_m": endpoint_step_m,
            },
        },
        "candidate_stats": {
            "raw_pair_count": candidate_stats.raw_pair_count,
            "deduped_family_count": candidate_stats.deduped_family_count,
            "meaningful_family_count": candidate_stats.meaningful_family_count,
        },
        "selection": {
            "stage": selection.stage,
            "exact_status": selection.exact_status,
            "exact_pool_count": len(selection.exact_pool_ids),
        },
        "files": {
            "pair_segments": str(pair_segments_path),
            "meaningful_lines": str(meaningful_lines_path),
            "selected_segments": str(selected_segments_path),
            "point_status": str(point_status_path),
            "pair_summary": str(pair_summary_path),
            "candidate_summary": str(candidate_summary_path),
            "selected_rows": str(selected_rows_path),
            "coverage_offsets": str(coverage_offsets_path),
            "manifest": str(manifest_path),
            "readme": str(readme_path),
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    readme_path.write_text(
        "\n".join(
            [
                "# LOS Segment Cover Output",
                "",
                f"- Input dataset: `{dataset.path}`",
                f"- DEM: `{resolved_dem_path}`",
                f"- Output CRS: `{output_settings.crs_authid}`",
                f"- Report unit: `{output_settings.unit}`",
                f"- DEM elevation unit interpreted as: `{normalize_length_unit(dem_unit)[0]}`",
                f"- LOS-valid point pairs: `{len(raw_pair_candidates)}`",
                f"- Deduped line families: `{len(family_candidates)}`",
                f"- Meaningful 3+ point lines: `{len(meaningful_candidates)}`",
                f"- Selected lines: `{len(selected_candidates)}`",
                f"- Reachable points: `{len(reachable_ids)}`",
                f"- Covered points: `{len(covered_ids)}`",
                "",
                "Files:",
                "- `manifest.json`: run summary and configuration",
                "- `pair_segments.geojson`: all LOS-valid point-to-point pair segments",
                "- `meaningful_lines.geojson`: deduped 3+ point line families",
                "- `selected_segments.geojson`: selected line-family representatives as `LineStringZ`",
                "- `point_status.csv`: per-point coverage and assignment",
                "- `pair_summary.csv`: every LOS-valid pair and the points near its segment",
                "- `candidate_summary.csv`: deduped line-family inventory and solver participation",
                "- `selected_rows.csv`: selected-segment ranking summary",
                "- `coverage_offsets.geojson`: point-to-segment residual lines",
                "",
                "Next step for QGIS export:",
                f"- `cvr.bat los-project --input-dir \"{output_root.resolve()}\"`",
            ]
        ),
        encoding="utf-8",
    )
    return manifest
