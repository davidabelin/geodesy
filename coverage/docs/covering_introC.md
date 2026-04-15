# Covering Preliminaries Part III

## Direct LOS Segment Cover

This part defines the new problem behind the current coverage work:

> Given a set of geographic points, a DEM, and direct line-of-sight geometry in 3D, what is the smallest practical set of LOS segments needed to cover every reachable point?

This is not the same as the earlier radius-first site-cover workflow.

- Parts I and II established the covering vocabulary and the 3D geometry background.
- Part III switches from `site cover` to `segment cover`.
- The old `los-bundle` path is now legacy for this question because it picks sites before testing terrain visibility.

## Problem Definition

Each selected object is a finite direct 3D segment:

- one endpoint is anchored at an input point
- the other endpoint is a sampled visible terrain location
- the segment is a straight chord in 3D space, not a geodesic path draped over the ellipsoid

A point is counted as covered when both conditions hold:

1. the point is individually visible from the anchored endpoint
2. the point lies within a configurable distance tolerance of the finite 3D segment

For V1, the tolerance is measured in meters as point-to-segment distance, not by angular deviation.

## Elevation Rules

The workflow uses a DEM-backed elevation provider.

- If the dataset explicitly supplies altitude, that value is treated as authoritative.
- If a point has no assigned altitude, the DEM is the authority.
- V1 assumes all altitudes are absolute meters and does not reconcile vertical datums.

Default offsets in V1:

- anchor height: `+2 m`
- covered-point height: `+2 m`
- free-endpoint height: `+2 m`

## Candidate Segments

V1 uses a discretized one-free-endpoint model.

- Every input point is a possible anchor.
- Around each anchor, the solver samples terrain endpoints on radial spokes.
- Default discretization:
  - azimuth step: `10 deg`
  - endpoint step: `25 m`
  - LOS sample step: `10 m`

The endpoint must itself be visible from the anchor. That gives a family of terrain-clear candidate segments.

For practicality, the solver also keeps an anchor-only fallback candidate. Those candidates are dropped if the same anchor has any strictly better multi-point segment.

## Optimization Strategy

The exact problem is combinatorial, so V1 uses a hybrid strategy:

1. generate terrain-clear candidate segments
2. build a segment-to-point coverage relation
3. run greedy set cover
4. drop redundant selections
5. try local improvements
   - `2-for-1` collapses first
   - then same-cardinality residual improvements
6. optionally run reduced-pool exact refinement with `scipy.optimize.milp`

The exact refinement pool is intentionally capped.

- Start with all currently selected segments.
- Add the top `15` candidates per point, ranked by lower residual distance and then shorter segment length.
- Skip exact refinement if the reduced pool grows past `500` candidates.

## Outputs

The solver path is now split into two layers:

- `los-cover`
  - pure solver output
  - writes JSON, CSV, and GeoJSON artifacts
- `los-project`
  - QGIS runtime export
  - builds a `GeoPackage + QGIS project`

Primary artifacts from `los-cover`:

- `manifest.json`
- `selected_segments.geojson`
- `point_status.csv`
- `candidate_summary.csv`
- `README.md`

Primary artifacts from `los-project`:

- `los_segment_cover.gpkg`
- `los_segment_cover.qgs`
- `README.md`

## Why QGIS First

QGIS remains the first visualization target because it already fits the local data workflow and supports DEM-backed inspection. But the new output format is intentionally more stable than the earlier custom 3D loader approach.

V1 prefers:

- a conservative 2D project that opens reliably
- optional manual 3D inspection inside QGIS
- no injected 3D dock
- no custom 3D renderer script

That keeps the geometry and solver work reusable when the project later moves toward a Flask or browser-based viewer.
