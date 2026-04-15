from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np
from pyproj import CRS, Transformer

from coverage_core import (
    Dataset,
    WGS84,
    ensure_parent_dir,
    load_dataset,
    resolve_input_path,
    write_rows,
)


try:
    from scipy.optimize import Bounds, LinearConstraint, milp

    MILP_AVAILABLE = True
except Exception:
    Bounds = None
    LinearConstraint = None
    milp = None
    MILP_AVAILABLE = False


LOS_SOLVER_VERSION = "los-segment-cover-v1"
WGS84_3D = CRS.from_epsg(4979)
ECEF_CRS = CRS.from_epsg(4978)
GEODETIC_TO_ECEF = Transformer.from_crs(WGS84_3D, ECEF_CRS, always_xy=True)
ECEF_TO_GEODETIC = Transformer.from_crs(ECEF_CRS, WGS84_3D, always_xy=True)


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


@dataclass(frozen=True)
class CandidateBuildStats:
    raw_candidate_count: int
    deduped_candidate_count: int
    reachable_mask: int
    anchor_visible_masks: list[int]


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


def has_gdal_backend() -> bool:
    try:
        from osgeo import gdal as _gdal  # noqa: F401
    except Exception:
        return False
    return True


def create_elevation_provider(dem_path: str | Path, repo_root: Path) -> ElevationProvider:
    path = resolve_input_path(str(dem_path), repo_root)
    if not has_gdal_backend():
        raise RuntimeError(
            "No local GDAL-backed DEM sampler is available in this Python runtime. "
            "Run `los-cover` through the OSGeo4W QGIS runtime (for example via `cvr.bat`) "
            "or install GDAL into the active interpreter."
        )
    return GDALDemSampler(path)


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


def _iter_azimuths(step_deg: float) -> Iterable[float]:
    if step_deg <= 0:
        raise ValueError("azimuth-step-deg must be positive")
    steps = max(1, int(math.ceil(360.0 / step_deg)))
    for index in range(steps):
        azimuth = index * step_deg
        if azimuth >= 360.0:
            break
        yield float(azimuth)


def _iter_endpoint_distances(step_m: float, max_distance_m: float) -> Iterable[float]:
    if step_m <= 0:
        raise ValueError("endpoint-step-m must be positive")
    if max_distance_m < 0:
        raise ValueError("max-segment-length must be non-negative")
    count = int(math.floor(max_distance_m / step_m))
    for index in range(1, count + 1):
        yield float(index * step_m)


def _mask_from_indexes(indexes: Iterable[int]) -> int:
    mask = 0
    for index in indexes:
        mask |= 1 << index
    return mask


def _mask_indexes(mask: int, point_count: int) -> list[int]:
    return [index for index in range(point_count) if mask & (1 << index)]


def _mask_to_point_ids(mask: int, points: list[PreparedPoint]) -> list[str]:
    return [points[index].point_id for index in _mask_indexes(mask, len(points))]


def _candidate_sort_key(candidate: CandidateSegment) -> tuple[float, float, str]:
    return (
        round(candidate.segment_length_m, 6),
        round(candidate.residual_sum_m, 6),
        candidate.candidate_id,
    )


def _build_candidate(
    *,
    candidate_id: str,
    anchor_index: int,
    anchor: ResolvedPoint,
    cover_points: list[ResolvedPoint],
    visible_point_mask: int,
    endpoint_lon: float,
    endpoint_lat: float,
    endpoint_ground_alt_m: float,
    endpoint_display_alt_m: float,
    surface_distance_m: float,
    azimuth_deg: Optional[float],
    generation_kind: str,
    min_clearance_m: Optional[float],
    line_tolerance_m: float,
) -> CandidateSegment:
    end_xyz = geodetic_to_ecef(endpoint_lon, endpoint_lat, endpoint_display_alt_m)
    point_distances_m: dict[int, float] = {}
    projection_fractions: dict[int, float] = {}
    coverage_mask = 0

    for point_index in _mask_indexes(visible_point_mask, len(cover_points)):
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
        azimuth_deg=azimuth_deg,
        visible_point_mask=visible_point_mask,
        point_distances_m=point_distances_m,
        projection_fractions=projection_fractions,
        generation_kind=generation_kind,
        min_clearance_m=min_clearance_m,
    )


def deduplicate_anchor_candidates(
    candidates: list[CandidateSegment],
    *,
    anchor_index: int,
) -> list[CandidateSegment]:
    anchor_mask = 1 << anchor_index
    best_by_mask: dict[int, CandidateSegment] = {}
    anchor_only: Optional[CandidateSegment] = None

    for candidate in candidates:
        if candidate.coverage_mask == 0:
            continue
        if candidate.coverage_mask == anchor_mask:
            if anchor_only is None or _candidate_sort_key(candidate) < _candidate_sort_key(anchor_only):
                anchor_only = candidate
            continue
        current = best_by_mask.get(candidate.coverage_mask)
        if current is None or _candidate_sort_key(candidate) < _candidate_sort_key(current):
            best_by_mask[candidate.coverage_mask] = candidate

    deduped = sorted(best_by_mask.values(), key=lambda item: item.candidate_id)
    if anchor_only is not None and not deduped:
        deduped.append(anchor_only)
    return deduped


def generate_candidates(
    prepared_points: list[PreparedPoint],
    provider: ElevationProvider,
    *,
    anchor_height_m: float,
    point_height_m: float,
    endpoint_height_m: float,
    max_segment_length_m: float,
    line_tolerance_m: float,
    azimuth_step_deg: float,
    endpoint_step_m: float,
    sample_step_m: float,
) -> tuple[list[CandidateSegment], CandidateBuildStats]:
    anchor_points = resolve_points(prepared_points, offset_m=anchor_height_m)
    cover_points = resolve_points(prepared_points, offset_m=point_height_m)

    all_candidates: list[CandidateSegment] = []
    raw_candidate_count = 0
    reachable_mask = 0
    anchor_visible_masks: list[int] = []

    for anchor_index, anchor in enumerate(anchor_points):
        anchor_visible_indexes: list[int] = []
        for point_index, point in enumerate(cover_points):
            if point_index == anchor_index:
                anchor_visible_indexes.append(point_index)
                continue

            los_check = chord_los_clear(
                anchor.ecef,
                point.ecef,
                provider,
                sample_step_m=sample_step_m,
                start_ground_m=prepared_points[anchor_index].ground_alt_m,
                end_ground_m=prepared_points[point_index].ground_alt_m,
            )
            if los_check.visible:
                anchor_visible_indexes.append(point_index)

        visible_mask = _mask_from_indexes(anchor_visible_indexes)
        anchor_visible_masks.append(visible_mask)

        anchor_candidates: list[CandidateSegment] = []
        sequence = 1

        self_candidate = _build_candidate(
            candidate_id=f"{anchor.point_id}__self",
            anchor_index=anchor_index,
            anchor=anchor,
            cover_points=cover_points,
            visible_point_mask=visible_mask,
            endpoint_lon=anchor.lon,
            endpoint_lat=anchor.lat,
            endpoint_ground_alt_m=prepared_points[anchor_index].ground_alt_m,
            endpoint_display_alt_m=anchor.display_alt_m,
            surface_distance_m=0.0,
            azimuth_deg=None,
            generation_kind="self",
            min_clearance_m=anchor.display_alt_m - prepared_points[anchor_index].ground_alt_m,
            line_tolerance_m=line_tolerance_m,
        )
        anchor_candidates.append(self_candidate)

        for azimuth_deg in _iter_azimuths(azimuth_step_deg):
            for surface_distance_m in _iter_endpoint_distances(endpoint_step_m, max_segment_length_m):
                endpoint_lon, endpoint_lat, _ = WGS84.fwd(
                    anchor.lon, anchor.lat, azimuth_deg, surface_distance_m
                )
                try:
                    endpoint_ground_alt_m = provider.sample_ground_m(endpoint_lon, endpoint_lat)
                except ValueError:
                    continue

                endpoint_display_alt_m = endpoint_ground_alt_m + endpoint_height_m
                endpoint_xyz = geodetic_to_ecef(
                    endpoint_lon,
                    endpoint_lat,
                    endpoint_display_alt_m,
                )
                los_check = chord_los_clear(
                    anchor.ecef,
                    endpoint_xyz,
                    provider,
                    sample_step_m=sample_step_m,
                    start_ground_m=prepared_points[anchor_index].ground_alt_m,
                    end_ground_m=endpoint_ground_alt_m,
                )
                if not los_check.visible:
                    continue

                candidate = _build_candidate(
                    candidate_id=f"{anchor.point_id}__seg_{sequence:04d}",
                    anchor_index=anchor_index,
                    anchor=anchor,
                    cover_points=cover_points,
                    visible_point_mask=visible_mask,
                    endpoint_lon=endpoint_lon,
                    endpoint_lat=endpoint_lat,
                    endpoint_ground_alt_m=endpoint_ground_alt_m,
                    endpoint_display_alt_m=endpoint_display_alt_m,
                    surface_distance_m=surface_distance_m,
                    azimuth_deg=azimuth_deg,
                    generation_kind="radial",
                    min_clearance_m=los_check.min_clearance_m,
                    line_tolerance_m=line_tolerance_m,
                )
                sequence += 1
                if candidate.coverage_count > 0:
                    anchor_candidates.append(candidate)

        raw_candidate_count += len(anchor_candidates)
        deduped = deduplicate_anchor_candidates(anchor_candidates, anchor_index=anchor_index)
        for candidate in deduped:
            reachable_mask |= candidate.coverage_mask
        all_candidates.extend(deduped)

    return all_candidates, CandidateBuildStats(
        raw_candidate_count=raw_candidate_count,
        deduped_candidate_count=len(all_candidates),
        reachable_mask=reachable_mask,
        anchor_visible_masks=anchor_visible_masks,
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


def _feature_collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def write_geojson(path: Path, features: list[dict[str, Any]]) -> None:
    ensure_parent_dir(path)
    path.write_text(json.dumps(_feature_collection(features), indent=2), encoding="utf-8")


def _rounded_xyz(lon: float, lat: float, z: float) -> list[float]:
    return [round(lon, 9), round(lat, 9), round(z, 3)]


def _candidate_summary_rows(
    candidates: list[CandidateSegment],
    *,
    selected_ids: set[str],
    exact_pool_ids: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        rows.append(
            {
                "candidate_id": candidate.candidate_id,
                "anchor_id": candidate.anchor_id,
                "generation_kind": candidate.generation_kind,
                "azimuth_deg": None if candidate.azimuth_deg is None else round(candidate.azimuth_deg, 6),
                "surface_distance_m": round(candidate.surface_distance_m, 6),
                "segment_length_m": round(candidate.segment_length_m, 6),
                "coverage_count": candidate.coverage_count,
                "visible_point_count": candidate.visible_point_mask.bit_count(),
                "residual_sum_m": round(candidate.residual_sum_m, 6),
                "residual_mean_m": round(candidate.residual_mean_m, 6),
                "selected": 1 if candidate.candidate_id in selected_ids else 0,
                "in_exact_pool": 1 if candidate.candidate_id in exact_pool_ids else 0,
                "min_clearance_m": (
                    None
                    if candidate.min_clearance_m is None
                    else round(candidate.min_clearance_m, 6)
                ),
            }
        )
    return rows


def _selected_segment_features(
    selected_candidates: list[CandidateSegment],
    rank_by_candidate_id: dict[str, int],
) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    for candidate in selected_candidates:
        features.append(
            _feature(
                {
                    "type": "LineString",
                    "coordinates": [
                        _rounded_xyz(
                            candidate.anchor_lon,
                            candidate.anchor_lat,
                            candidate.anchor_display_alt_m,
                        ),
                        _rounded_xyz(
                            candidate.endpoint_lon,
                            candidate.endpoint_lat,
                            candidate.endpoint_display_alt_m,
                        ),
                    ],
                },
                {
                    "candidate_id": candidate.candidate_id,
                    "anchor_id": candidate.anchor_id,
                    "selected_rank": rank_by_candidate_id.get(candidate.candidate_id, 0),
                    "coverage_count": candidate.coverage_count,
                    "segment_length_m": round(candidate.segment_length_m, 6),
                    "surface_distance_m": round(candidate.surface_distance_m, 6),
                    "residual_sum_m": round(candidate.residual_sum_m, 6),
                    "generation_kind": candidate.generation_kind,
                },
            )
        )
    return features


def _build_point_status_rows(
    prepared_points: list[PreparedPoint],
    display_points: list[ResolvedPoint],
    selected_candidates: list[CandidateSegment],
    *,
    point_count: int,
    reachable_mask: int,
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
            offset_features.append(
                _feature(
                    {
                        "type": "LineString",
                        "coordinates": [
                            _rounded_xyz(display_point.lon, display_point.lat, display_point.display_alt_m),
                            _rounded_xyz(projected_lon, projected_lat, projected_alt_m),
                        ],
                    },
                    {
                        "point_id": prepared.point_id,
                        "candidate_id": candidate.candidate_id,
                        "anchor_id": candidate.anchor_id,
                        "residual_distance_m": round(residual_distance_m, 6),
                    },
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

        rows.append(
            {
                "point_id": prepared.point_id,
                "reachable": 1 if reachable else 0,
                "covered": 1 if covered else 0,
                "assigned_candidate_id": assigned_candidate_id,
                "assigned_anchor_id": assigned_anchor_id,
                "residual_distance_m": None if residual_distance_m is None else round(residual_distance_m, 6),
                "point_lat": round(display_point.lat, 9),
                "point_lon": round(display_point.lon, 9),
                "ground_alt_m": round(prepared.ground_alt_m, 6),
                "absolute_alt_m": round(prepared.absolute_alt_m, 6),
                "display_alt_m": round(display_point.display_alt_m, 6),
                "altitude_source": prepared.altitude_source,
                "has_altitude": 1 if prepared.has_altitude else 0,
                "projected_lon": None if projected_lon is None else round(projected_lon, 9),
                "projected_lat": None if projected_lat is None else round(projected_lat, 9),
                "projected_alt_m": None if projected_alt_m is None else round(projected_alt_m, 6),
            }
        )

    return rows, offset_features


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
    max_segment_length_m: float,
    line_tolerance_m: float = 5.0,
    anchor_height_m: float = 2.0,
    point_height_m: float = 2.0,
    endpoint_height_m: float = 2.0,
    azimuth_step_deg: float = 10.0,
    endpoint_step_m: float = 25.0,
    sample_step_m: float = 10.0,
    solver: str = "hybrid",
) -> dict[str, Any]:
    dataset = load_dataset(
        input_path,
        repo_root,
        id_field=id_field,
        lat_field=lat_field,
        lon_field=lon_field,
        alt_field=alt_field,
    )
    resolved_dem_path = resolve_input_path(str(dem_path), repo_root)
    provider = create_elevation_provider(resolved_dem_path, repo_root)
    prepared_points = prepare_points(dataset, provider)
    display_points = resolve_points(prepared_points, offset_m=point_height_m)
    candidates, candidate_stats = generate_candidates(
        prepared_points,
        provider,
        anchor_height_m=anchor_height_m,
        point_height_m=point_height_m,
        endpoint_height_m=endpoint_height_m,
        max_segment_length_m=max_segment_length_m,
        line_tolerance_m=line_tolerance_m,
        azimuth_step_deg=azimuth_step_deg,
        endpoint_step_m=endpoint_step_m,
        sample_step_m=sample_step_m,
    )
    selection = solve_candidate_cover(
        candidates,
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
    )
    selected_rows = rank_selected_candidates(
        selected_candidates,
        point_count=len(prepared_points),
        target_mask=selection.reachable_mask,
    )
    rank_by_candidate_id = {str(row["candidate_id"]): int(row["rank"]) for row in selected_rows}
    selected_features = _selected_segment_features(selected_candidates, rank_by_candidate_id)
    candidate_summary_rows = _candidate_summary_rows(
        candidates,
        selected_ids=selected_ids,
        exact_pool_ids=selection.exact_pool_ids,
    )

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    selected_segments_path = output_root / "selected_segments.geojson"
    point_status_path = output_root / "point_status.csv"
    candidate_summary_path = output_root / "candidate_summary.csv"
    selected_rows_path = output_root / "selected_rows.csv"
    coverage_offsets_path = output_root / "coverage_offsets.geojson"
    manifest_path = output_root / "manifest.json"
    readme_path = output_root / "README.md"

    write_geojson(selected_segments_path, selected_features)
    write_geojson(coverage_offsets_path, coverage_offset_features)
    write_rows(point_status_path, point_status_rows, "csv")
    write_rows(candidate_summary_path, candidate_summary_rows, "csv")
    write_rows(selected_rows_path, selected_rows, "csv")

    reachable_ids = _mask_to_point_ids(selection.reachable_mask, prepared_points)
    uncovered_ids = [row["point_id"] for row in point_status_rows if not row["covered"]]
    covered_ids = [row["point_id"] for row in point_status_rows if row["covered"]]
    manifest = {
        "solver_version": LOS_SOLVER_VERSION,
        "input_path": str(dataset.path),
        "dem_path": str(resolved_dem_path),
        "output_dir": str(output_root.resolve()),
        "point_count": len(prepared_points),
        "candidate_count": len(candidates),
        "raw_candidate_count": candidate_stats.raw_candidate_count,
        "reachable_point_count": len(reachable_ids),
        "covered_point_count": len(covered_ids),
        "selected_segment_count": len(selected_candidates),
        "uncovered_point_ids": uncovered_ids,
        "reachable_point_ids": reachable_ids,
        "selected_candidate_ids": [candidate.candidate_id for candidate in selected_candidates],
        "config": {
            "line_tolerance_m": line_tolerance_m,
            "anchor_height_m": anchor_height_m,
            "point_height_m": point_height_m,
            "endpoint_height_m": endpoint_height_m,
            "max_segment_length_m": max_segment_length_m,
            "azimuth_step_deg": azimuth_step_deg,
            "endpoint_step_m": endpoint_step_m,
            "sample_step_m": sample_step_m,
            "solver": solver,
        },
        "selection": {
            "stage": selection.stage,
            "exact_status": selection.exact_status,
            "exact_pool_count": len(selection.exact_pool_ids),
        },
        "files": {
            "selected_segments": str(selected_segments_path),
            "point_status": str(point_status_path),
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
                f"- Selected segments: `{len(selected_candidates)}`",
                f"- Reachable points: `{len(reachable_ids)}`",
                f"- Covered points: `{len(covered_ids)}`",
                "",
                "Files:",
                "- `manifest.json`: run summary and configuration",
                "- `selected_segments.geojson`: selected LOS segments as `LineStringZ`",
                "- `point_status.csv`: per-point coverage and assignment",
                "- `candidate_summary.csv`: candidate inventory and solver participation",
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
