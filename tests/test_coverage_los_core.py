from __future__ import annotations

from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "coverage" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


from coverage_core import Dataset, PointRecord
from coverage_los_core import (
    CandidateSegment,
    build_output_settings,
    chord_waypoint,
    deduplicate_family_candidates,
    geodesic_linear_waypoint,
    length_to_meters,
    normalize_output_crs,
    point_to_segment_distance_m,
    prepare_points,
    resolve_points,
    solve_candidate_cover,
)


class FlatElevationProvider:
    def __init__(self, elevation_m: float = 0.0):
        self.elevation_m = elevation_m

    def sample_ground_m(self, lon: float, lat: float) -> float:
        return self.elevation_m


def test_output_crs_and_unit_helpers_accept_nad83_and_feet() -> None:
    crs, authid, epsg = normalize_output_crs("NAD83")
    settings = build_output_settings("NAD83", "feet")

    assert crs.to_epsg() == 4269
    assert authid == "EPSG:4269"
    assert epsg == 4269
    assert settings.crs_authid == "EPSG:4269"
    assert settings.unit == "feet"
    assert settings.unit_suffix == "ft"
    assert round(length_to_meters(3.280839895013123, "feet"), 6) == 1.0


def _candidate(
    candidate_id: str,
    coverage_mask: int,
    *,
    anchor_index: int = 0,
    anchor_id: str = "A",
    residual_sum_m: float = 0.0,
    segment_length_m: float = 10.0,
    point_distances_m: dict[int, float] | None = None,
) -> CandidateSegment:
    point_distances_m = point_distances_m or {
        index: float(index + 1)
        for index in range(32)
        if coverage_mask & (1 << index)
    }
    return CandidateSegment(
        candidate_id=candidate_id,
        anchor_index=anchor_index,
        anchor_id=anchor_id,
        coverage_mask=coverage_mask,
        coverage_count=coverage_mask.bit_count(),
        residual_sum_m=residual_sum_m or sum(point_distances_m.values()),
        segment_length_m=segment_length_m,
        point_distances_m=point_distances_m,
    )


def test_point_to_segment_distance_handles_inside_and_outside_projection() -> None:
    inside_distance, inside_fraction = point_to_segment_distance_m(
        (5.0, 2.0, 0.0),
        (0.0, 0.0, 0.0),
        (10.0, 0.0, 0.0),
    )
    assert round(inside_distance, 6) == 2.0
    assert round(inside_fraction, 6) == 0.5

    outside_distance, outside_fraction = point_to_segment_distance_m(
        (15.0, 4.0, 0.0),
        (0.0, 0.0, 0.0),
        (10.0, 0.0, 0.0),
    )
    assert round(outside_distance, 6) == round((5.0**2 + 4.0**2) ** 0.5, 6)
    assert outside_fraction == 1.0


def test_chord_waypoint_differs_from_geodesic_linear_waypoint() -> None:
    start = (-77.0, 38.0, 0.0)
    end = (-76.0, 38.0, 0.0)
    chord_mid = chord_waypoint(start, end, 0.5)
    geodesic_mid = geodesic_linear_waypoint(start, end, 0.5)

    assert abs(geodesic_mid[2]) < 1e-6
    assert chord_mid[2] < -100.0


def test_prepare_points_uses_dem_when_altitude_missing() -> None:
    dataset = Dataset(
        path=Path("points.csv"),
        fmt="csv",
        points=[
            PointRecord(
                point_id="A",
                lat=38.0,
                lon=-77.0,
                alt_m=0.0,
                source_index=1,
                properties={},
                has_altitude=False,
            ),
            PointRecord(
                point_id="B",
                lat=38.001,
                lon=-77.0,
                alt_m=42.0,
                source_index=2,
                properties={},
                has_altitude=True,
            ),
        ],
        id_field="LOC",
        lat_field="LAT",
        lon_field="LON",
        alt_field="ALT_M",
    )
    prepared = prepare_points(dataset, FlatElevationProvider(7.5))
    resolved = resolve_points(prepared, offset_m=2.0)

    assert prepared[0].absolute_alt_m == 7.5
    assert prepared[0].altitude_source == "dem"
    assert prepared[1].absolute_alt_m == 42.0
    assert prepared[1].altitude_source == "dataset"
    assert resolved[0].display_alt_m == 9.5
    assert resolved[1].display_alt_m == 44.0


def test_deduplicate_family_candidates_keeps_best_representative_for_same_mask() -> None:
    candidates = [
        _candidate("short", 0b111, anchor_index=0, anchor_id="A", segment_length_m=10.0),
        _candidate("long", 0b111, anchor_index=1, anchor_id="B", segment_length_m=20.0),
        _candidate("pair_only", 0b011, anchor_index=0, anchor_id="A", segment_length_m=9.0),
        _candidate("singleton", 0b001, anchor_index=0, anchor_id="A", segment_length_m=0.0),
    ]

    deduped = deduplicate_family_candidates(candidates)
    assert [candidate.candidate_id for candidate in deduped] == ["pair_only", "short"]
    assert all(candidate.generation_kind == "pair-family" for candidate in deduped)


def test_solver_handles_single_segment_cover() -> None:
    candidates = [
        _candidate("all", 0b111),
        _candidate("left", 0b011),
        _candidate("right", 0b110),
    ]

    result = solve_candidate_cover(candidates, 3, solver="hybrid", reachable_mask=0b111)
    assert [candidate.candidate_id for candidate in result.selected_candidates] == ["all"]


def test_solver_handles_two_segment_cover() -> None:
    candidates = [
        _candidate("a", 0b0011, anchor_id="A"),
        _candidate("b", 0b1100, anchor_id="B"),
        _candidate("c", 0b0101, anchor_id="C"),
    ]

    result = solve_candidate_cover(candidates, 4, solver="hybrid", reachable_mask=0b1111)
    selected_ids = {candidate.candidate_id for candidate in result.selected_candidates}
    assert selected_ids == {"a", "b"}


def test_exact_refinement_recovers_better_solution_than_greedy() -> None:
    candidates = [
        _candidate("greedy_first", 0b001111, segment_length_m=20.0),
        _candidate("greedy_second", 0b010001, residual_sum_m=1.0, segment_length_m=5.0),
        _candidate("greedy_third", 0b100010, residual_sum_m=1.0, segment_length_m=5.0),
        _candidate("opt_a", 0b010011, residual_sum_m=5.0, segment_length_m=10.0),
        _candidate("opt_b", 0b101100, residual_sum_m=5.0, segment_length_m=10.0),
    ]

    greedy = solve_candidate_cover(candidates, 6, solver="greedy", reachable_mask=0b111111)
    hybrid = solve_candidate_cover(candidates, 6, solver="hybrid", reachable_mask=0b111111)

    assert len(greedy.selected_candidates) == 3
    assert {candidate.candidate_id for candidate in hybrid.selected_candidates} == {"opt_a", "opt_b"}
