# LOS Segment Cover V1

## Summary
Build a new terrain-aware solver for the question: “What is the minimum-ish number of direct 3D LOS segments needed to cover all input points?” V1 will treat each input point as a possible anchor, allow one free endpoint sampled from visible DEM locations, define coverage as `point-to-segment distance <= 5 m` plus `anchor-to-point LOS visible`, and optimize with a hybrid approach: greedy construction, local improvements, then optional small-instance exact refinement on a reduced candidate pool.

The primary deliverable will be a stable QGIS workflow based on `GeoPackage + QGIS project`, not the current GeoJSON + custom 3D-loader path. The current `los-bundle` path should be treated as legacy for this problem because it solves radius-first selection and only checks visibility afterward.

## Key Changes
### Geometry and terrain model
- Replace the current geodesic-path LOS approximation with a true direct 3D chord model:
  - resolve anchor, target-point, and free-endpoint positions to 3D coordinates
  - compute point-to-segment distance against the finite 3D segment, not an infinite line
  - require the orthogonal projection to fall within the segment bounds
  - evaluate terrain obstruction by sampling along the straight 3D segment and comparing against DEM ground heights
- Add an elevation-provider abstraction with local raster support now:
  - `sample_ground_m(lon, lat)`
  - point altitude resolution rule: dataset altitude if present, otherwise DEM
  - default offsets: anchor `+2 m`, covered points `+2 m`, free endpoint `+2 m`
- Assume dataset `alt_m` is an absolute elevation in meters. V1 will not reconcile vertical datums between source points and DEMs.

### Candidate generation and solver
- Add a new pure-Python solver path, separate from QGIS runtime code.
- New candidate family:
  - every input point is an eligible anchor
  - free endpoints are visible DEM sample points generated on radial spokes around each anchor
  - default discretization: `azimuth-step = 10 deg`, `endpoint-step = 25 m`, user-supplied `max-segment-length`, LOS sample step `10 m`
- For each candidate segment, build a coverage set of input points satisfying both:
  - 3D distance from point to segment `<= 5 m`
  - individual LOS from the anchor to the point is terrain-clear
- Prune candidate segments aggressively:
  - drop candidates that cover only the anchor unless no better candidate exists for that anchor
  - deduplicate identical coverage bitsets from the same anchor, keeping the shorter segment
- Solver sequence:
  1. greedy set-cover selection maximizing newly covered points
  2. drop redundant selected segments
  3. perform local improvements with `2-for-1` collapses first, then equal-cardinality residual improvements
  4. run reduced-pool exact refinement with `scipy.optimize.milp` when the reduced pool is small enough
- Reduced-pool exact refinement policy:
  - pool = all selected segments plus top `K=15` covering candidates per point, ranked by smaller residual distance then shorter segment length
  - skip exact refinement when pool size exceeds `500` candidates and record that in the manifest

### CLI, artifacts, and QGIS output
- Extend `coverage/scripts/coverage_cli.py` with a new pure-Python subcommand: `los-cover`.
- Add a QGIS-runtime export command, launched via `cvr.bat`, for project generation: `los-project`.
- Keep existing radius/site-cover commands unchanged. Mark `los-bundle` as legacy in docs and stop using it for the line-cover workflow.
- `los-cover` interface:
  - required: `--input`, `--dem`, `--max-segment-length`, `--output-dir`
  - optional: `--line-tolerance-m` default `5`, `--anchor-height-m` default `2`, `--point-height-m` default `2`, `--endpoint-height-m` default `2`, `--azimuth-step-deg` default `10`, `--endpoint-step-m` default `25`, `--sample-step-m` default `10`, `--solver` default `hybrid`
- `los-cover` result directory:
  - `manifest.json`
  - `selected_segments.geojson` as `LineStringZ`
  - `point_status.csv` with assigned segment, residual distance, anchor id, and visibility flags
  - `candidate_summary.csv` with candidate coverage counts and solver participation
  - `README.md`
- `los-project` output:
  - one `GeoPackage` containing styled layers for `points_z`, `selected_segments_z`, `selected_anchors_z`, `free_endpoints_z`, and `coverage_offsets_z`
  - one ready-to-open `.qgz`/`.qgs` project using conservative 2D defaults
  - manual 3D usage only: no injected custom 3D dock, no custom 3D renderer script, no auto-opened scene
  - project README with a safe 3D recipe: DEM terrain, vertical scale `1.0`, shadows off, eye-dome lighting off, labels off, clipped extent to result bbox

### Docs
- Add `coverage/docs/covering_introC.md` to formalize:
  - the direct-LOS segment-cover problem definition
  - the one-free-endpoint discretization strategy
  - the hybrid solver and reduced MILP refinement
  - the QGIS-first workflow and why it replaces the old LOS bundle path
  - the later migration path to Flask/web visualization

## Test Plan
- Unit tests for:
  - 3D point-to-segment distance with inside-segment and outside-segment projections
  - direct-chord LOS sampling versus the old geodesic-plus-linear-z behavior
  - DEM fallback when point altitude is missing
  - candidate deduplication by identical coverage set
- Solver tests on synthetic cases with known optima:
  - one line covers all points
  - two anchors required
  - greedy misses optimum but reduced MILP recovers it
- Regression tests on the existing `refpnts` data:
  - `los-cover` completes and writes the expected artifact set
  - all points receive covered/uncovered status and assigned residuals
- QGIS export tests:
  - when the QGIS runtime is available, `los-project` writes a valid `GeoPackage` and project file
  - exporter does not require 3D dock injection or legacy GeoJSON loader scripts

## Assumptions and Defaults
- Inputs are WGS84 lat/lon point datasets with optional altitude fields.
- V1 is optimized for small to medium point sets, with candidate growth controlled by radial sampling parameters.
- DEM support in V1 is local raster only at runtime, but the provider interface will be designed so MapZen/remote sources can be added later without rewriting the solver.
- “Covering” means distance to the finite 3D segment plus anchor-to-point visibility, not angle-based tolerance.
- No code changes have been made yet in this planning turn.

## Planned Commit Summary
```md
Add a terrain-aware LOS segment-cover workflow for geographic points.

- introduce a pure-Python `los-cover` solver using direct 3D LOS segments, visible DEM-sampled free endpoints, hybrid greedy/local-search optimization, and reduced-pool MILP refinement
- add a stable `GeoPackage + QGIS project` export path for covered points, selected segments, anchors, and coverage residuals
- document the new line-cover model and deprecate the old radius-first LOS bundle workflow for this problem
```
