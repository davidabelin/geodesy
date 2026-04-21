# Coverage Tool Usage

This is the operational guide for the `coverage` tools in this repository.

The toolset currently has two main workflows:

1. Radius coverage: choose or evaluate candidate points using a distance radius.
2. LOS segment cover: use a DEM to find direct line-of-sight point pairs and build a QGIS project from the selected LOS segments.

The pairwise LOS segment-cover workflow is the newer workflow for the "LOS Segment Cover" QGIS project. The older radius-first QGIS bundle remains available, but it answers a different question.

## Quick Start: LOS Segment Cover For QGIS

From the repository root:

```cmd
cvr.bat los-cover --input ".\coverage\alphapnts.csv" --dem ".\data\tif\dc_dem.tif" --output-dir ".\coverage\results\alpha_los_pairwise_m25000_t15" --max-segment-length 25000 --line-tolerance 15 --sample-step 25 --solver hybrid && cvr.bat los-project --input-dir ".\coverage\results\alpha_los_pairwise_m25000_t15" --output-dir ".\coverage\results\alpha_los_pairwise_m25000_t15_qgis"
```

Open the generated project:

```text
coverage\results\alpha_los_pairwise_m25000_t15_qgis\los_segment_cover.qgs
```

The generated QGIS package contains:

- `los_segment_cover.gpkg`
- `los_segment_cover.qgs`
- `README.md`

The main QGIS layers are:

- `points_z`
- `pair_segments_z`
- `meaningful_lines_z`
- `selected_lines_z`
- `coverage_offsets_z`

## Launchers And Runtime

Use `cvr.bat` from the repository root for normal work:

```cmd
cvr.bat <command> <options>
```

Most commands are handled by:

```text
coverage\scripts\coverage_cli.py
```

Two QGIS-specific commands are intercepted by `cvr.bat` and run under OSGeo4W QGIS Python:

- `los-project`
- `los-bundle`

The `los-project` command does not appear in `coverage_cli.py --help` because it is handled by:

```text
coverage\scripts\coverage_los_project.py
```

The `los-cover` command needs GDAL to sample the DEM. If the active Python does not have `osgeo`, the CLI attempts to re-run itself through:

```text
%LOCALAPPDATA%\Programs\OSGeo4W\bin\python-qgis.bat
```

If that automatic re-exec fails, run it explicitly:

```cmd
"%LOCALAPPDATA%\Programs\OSGeo4W\bin\python-qgis.bat" ".\coverage\scripts\coverage_cli.py" los-cover --input ".\coverage\alphapnts.csv" --dem ".\data\tif\dc_dem.tif" --output-dir ".\coverage\results\alpha_los_pairwise_m25000_t15" --max-segment-length 25000 --line-tolerance 15 --sample-step 25 --solver hybrid
```

## Input Data

Supported point inputs:

- CSV
- GeoJSON FeatureCollection containing Point features

CSV fields are auto-detected when possible.

ID field candidates:

- `LOC`
- `loc`
- `id`
- `ID`
- `name`
- `NAME`
- `label`
- `LABEL`

Latitude field candidates:

- `LAT`
- `lat`
- `latitude`
- `Latitude`
- `LAT_Orig`
- `lat_orig`

Longitude field candidates:

- `LON`
- `lon`
- `longitude`
- `Longitude`
- `LON_Orig`
- `lon_orig`

Altitude in meters candidates:

- `ALT_M`
- `alt_m`
- `ALT`
- `alt`
- `altitude_m`
- `elev_m`
- `z_m`

Altitude in feet candidates:

- `ALT_FT`
- `alt_ft`
- `elev_ft`
- `z_ft`

If the fields are not detected correctly, pass overrides:

```cmd
cvr.bat inspect --input ".\coverage\alphapnts.csv" --id-field LOC --lat-field LAT --lon-field LON
```

For LOS work, if a point has an explicit altitude, that altitude is used. If it does not, ground elevation is sampled from the DEM.

## Path Resolution

Relative input paths are checked in these places:

1. The current working directory.
2. The repository root.
3. The repository `data\` folder.

For reproducible runs, use explicit paths relative to the repository root.

Outputs normally go under:

```text
coverage\results\
```

## Core Concepts And Naming Conventions

### Units And Suffixes

The suffix `_m` means "in meters." This is true for distances, elevations, lengths, residuals, and terrain clearances.

The suffix `_ft` means "in feet." The LOS workflow writes `*_ft` fields when `--unit feet` is used, and keeps `*_m` fields where useful for auditability. The QGIS GeoPackage schema also has nullable feet fields so one schema works for both meter and feet runs.

Defaults are generally meters:

- Radius commands default `--unit meters`.
- LOS commands default `--unit meters`.
- DEM vertical values default `--dem-unit meters`.
- Geometry Z values are always written in meters.

The unit selected by `--unit` controls user-facing distance inputs for the command. Internally, the tools convert to meters for calculations.

### Horizontal Coordinates Versus Z Values

Input longitude/latitude are geographic coordinates.

For LOS output:

- `--output-crs WGS84` and `--output-crs EPSG:4326` write horizontal coordinates as WGS84 longitude/latitude.
- `--output-crs NAD83` and `--output-crs EPSG:4269` write horizontal coordinates as NAD83 longitude/latitude.
- GeoJSON and GeoPackage horizontal coordinates use the selected output CRS.
- Z coordinates remain meters above the assumed vertical reference.

The CSV column names `point_lon`, `point_lat`, `projected_lon`, and `projected_lat` are historical names. When `--output-crs NAD83` is used, those columns hold NAD83 geographic X/Y values even though the names still say lon/lat.

### Boolean Fields

Boolean-like output fields use `1` and `0`:

- `1` means true/yes.
- `0` means false/no.

### IDs

Point IDs come from the detected or explicit ID field. In `alphapnts.csv`, that is `LOC`.

LOS candidate IDs use:

```text
<anchor_id>__<endpoint_id>
```

For example:

```text
K__J
```

### Anchor And Endpoint

In LOS output, each segment has two original input points:

- `anchor_id`: first endpoint used by the candidate segment record.
- `endpoint_id`: second endpoint used by the candidate segment record.

The names do not imply ownership or direction in the real-world geometry. They are stable labels for the two segment endpoints.

### Assigned

`assigned_*` fields appear in `point_status.csv` and the QGIS `points_z` layer.

A point can be covered by more than one selected segment. The tool assigns the point to one selected segment for reporting by sorting possible selected segments by:

1. lowest residual distance from the point to the segment
2. shortest segment length
3. candidate ID

So:

- `assigned_candidate_id` is the selected segment chosen as the best explanation for that point.
- `assigned_anchor_id` and `assigned_endpoint_id` are the endpoints of that selected segment.

If a point is uncovered, these fields are blank.

### Projected

`projected_*` fields describe the nearest point on the assigned 3D LOS segment.

For a covered point, the tool finds the nearest point on the assigned segment's 3D chord. That nearest point is the projected point.

So:

- `projected_lon` / `projected_lat`: horizontal coordinates of the nearest point on the assigned segment.
- `projected_alt_m`: Z value of that nearest point on the assigned segment.
- `residual_distance_m`: 3D distance from the actual display point to that projected point.

If the covered point is one of the segment endpoints, residual distance is usually `0.0` and the projected point is the point itself.

### Residual

Residual distance is the point-to-segment miss distance in 3D.

In LOS candidate generation, a point belongs to a candidate line family when its 3D distance to that finite segment is less than or equal to the line tolerance.

Residual fields:

- `residual_distance_m`: one point's distance to its assigned selected segment.
- `residual_sum_m`: sum of residual distances for all points covered by a candidate.
- `residual_mean_m`: average residual distance for all points covered by a candidate.

Lower residuals indicate that the points lie closer to the candidate segment.

### Tolerance

The word "tolerance" currently matters most in `los-cover`.

`--line-tolerance` is the maximum 3D point-to-segment residual distance that lets a third-or-later point count as lying on a LOS-valid pair's induced line family.

Example:

```cmd
cvr.bat los-cover --line-tolerance 15 --unit meters ...
```

This means a point can be up to 15 meters away from a LOS-valid finite segment and still be included in that segment's covered point set.

With feet:

```cmd
cvr.bat los-cover --unit feet --line-tolerance 50 ...
```

This means 50 feet.

With the explicit meter option:

```cmd
cvr.bat los-cover --line-tolerance-m 15 ...
```

This always means 15 meters, regardless of `--unit`.

This tolerance does not mean DEM sampling spacing and does not mean QGIS visual snapping tolerance.

### Surface Distance Versus Segment Length

LOS candidates report two related lengths:

- `surface_distance_m`: WGS84 geodesic ground distance between segment endpoints.
- `segment_length_m`: 3D ECEF chord length between the display-height endpoints.

They are usually close, but not identical. `--max-segment-length` filters by surface distance.

### Terrain Clearance

`min_clearance_m` is the minimum vertical clearance found while sampling terrain along an endpoint-to-endpoint LOS line.

Positive values mean the sampled line stayed above terrain at the tested sample points. A pair is LOS-valid only when no sampled terrain point blocks the line.

### Reachable Versus Covered

In LOS output:

- `reachable=1` means the point belongs to at least one LOS-valid candidate family before final selection.
- `covered=1` means the point is covered by one of the selected candidates.

The solver tries to cover all reachable points. A point can be unreachable because no valid LOS pair connects it under the current DEM, point height, max segment length, and sampling settings.

## Inspect A Dataset

Use `inspect` first when a dataset is new or fields are uncertain.

```cmd
cvr.bat inspect --input ".\coverage\alphapnts.csv"
```

Useful explicit version:

```cmd
cvr.bat inspect --input ".\coverage\alphapnts.csv" --id-field LOC --lat-field LAT --lon-field LON --output ".\coverage\results\inspect__alphapnts.csv"
```

Options:

```text
--input INPUT
--id-field ID_FIELD
--lat-field LAT_FIELD
--lon-field LON_FIELD
--alt-field ALT_FIELD
--output OUTPUT
--format csv|json
```

Output columns include:

- `input_path`
- `format`
- `record_count`
- `id_field`
- `lat_field`
- `lon_field`
- `alt_field`
- `min_lat`
- `max_lat`
- `min_lon`
- `max_lon`
- `min_alt_m`
- `max_alt_m`

## Radius Coverage Matrix

The `matrix` command evaluates every demand point against every candidate point and records whether the pair is inside a radius.

Same file for demand and candidates:

```cmd
cvr.bat matrix --input ".\coverage\alphapnts.csv" --radius 1000 --unit meters --exclude-self --covered-only
```

Separate demand and candidate files:

```cmd
cvr.bat matrix --input ".\coverage\demand.csv" --candidates ".\coverage\candidates.csv" --radius 2 --unit miles
```

Options:

```text
--input INPUT
--candidates CANDIDATES
--id-field ID_FIELD
--lat-field LAT_FIELD
--lon-field LON_FIELD
--alt-field ALT_FIELD
--radius RADIUS
--unit meters|m|km|miles|mi|feet|ft
--distance-mode surface|ecef-3d
--exclude-self
--covered-only
--output OUTPUT
--format csv|json
```

Argument details:

- `--input`: demand point dataset.
- `--candidates`: candidate-site point dataset. If omitted, the input dataset is also used as the candidate dataset.
- `--id-field`: field used as the point identifier.
- `--lat-field`: CSV latitude field override.
- `--lon-field`: CSV longitude field override.
- `--alt-field`: altitude field override. Meter fields are interpreted as meters; detected feet fields are converted to meters.
- `--radius`: coverage radius in `--unit`.
- `--unit`: unit for `--radius`.
- `--distance-mode surface`: ellipsoidal geodesic ground distance. This ignores altitude.
- `--distance-mode ecef-3d`: straight-line 3D distance using altitude values.
- `--exclude-self`: skip demand/candidate pairs with the same point ID.
- `--covered-only`: only write rows where `covered=1`.
- `--output`: explicit output file path.
- `--format`: `csv` or `json` table output.

Distance modes:

- `surface`: WGS84 ellipsoidal geodesic distance.
- `ecef-3d`: straight-line 3D ECEF distance using point altitudes.

Output columns include:

- `demand_id`
- `candidate_id`
- `covered`
- `distance_m`
- `radius_m`
- `distance_mode`
- `demand_lat`
- `demand_lon`
- `candidate_lat`
- `candidate_lon`

## Greedy Full Cover

The `full-cover` command chooses candidate points until no more reachable demand points remain uncovered.

```cmd
cvr.bat full-cover --input ".\coverage\alphapnts.csv" --radius 1000 --unit meters --exclude-self
```

With separate candidate sites:

```cmd
cvr.bat full-cover --input ".\coverage\demand.csv" --candidates ".\coverage\candidates.csv" --radius 2 --unit miles
```

Options:

```text
--input INPUT
--candidates CANDIDATES
--id-field ID_FIELD
--lat-field LAT_FIELD
--lon-field LON_FIELD
--alt-field ALT_FIELD
--radius RADIUS
--unit meters|m|km|miles|mi|feet|ft
--distance-mode surface|ecef-3d
--exclude-self
--output OUTPUT
--format csv|json
```

Argument details:

- `--input`: demand point dataset.
- `--candidates`: candidate-site point dataset. If omitted, the input dataset is also used as the candidate dataset.
- `--radius`: maximum distance for a candidate to cover a demand point.
- `--unit`: unit for `--radius`.
- `--distance-mode`: same meaning as in `matrix`.
- `--exclude-self`: useful when demand and candidate files are the same and a point should not cover itself.
- `--output`: explicit output file path.
- `--format`: `csv` or `json` table output.

Output columns include:

- `rank`
- `candidate_id`
- `newly_covered_count`
- `total_covered_count`
- `remaining_uncovered_count`
- `covered_demand_ids`

## Greedy Budgeted Max Cover

The `max-cover` command chooses up to `--budget` candidates to cover as many demand points as possible.

```cmd
cvr.bat max-cover --input ".\coverage\alphapnts.csv" --radius 1000 --unit meters --exclude-self --budget 5
```

Options:

```text
--input INPUT
--candidates CANDIDATES
--id-field ID_FIELD
--lat-field LAT_FIELD
--lon-field LON_FIELD
--alt-field ALT_FIELD
--radius RADIUS
--unit meters|m|km|miles|mi|feet|ft
--distance-mode surface|ecef-3d
--exclude-self
--budget BUDGET
--output OUTPUT
--format csv|json
```

Argument details:

- `--budget`: maximum number of candidates the solver may select.
- Other arguments have the same meaning as `full-cover`.

Output columns are the same shape as `full-cover`.

## Radius-First QGIS Bundle

The `qgis-bundle` command writes a simple GeoJSON bundle for the older radius coverage workflow.

```cmd
cvr.bat qgis-bundle --input ".\coverage\alphapnts.csv" --radius 1000 --unit meters --exclude-self --solver full-cover --output-dir ".\coverage\results\alpha_radius_qgis_bundle"
```

Options:

```text
--input INPUT
--candidates CANDIDATES
--id-field ID_FIELD
--lat-field LAT_FIELD
--lon-field LON_FIELD
--alt-field ALT_FIELD
--radius RADIUS
--unit meters|m|km|miles|mi|feet|ft
--distance-mode surface|ecef-3d
--exclude-self
--solver none|full-cover|max-cover
--budget BUDGET
--output-dir OUTPUT_DIR
```

Argument details:

- `--solver none`: do not select candidates; only write coverage relationships.
- `--solver full-cover`: select enough candidates to cover all reachable demand under the radius rule.
- `--solver max-cover`: select up to `--budget` candidates.
- `--budget`: required when `--solver max-cover`.
- `--output-dir`: folder for GeoJSON files, manifest, solver table, and QGIS loader script.
- Other radius, unit, field, and distance arguments have the same meaning as `matrix`.

Bundle files:

- `demands.geojson`
- `candidates.geojson`
- `coverage_links.geojson`
- `coverage_zones.geojson`
- `solver_rows.csv`
- `load_bundle_qgis.py`
- `bundle_manifest.json`

This path does not do terrain LOS checking. It is useful for radius-first screening and map review.

## Legacy LOS Bundle

The `los-bundle` command is a QGIS-runtime branch handled directly by `cvr.bat`. It is an older radius-first plus LOS-checking workflow.

```cmd
cvr.bat los-bundle --input ".\coverage\alphapnts.csv" --dem ".\data\tif\dc_dem.tif" --radius 1000 --unit meters --exclude-self --solver full-cover --output-dir ".\coverage\results\alpha_los_bundle"
```

Options:

```text
--input INPUT
--candidates CANDIDATES
--dem DEM
--id-field ID_FIELD
--lat-field LAT_FIELD
--lon-field LON_FIELD
--alt-field ALT_FIELD
--radius RADIUS
--unit meters|m|km|miles|mi|feet|ft
--distance-mode surface|ecef-3d
--exclude-self
--observer-height-m OBSERVER_HEIGHT_M
--target-height-m TARGET_HEIGHT_M
--sample-step-m SAMPLE_STEP_M
--solver none|full-cover|max-cover
--budget BUDGET
--output-dir OUTPUT_DIR
```

Argument details:

- `--dem`: DEM raster used for terrain sampling.
- `--observer-height-m`: height added above DEM altitude at demand points.
- `--target-height-m`: height added above DEM altitude at candidate points.
- `--sample-step-m`: terrain sampling interval along each LOS line, in meters.
- `--radius`: radius-first candidate filter before LOS sampling.
- Other field, unit, distance, solver, budget, and output arguments follow the radius workflow.

Use this only when you specifically want the older radius-first LOS workflow. For the "LOS Segment Cover" QGIS project, use `los-cover` plus `los-project`.

## LOS Segment Cover

The `los-cover` command is the current pairwise LOS workflow.

It answers:

> Given a set of geographic points and a DEM, which direct point-to-point LOS segments exist, and which selected segment families cover the reachable points?

Basic run:

```cmd
cvr.bat los-cover --input ".\coverage\alphapnts.csv" --dem ".\data\tif\dc_dem.tif" --output-dir ".\coverage\results\alpha_los_pairwise_m25000_t15" --max-segment-length 25000 --line-tolerance 15 --sample-step 25 --solver hybrid
```

Then build the QGIS project:

```cmd
cvr.bat los-project --input-dir ".\coverage\results\alpha_los_pairwise_m25000_t15" --output-dir ".\coverage\results\alpha_los_pairwise_m25000_t15_qgis"
```

Options:

```text
--input INPUT
--dem DEM
--output-dir OUTPUT_DIR
--id-field ID_FIELD
--lat-field LAT_FIELD
--lon-field LON_FIELD
--alt-field ALT_FIELD
--max-segment-length MAX_SEGMENT_LENGTH
--unit meters|m|feet|ft
--output-crs WGS84|EPSG:4326|NAD83|EPSG:4269
--dem-unit meters|m|feet|ft
--line-tolerance LINE_TOLERANCE
--line-tolerance-m LINE_TOLERANCE_M
--point-height POINT_HEIGHT
--point-height-m POINT_HEIGHT_M
--sample-step SAMPLE_STEP
--sample-step-m SAMPLE_STEP_M
--solver greedy|hybrid
```

Argument details:

- `--input`: point dataset. Each point can become an endpoint and can also be tested for near-membership in other point-to-point segments.
- `--dem`: local DEM raster. Endpoints without explicit altitude are lifted from this DEM, and terrain samples are checked against it.
- `--output-dir`: folder where solver artifacts are written.
- `--id-field`, `--lat-field`, `--lon-field`, `--alt-field`: input field overrides.
- `--max-segment-length`: maximum allowed endpoint-to-endpoint surface distance. Uses `--unit`.
- `--unit`: unit for unit-aware LOS arguments: max segment length, line tolerance, point height, and sample step.
- `--output-crs`: horizontal CRS for written coordinates and the later QGIS project.
- `--dem-unit`: vertical unit of DEM cell values before conversion to meters.
- `--line-tolerance`: maximum 3D residual distance for a point to count as belonging to a candidate segment family. Uses `--unit`.
- `--line-tolerance-m`: same concept as `--line-tolerance`, but always meters and overrides the unit-aware value.
- `--point-height`: endpoint/display height added above the explicit or DEM-derived point elevation. Uses `--unit`.
- `--point-height-m`: same concept as `--point-height`, but always meters and overrides the unit-aware value.
- `--sample-step`: terrain sampling interval along candidate LOS lines. Uses `--unit`.
- `--sample-step-m`: same concept as `--sample-step`, but always meters and overrides the unit-aware value.
- `--solver greedy`: greedy set-cover selection.
- `--solver hybrid`: greedy selection plus local improvements and reduced-pool exact refinement when available.

Accepted but legacy/unused for the pairwise model:

```text
--anchor-height-m
--endpoint-height-m
--azimuth-step-deg
--endpoint-step-m
```

### LOS Units

The `--unit` option controls these unit-aware values:

- `--max-segment-length`
- `--line-tolerance`
- `--point-height`
- `--sample-step`

Examples:

```cmd
cvr.bat los-cover --input ".\coverage\alphapnts.csv" --dem ".\data\tif\dc_dem.tif" --output-dir ".\coverage\results\alpha_los_ft" --unit feet --max-segment-length 82000 --line-tolerance 50 --sample-step 80 --output-crs NAD83
```

Meter-explicit options always mean meters:

- `--line-tolerance-m`
- `--point-height-m`
- `--sample-step-m`

The `--dem-unit` option controls how DEM cell values are interpreted before internal conversion:

- `--dem-unit meters`
- `--dem-unit feet`

Geometry Z values are written in meters for the QGIS 3D workflow.

### LOS CRS

Supported output horizontal CRS values:

- `WGS84`
- `EPSG:4326`
- `NAD83`
- `EPSG:4269`

The solver still performs internal LOS and chord calculations in ECEF meters. The output CRS affects horizontal coordinates written to GeoJSON, CSV projected coordinate fields, GeoPackage layers, and the generated QGIS project.

### LOS Elevation Rules

For each point:

- If the input has an explicit altitude field, that altitude is authoritative.
- If the input has no altitude, ground elevation is sampled from the DEM.
- A display/LOS point-height offset is added with `--point-height` or `--point-height-m`.
- Default point height is `2 m`.

The tool does not reconcile vertical datums. Keep DEM and input altitude assumptions consistent.

### LOS Candidate Generation

The current pairwise model:

1. Enumerates every unordered pair of input points.
2. Discards pairs longer than `--max-segment-length`.
3. Tests each remaining pair for terrain-clear direct LOS against the DEM.
4. For every LOS-valid pair, tests every point for near-membership in that finite segment.
5. Deduplicates identical point sets into line families.
6. Solves a set-cover problem over the deduped families.

The `meaningful_lines.geojson` output contains only deduped line families with three or more points. If all useful selected lines are simple point pairs, this file can be empty.

### LOS Solver Choices

`--solver greedy`:

- Uses the greedy set-cover approximation.

`--solver hybrid`:

- Starts with greedy/local improvement.
- Attempts reduced-pool exact refinement with `scipy.optimize.milp` when available and practical.
- Skips exact refinement when the reduced pool is too large.

The CLI prints the exact refinement status after each run.

### LOS Output Files

`los-cover` writes:

- `manifest.json`: run summary, config, file paths, selected IDs, uncovered IDs.
- `README.md`: short run summary and next QGIS command.
- `pair_segments.geojson`: every LOS-valid point-to-point segment.
- `meaningful_lines.geojson`: deduped 3+ point line families.
- `selected_segments.geojson`: selected line-family representatives as `LineStringZ`.
- `point_status.csv`: per-point reachability, coverage, assignment, elevations, projected coordinates.
- `pair_summary.csv`: one row per LOS-valid pair.
- `candidate_summary.csv`: one row per deduped line-family candidate.
- `selected_rows.csv`: selected segment ranking summary.
- `coverage_offsets.geojson`: residual point-to-selected-segment offset lines.

### Inspect Output Columns

`inspect` writes one row with:

- `input_path`: resolved input file path.
- `format`: `csv` or `geojson`.
- `record_count`: number of loaded point records.
- `id_field`: detected or explicit ID field.
- `lat_field`: detected or explicit latitude field.
- `lon_field`: detected or explicit longitude field.
- `alt_field`: detected or explicit altitude field, or `__default_zero__` when no altitude exists.
- `min_lat`: minimum input latitude.
- `max_lat`: maximum input latitude.
- `min_lon`: minimum input longitude.
- `max_lon`: maximum input longitude.
- `min_alt_m`: minimum loaded altitude in meters. This is `0.0` for files without altitude.
- `max_alt_m`: maximum loaded altitude in meters.

### Matrix Output Columns

`matrix` writes:

- `demand_id`: demand point ID.
- `candidate_id`: candidate point ID.
- `covered`: `True` or `False` in the CSV/JSON output, meaning distance is within radius.
- `distance_m`: computed demand-to-candidate distance in meters.
- `radius_m`: coverage radius converted to meters.
- `distance_mode`: `surface` or `ecef-3d`.
- `demand_lat`: demand point latitude.
- `demand_lon`: demand point longitude.
- `candidate_lat`: candidate point latitude.
- `candidate_lon`: candidate point longitude.

### Full-Cover And Max-Cover Output Columns

`full-cover`, `max-cover`, and radius-bundle `solver_rows.csv` write:

- `rank`: selection order, starting at `1`.
- `candidate_id`: selected candidate point ID.
- `newly_covered_count`: number of previously uncovered demand points covered by this candidate.
- `total_covered_count`: cumulative demand points covered after this candidate is selected.
- `remaining_uncovered_count`: demand points still uncovered after this candidate is selected.
- `covered_demand_ids`: pipe-separated demand IDs newly covered at this rank.

### Radius QGIS Bundle GeoJSON Properties

`demands.geojson` point properties:

- `point_id`: demand point ID.
- `lat`: source latitude.
- `lon`: source longitude.
- `alt_m`: loaded altitude in meters.
- `role`: `demand`.
- `coverage_count`: number of candidate points that cover this demand point.
- `covered`: `1` if at least one candidate covers the demand point.
- `nearest_candidate_id`: nearest covering candidate ID, or blank if uncovered.
- `nearest_distance_m`: distance to the nearest covering candidate, in meters.
- `selected_coverage_count`: number of selected candidates that cover this demand point.

`candidates.geojson` point properties:

- `point_id`: candidate point ID.
- `lat`: source latitude.
- `lon`: source longitude.
- `alt_m`: loaded altitude in meters.
- `role`: `candidate`.
- `cover_count`: number of demand points this candidate covers.
- `selected`: `1` if selected by the bundle solver.
- `selected_rank`: selection rank, or `0` if not selected.
- `selected_newly_covered_count`: demand points newly covered when this candidate was selected.

`coverage_links.geojson` line properties:

- `demand_id`: demand point ID.
- `candidate_id`: candidate point ID.
- `distance_m`: demand-to-candidate distance in meters.
- `radius_m`: coverage radius in meters.
- `distance_mode`: `surface` or `ecef-3d`.
- `selected_candidate`: `1` if the candidate endpoint was selected by the solver.

`coverage_zones.geojson` polygon properties:

- `point_id`: candidate point ID at the center of the zone.
- `lat`: source latitude.
- `lon`: source longitude.
- `alt_m`: loaded altitude in meters.
- `radius_m`: zone radius in meters.
- `cover_count`: number of demand points this candidate covers.
- `selected`: `1` if selected by the solver.
- `selected_rank`: selection rank, or `0` if not selected.
- `selected_newly_covered_count`: demand points newly covered when this candidate was selected.
- `zone_is_approximate`: `1` when `distance_mode=ecef-3d`, because the visual zone is still drawn as a geodesic surface buffer.

### LOS `point_status.csv` Columns

- `point_id`
- `reachable`: `1` if the point belongs to at least one LOS-valid candidate before final selection.
- `covered`: `1` if the point is covered by a selected candidate.
- `assigned_candidate_id`: selected segment assigned to this point for reporting.
- `assigned_anchor_id`: first endpoint of the assigned segment.
- `assigned_endpoint_id`: second endpoint of the assigned segment.
- `residual_distance_m`: 3D distance from this point to the assigned segment. Blank if uncovered.
- `residual_distance_ft`: same residual in feet when feet output is active. May be absent in meter-only CSVs.
- `point_lat`: output-CRS Y coordinate of the display point.
- `point_lon`: output-CRS X coordinate of the display point.
- `ground_alt_m`: DEM ground elevation sampled at the point, in meters.
- `ground_alt_ft`: same ground elevation in feet when feet output is active.
- `absolute_alt_m`: authoritative point altitude before display offset. If the input has altitude, this is the input altitude; otherwise it is DEM ground altitude.
- `absolute_alt_ft`: same absolute altitude in feet when feet output is active.
- `display_alt_m`: altitude used for LOS/display after adding point height.
- `display_alt_ft`: same display altitude in feet when feet output is active.
- `altitude_source`: `input` when an input altitude was used, otherwise `dem`.
- `has_altitude`: `1` if the input file supplied altitude for the point.
- `projected_lon`: output-CRS X coordinate of the nearest point on the assigned segment. Blank if uncovered.
- `projected_lat`: output-CRS Y coordinate of the nearest point on the assigned segment. Blank if uncovered.
- `projected_alt_m`: Z value of the nearest point on the assigned segment. Blank if uncovered.
- `projected_alt_ft`: same projected altitude in feet when feet output is active.

### LOS `pair_summary.csv` And `candidate_summary.csv` Columns

Both files use the same column shape.

`pair_summary.csv` contains every raw LOS-valid input point pair.

`candidate_summary.csv` contains deduped line-family candidates. Multiple raw pairs can induce the same covered point set; the candidate summary keeps one representative.

Columns:

- `candidate_id`: segment or line-family ID, usually `<anchor_id>__<endpoint_id>`.
- `anchor_id`: first endpoint point ID.
- `endpoint_id`: second endpoint point ID.
- `generation_kind`: `pair` for raw pairs; `pair-family` for deduped family representatives.
- `surface_distance_m`: WGS84 endpoint-to-endpoint surface distance in meters.
- `surface_distance_ft`: same surface distance in feet when feet output is active.
- `segment_length_m`: 3D endpoint-to-endpoint segment length in meters.
- `segment_length_ft`: same segment length in feet when feet output is active.
- `coverage_count`: number of points included in this candidate's covered point set.
- `meaningful`: `1` when the candidate covers at least three points.
- `residual_sum_m`: sum of point-to-segment residual distances for all covered points.
- `residual_sum_ft`: same sum in feet when feet output is active.
- `residual_mean_m`: average point-to-segment residual distance for covered points.
- `residual_mean_ft`: same average in feet when feet output is active.
- `covered_point_ids`: pipe-separated point IDs covered by the candidate.
- `selected`: `1` if the final solver selected this candidate.
- `in_exact_pool`: `1` if this candidate was included in the reduced exact-refinement pool.
- `min_clearance_m`: minimum sampled terrain clearance for the endpoint-to-endpoint LOS line, in meters.
- `min_clearance_ft`: same clearance in feet when feet output is active.

### LOS `selected_rows.csv` Columns

`selected_rows.csv` ranks only the final selected candidates.

- `rank`: reporting rank, starting at `1`.
- `candidate_id`: selected candidate ID.
- `anchor_id`: first endpoint point ID.
- `endpoint_id`: second endpoint point ID.
- `coverage_count`: total points covered by this selected candidate.
- `meaningful`: `1` if it covers at least three points.
- `newly_covered_count`: number of not-yet-covered reachable points added at this rank.
- `total_covered_count`: cumulative reachable points covered through this rank.
- `remaining_uncovered_count`: reachable points still uncovered after this rank.

### LOS GeoJSON Properties

`pair_segments.geojson`, `meaningful_lines.geojson`, and `selected_segments.geojson` contain 3D `LineString` features. Their coordinates are:

```text
[output_x, output_y, z_m]
```

Line properties:

- `candidate_id`
- `anchor_id`
- `endpoint_id`
- `selected`: `1` if selected.
- `selected_rank`: rank in `selected_rows.csv`, or `0`.
- `coverage_count`
- `meaningful`
- `generation_kind`
- `covered_point_ids`
- `segment_length_m` and optionally `segment_length_ft`
- `surface_distance_m` and optionally `surface_distance_ft`
- `residual_sum_m` and optionally `residual_sum_ft`
- `residual_mean_m` and optionally `residual_mean_ft`

`coverage_offsets.geojson` contains 3D `LineString` features from each covered display point to its projected point on the assigned segment. Properties:

- `point_id`
- `candidate_id`
- `anchor_id`
- `endpoint_id`
- `residual_distance_m`
- `residual_distance_ft` when feet output is active.

In feet mode, unit-aware fields also include `*_ft` values while retaining `*_m` values for auditability.

### LOS `manifest.json` Keys

Important top-level manifest fields:

- `solver_version`: implementation/model version label.
- `input_path`: resolved input dataset path.
- `dem_path`: resolved DEM path.
- `output_dir`: resolved output artifact directory.
- `output_crs`: output horizontal CRS.
- `output_epsg`: output EPSG integer.
- `output_unit`: report unit, usually `meters` or `feet`.
- `output_unit_suffix`: field suffix for unit-aware report fields.
- `geometry_z_unit`: always `meters`.
- `dem_unit`: interpreted DEM vertical unit.
- `point_count`: loaded input point count.
- `pair_segment_count`: raw LOS-valid point-pair count.
- `line_family_count`: deduped candidate family count.
- `meaningful_line_count`: count of deduped families covering at least three points.
- `reachable_point_count`: count of points reachable by at least one candidate.
- `covered_point_count`: count of reachable points covered by final selected candidates.
- `selected_segment_count`: number of selected candidates.
- `selected_meaningful_line_count`: number of selected candidates covering at least three points.
- `uncovered_point_ids`: points not covered by final selected candidates.
- `reachable_point_ids`: points with at least one candidate.
- `selected_candidate_ids`: final selected candidate IDs.
- `selected_meaningful_line_ids`: selected 3+ point candidate IDs.
- `config`: run configuration after unit conversion.
- `candidate_stats`: raw/deduped/meaningful candidate counts.
- `selection`: solver stage, exact refinement status, and exact pool count.
- `files`: paths to generated output files.

Important `config` fields:

- `line_tolerance_m`: actual line tolerance used in meters.
- `point_height_m`: actual point height used in meters.
- `max_segment_length_m`: actual max segment filter in meters.
- `sample_step_m`: actual terrain sample spacing in meters.
- `solver`: `greedy` or `hybrid`.
- `legacy_unused_options`: accepted compatibility options that do not control the current pairwise model.

Important `selection.exact_status` values:

- `not-requested`: `--solver greedy`.
- `skipped-no-scipy`: exact refinement could not run because SciPy MILP was unavailable.
- `skipped-pool-limit`: reduced exact pool exceeded the configured limit.
- `skipped-no-improvement`: exact solver did not return a usable improvement.
- `no-better-solution`: exact solver ran but did not improve the local-search result.
- `applied`: exact refinement improved the result and was used.

### QGIS GeoPackage Layer Fields

`los-project` converts LOS artifacts to `los_segment_cover.gpkg`. Some field names are shortened for QGIS.

`points_z` fields:

- `point_id`
- `reachable`
- `covered`
- `assigned_candidate_id`
- `assigned_anchor_id`
- `assigned_endpoint_id`
- `residual_m`: from `residual_distance_m`.
- `residual_ft`: from `residual_distance_ft`, if present.
- `ground_m`: from `ground_alt_m`.
- `ground_ft`: from `ground_alt_ft`, if present.
- `absolute_m`: from `absolute_alt_m`.
- `absolute_ft`: from `absolute_alt_ft`, if present.
- `display_m`: from `display_alt_m`.
- `display_ft`: from `display_alt_ft`, if present.
- `alt_source`: from `altitude_source`.

`pair_segments_z`, `meaningful_lines_z`, and `selected_lines_z` fields:

- `candidate_id`
- `anchor_id`
- `endpoint_id`
- `selected`
- `selected_rank`
- `cover_count`: from `coverage_count`.
- `meaningful`
- `segment_m`: from `segment_length_m`.
- `segment_ft`: from `segment_length_ft`, if present.
- `surface_m`: from `surface_distance_m`.
- `surface_ft`: from `surface_distance_ft`, if present.
- `residual_m`: from `residual_sum_m`.
- `residual_ft`: from `residual_sum_ft`, if present.
- `residual_mean`: from `residual_mean_m`.
- `residual_mean_ft`: from `residual_mean_ft`, if present.
- `kind`: from `generation_kind`.
- `covered_ids`: from `covered_point_ids`.

`coverage_offsets_z` fields:

- `point_id`
- `candidate_id`
- `anchor_id`
- `endpoint_id`
- `residual_m`: from `residual_distance_m`.
- `residual_ft`: from `residual_distance_ft`, if present.

## LOS Project Export

`los-project` builds the stable QGIS package from a `los-cover` output directory.

```cmd
cvr.bat los-project --input-dir ".\coverage\results\alpha_los_pairwise_m25000_t15"
```

Explicit output directory:

```cmd
cvr.bat los-project --input-dir ".\coverage\results\alpha_los_pairwise_m25000_t15" --output-dir ".\coverage\results\alpha_los_pairwise_m25000_t15_qgis"
```

Override the DEM path recorded in `manifest.json`:

```cmd
cvr.bat los-project --input-dir ".\coverage\results\alpha_los_pairwise_m25000_t15" --dem ".\data\tif\dc_dem.tif"
```

Options:

```text
--input-dir INPUT_DIR
--output-dir OUTPUT_DIR
--dem DEM
```

Argument details:

- `--input-dir`: directory produced by `los-cover`. It must contain `manifest.json` and the files listed in that manifest.
- `--output-dir`: directory for `los_segment_cover.gpkg`, `los_segment_cover.qgs`, and the project README. If omitted, the project is written under the input directory.
- `--dem`: optional DEM path override for the QGIS project. If omitted, the DEM path recorded in `manifest.json` is used.

Output files:

- `los_segment_cover.gpkg`
- `los_segment_cover.qgs`
- `README.md`

The QGIS project title is:

```text
LOS Segment Cover
```

## Suggested QGIS 3D Setup

After opening `los_segment_cover.qgs`:

1. Open `View -> New 3D Map View`.
2. In terrain settings, choose the loaded DEM raster.
3. Keep vertical scale at `1.0`.
4. Turn shadows off.
5. Turn eye-dome lighting off.
6. Turn labels off for the first inspection.
7. Start at the LOS result extent before widening the view.

## Common Recipes

Inspect `alphapnts.csv`:

```cmd
cvr.bat inspect --input ".\coverage\alphapnts.csv"
```

Run current LOS segment cover and QGIS export:

```cmd
cvr.bat los-cover --input ".\coverage\alphapnts.csv" --dem ".\data\tif\dc_dem.tif" --output-dir ".\coverage\results\alpha_los_pairwise_m25000_t15" --max-segment-length 25000 --line-tolerance 15 --sample-step 25 --solver hybrid && cvr.bat los-project --input-dir ".\coverage\results\alpha_los_pairwise_m25000_t15" --output-dir ".\coverage\results\alpha_los_pairwise_m25000_t15_qgis"
```

Run LOS segment cover with NAD83 horizontal output and feet reporting:

```cmd
cvr.bat los-cover --input ".\coverage\alphapnts.csv" --dem ".\data\tif\dc_dem.tif" --output-dir ".\coverage\results\alpha_los_nad83_ft" --unit feet --output-crs NAD83 --max-segment-length 82000 --line-tolerance 50 --sample-step 80 --solver hybrid && cvr.bat los-project --input-dir ".\coverage\results\alpha_los_nad83_ft" --output-dir ".\coverage\results\alpha_los_nad83_ft_qgis"
```

Run a radius matrix:

```cmd
cvr.bat matrix --input ".\coverage\alphapnts.csv" --radius 1000 --unit meters --exclude-self --covered-only
```

Run budgeted radius max cover:

```cmd
cvr.bat max-cover --input ".\coverage\alphapnts.csv" --radius 1000 --unit meters --exclude-self --budget 5
```

## Troubleshooting

### `los-project` is not listed in `coverage_cli.py --help`

That is expected. `los-project` is dispatched by `cvr.bat` to `coverage\scripts\coverage_los_project.py`.

Use:

```cmd
cvr.bat los-project --help
```

### `No module named osgeo`

`los-cover`, `los-project`, and `los-bundle` need GDAL/QGIS runtime support for DEM or QGIS project work.

For `los-cover`, the CLI should try to re-run itself through OSGeo4W QGIS Python. If that fails, run:

```cmd
cvr.bat los-project --input-dir ".\coverage\results\ ".\coverage\scripts\coverage_cli.py" los-cover --help
```

If that file does not exist, install QGIS Desktop via OSGeo4W or update `cvr.bat` / `coverage_cli.py` to the correct QGIS Python path.

### Point outside DEM extent

At least one input point is outside the DEM coverage area.

Fix by using a DEM that covers all points, or remove/split the outside points.

### DEM nodata at a point or sample

The DEM has nodata where an endpoint or LOS sample is needed.

Fix by using a more complete DEM, filling nodata, or adjusting the point set.

### No `meaningful_lines.geojson` features

This means no LOS-valid pair induced a line family with three or more points inside the line tolerance. The solver can still select 2-point LOS segments, and `selected_segments.geojson` can still be valid.

Try a larger `--line-tolerance` if the intended line families are nearly collinear but not within the current tolerance.

### Too few covered points

Check:

- `point_status.csv` for `reachable` and `covered`.
- `manifest.json` for `uncovered_point_ids`.
- DEM coverage and vertical units.
- `--max-segment-length`.
- `--point-height` or `--point-height-m`.
- `--sample-step` or `--sample-step-m`.
- Whether the point coordinates are correct.

### Slow LOS runs

The pairwise LOS workflow considers point pairs, so cost grows roughly with `n^2`.

To reduce runtime:

- Lower `--max-segment-length`.
- Increase `--sample-step`.
- Split a large point set into regions.
- Use `--solver greedy` if exact refinement is unnecessary.

## Command Reference

Top-level CLI commands:

```text
inspect
matrix
full-cover
max-cover
qgis-bundle
los-cover
```

Additional `cvr.bat` commands:

```text
los-project
los-bundle
```

Help commands:

```cmd
cvr.bat inspect --help
cvr.bat matrix --help
cvr.bat full-cover --help
cvr.bat max-cover --help
cvr.bat qgis-bundle --help
cvr.bat los-cover --help
cvr.bat los-project --help
cvr.bat los-bundle --help
```

## Choosing The Right Workflow

Use `matrix` when:

- You want all demand/candidate radius relationships.
- You need a table for further analysis.

Use `full-cover` when:

- You want enough candidates to cover all reachable demand points under a radius rule.

Use `max-cover` when:

- You have a fixed site budget and want the best radius coverage.

Use `qgis-bundle` when:

- You want a simple QGIS visualization of radius coverage.

Use `los-bundle` when:

- You specifically need the older radius-first LOS workflow.

Use `los-cover` plus `los-project` when:

- You want the current "LOS Segment Cover" QGIS project.
- You want direct point-to-point terrain LOS segments.
- You want selected LOS segment families and per-point coverage status.
