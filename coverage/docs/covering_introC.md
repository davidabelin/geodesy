# Covering Preliminaries Part III

## Pairwise LOS Line Families

This part replaces the earlier one-free-endpoint segment model with the cleaner question we actually care about:

> Given a set of geographic points and a DEM, which direct point-to-point LOS segments exist, and which of those pairwise segments induce the smallest useful collection of shared 3D lines through the set?

This is still a covering problem, but the primitive objects are now finite segments between original input points.

## Problem Definition

V2 uses only original points as segment endpoints.

- Every unordered point pair is a candidate segment.
- A pair qualifies only if the direct 3D chord between the two points is terrain-clear.
- A third-or-later point belongs to that pair-induced line if its 3D point-to-segment distance is within the configured tolerance.
- Singleton lines are invalid and are not generated.

This gives two related result sets:

- `2-point` LOS pairs
  - these are baseline visibility facts
- `3+ point` line families
  - these are the meaningful compressed structures

Different LOS-valid pairs can induce the same point set. When that happens, V2 deduplicates them into one representative line family.

## Elevation Rules

The workflow uses a DEM-backed elevation provider.

- If the dataset explicitly supplies altitude, that value is authoritative.
- If a point has no assigned altitude, the DEM provides ground elevation.
- V2 assumes all heights are absolute meters and does not reconcile vertical datums.

For the current implementation, all point endpoints use the same display offset:

- point height: `+2 m` by default

## CRS and Units

Defaults remain WGS84 and meters.

`los-cover` can also write result geometry in NAD83:

- `--output-crs WGS84`
- `--output-crs EPSG:4326`
- `--output-crs NAD83`
- `--output-crs EPSG:4269`

The solver still performs its internal chord and LOS calculations in ECEF meters. The output CRS controls the horizontal coordinates written to GeoJSON, CSV coordinate columns, GeoPackage layers, and the generated QGIS project.

`los-cover` can report lengths in feet:

- `--unit meters`
- `--unit feet`

When `--unit feet` is used, unit-aware arguments such as `--max-segment-length`, `--line-tolerance`, `--point-height`, and `--sample-step` are interpreted as feet. The old meter-explicit arguments still work:

- `--line-tolerance-m`
- `--point-height-m`
- `--sample-step-m`

Feet-mode CSV and GeoJSON properties include `*_ft` fields while retaining `*_m` fields for auditability.

DEM elevation values are assumed to be meters unless overridden:

- `--dem-unit meters`
- `--dem-unit feet`

This matters because DEM rasters often declare horizontal CRS but not vertical unit metadata. Geometry Z values remain written in meters so the QGIS 3D workflow stays consistent with the solver's internal elevation model.

## Candidate Generation

The candidate-generation step is finite and terrain-aware:

1. enumerate all unordered point pairs
2. discard pairs longer than `--max-segment-length` when that filter is set
3. test each pair for direct LOS against the DEM
4. for each LOS-valid pair, test every point for near-membership in that finite segment
5. record the induced point set for that pair
6. deduplicate identical point sets into one line family representative

So the main combinatorics are `O(n^2)` pair generation plus point-membership checks over the surviving pairs.

## Optimization Strategy

The solver now optimizes over deduped line families rather than over sampled terrain endpoints.

The workflow is:

1. generate all LOS-valid point pairs
2. lift those pairs into deduped line families
3. keep all `2+ point` families as candidates
4. emphasize `3+ point` families as the meaningful structures
5. solve a set-cover approximation over the deduped families
6. improve the selection with local search
7. optionally run reduced-pool MILP refinement with `scipy.optimize.milp`

The reduced exact pool uses:

- all currently selected families
- top `15` covering families per point
- ranked by lower residual distance, then shorter segment length

Exact refinement is skipped when that pool exceeds `500` candidates.

## Outputs

`los-cover` now writes artifacts that separate pairwise facts from higher-level line families:

- `pair_segments.geojson`
  - every LOS-valid point-to-point segment
- `meaningful_lines.geojson`
  - deduped `3+ point` line families
- `selected_segments.geojson`
  - the family representatives chosen by the solver
- `pair_summary.csv`
  - one row per LOS-valid pair
- `candidate_summary.csv`
  - one row per deduped line family
- `point_status.csv`
  - per-point coverage and assignment
- `coverage_offsets.geojson`
  - point-to-selected-line residuals
- `manifest.json`
- `README.md`

`los-project` then builds a stable QGIS package from those artifacts:

- `los_segment_cover.gpkg`
- `los_segment_cover.qgs`
- `README.md`

The project layers are:

- `points_z`
- `pair_segments_z`
- `meaningful_lines_z`
- `selected_lines_z`
- `coverage_offsets_z`

## Why This Replaces The Old LOS Bundle Path

The old `los-bundle` path remains available for the earlier radius-first workflow, but it is now legacy for this problem.

It answers a different question:

- select sites first
- check LOS afterward

The pairwise LOS line-family workflow answers the question we actually want:

- enumerate LOS-valid pairs directly
- identify shared multi-point lines
- optimize over those shared structures

That also makes the result easier to reason about in QGIS now, and easier to migrate later into a Flask or browser-based viewer.
