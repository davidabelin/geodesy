# High Points Sub-Geodesy Project

Mostly centered on `geodesy\highpoints` with reference to material throughout the `geodesy` workspace.

## What's there

Cleanup review summary: `highpoints` is currently a mixed legacy workbench, not a clean single-purpose module. It contains a small Python package/CLI, several standalone DEM scripts, QGIS project state, generated KML/GeoJSON/CSV outputs, a georeferenced image pair, and Python bytecode cache files. Nothing under `highpoints` is tracked by git at the moment; many outputs are already ignored by `.gitignore` (`*.kml`, `*.geojson`, `*.csv`, `*.zip`, `*.qgs~`, `__pycache__/`).

### Python package shell

- `__init__.py`
  - Minimal package metadata only.
  - Defines `__version__ = "0.1.0"`.
  - Keep if `highpoints` remains an importable package.

- `__main__.py`
  - Lets the package run as `python -m highpoints`.
  - Delegates directly to `cli.main()`.
  - Keep if we want a CLI after the refresh.

- `_paths.py`
  - Defines `package_root()` and `out_dir()`.
  - Current CLI uses `out_dir()` so relative outputs go under `highpoints/out/`.
  - Keep or fold into a cleaner CLI utility module.

- `cli.py`
  - Defines the current CLI command:
    - `python -m highpoints hidrant-peaks --dem <DEM> [--buffer-m ...] [--separation-m ...] [--output-kml ...] [--output-csv ...]`
  - It is currently only a wrapper around the hidrant-sector peak finder in `pipeline.py`.
  - Reusable as the place to add a future `grid-heights` command.

- `pipeline.py`
  - Contains `run_hidrant_peaks(...)`.
  - Imports `get_top_ten_peaks_quadrant()` from `highpoints.py`.
  - Builds four hidrant sectors (`We`, `So`, `Ea`, `No`), converts output to a GeoDataFrame, writes KML, and optionally writes CSV.
  - Useful as a pattern for making scripts callable from a package CLI.
  - Less useful as a long-term abstraction unless the hidrant workflow remains supported.

### Main legacy DEM scripts

- `highpoints.py`
  - Main reusable legacy algorithm.
  - Hardcodes DC-ish cardinal control points: `N`, `S`, `E`, `W`, `C`.
  - Defines `get_top_ten_peaks_quadrant(dem_path, triad, buffer_m=100, separation_m=100)`.
  - Builds a triangular polygon from three named points, buffers it in a projected CRS, masks the DEM to that polygon, finds high pixels, and excludes nearby cells using a circular mask.
  - Has newer tie-handling/randomization logic for equal max elevations.
  - CLI mode writes `Highpoints_per_Hidrant.kml` and optionally `highpoints_out.csv`.
  - Reusable pieces:
    - DEM masking with `rasterio.mask.mask`.
    - CRS handling via GeoPandas and PyProj.
    - Pixel-to-coordinate conversion with `rasterio.transform.xy`.
    - Separation-radius peak picking.
  - Cleanup concerns:
    - Hardcoded control points and sector names.
    - Legacy "quadrant"/"hidrant" terminology mixed together.
    - Random tie/jitter behavior makes exact repeatability harder.
    - Direct output filenames are legacy and not organized under a clean output directory.
    - KML export from the standalone script appears to have produced malformed duplicate fields in at least one output.

- `octant_heights.py`
  - Older experiment similar to `highpoints.py`, but for eight octant-ish areas.
  - Hardcodes more points: `N`, `S`, `E`, `W`, `SW5`, `SE5`, `NE5`, `NW5`, `C`.
  - Hardcodes `dem_file = "qgis/dc_dem.tif"`, which does not match the currently found DEM path (`data/tif/dc_dem.tif`).
  - Writes `highpoints/octant_x_2000.geojson`.
  - Reusable pieces:
    - Same basic DEM mask and peak extraction pattern as `highpoints.py`.
    - Some alternate subdivision ideas may be useful conceptually.
  - Cleanup concerns:
    - Looks experimental.
    - Hardcoded paths and output.
    - GeoJSON outputs declare EPSG:32618 but contain lon/lat-looking coordinates, so CRS metadata is suspect.
    - Probably better moved to `legacy/` unless octant outputs are still actively useful.

- `get_alts.py`
  - Samples DEM elevations at point coordinates from a CSV.
  - Expects CSV columns `LOC`, `LAT`, `LON`.
  - Writes GeoJSON and prints CSV-ish output.
  - Hardcoded paths:
    - `data\map_refpnts.csv`
    - `data\dc_dem.tif`
    - `data\map_refpnts.geojson`
  - Actual DEM found in this workspace: `data/tif/dc_dem.tif`.
  - Reusable pieces:
    - Coordinate transform before DEM sampling.
    - `rasterio.sample()` point-elevation pattern.
    - Meters-to-feet conversion.
  - Cleanup concerns:
    - Standalone, hardcoded, verbose, and probably should be folded into a utility function if kept.

### QGIS project state

- `highpoints.qgs`
  - QGIS project saved from QGIS `4.0.0-Norrkoping`.
  - Contains 15 layers.
  - Mostly references external sources, not the local generated files in this folder.
  - Important referenced sources:
    - `../data/kmz repos/High Points Layers.kmz`
    - `../border/Stones Styled.kmz`
    - Mapzen terrain tile URLs.
  - Contains a stored 3D map dock/view block.
    - The 3D view exists in project XML.
    - It is saved as not open (`isOpen="0"`).
    - Terrain uses the Mapzen contours layer as DEM terrain with exaggeration `3`.
  - Useful for understanding QGIS layer styling and 3D view state.
  - Cleanup concern: because it points to external KMZs and old layer names, it may be better treated as legacy reference unless we want a new clean QGIS project generated by code.

- `highpoints.qgs~`
  - QGIS backup from QGIS `3.44.2-Solothurn`.
  - Similar layer set, older paths.
  - Move to `legacy/` or discard once the active `.qgs` status is decided.

- `highpoints_attachments.zip`
  - QGIS attachment archive.
  - Contains a single styles database (`EQeCMW_styles.db`).
  - Keep only if preserving the current `highpoints.qgs`; otherwise legacy/discard.

### Generated or reference artifacts in `highpoints`

- `dc_dem_highpoints.kml`
  - Large KML, about 1.27 MB.
  - 6,801 point placemarks.
  - Document name says "huge dc_highpoints file".
  - Looks like a generated high-point cloud/reference layer.
  - Likely not source code; move to `legacy/` unless still needed as a reference dataset.

- `dc_top_highpoints_quads.geojson`
  - 40 point features.
  - Ten points each for `NW`, `SW`, `SE`, `NE`.
  - Properties: `name`, `quadrant`, `rank`, `elevation`, `marker-color`.
  - Legacy quadrant result snapshot.

- `Highpoints_per_Hidrant.kml`
  - 40 point placemarks.
  - Ten points each for `We`, `So`, `Ea`, `No`.
  - Generated from the hidrant-sector workflow.
  - KML structure looks malformed in places: duplicate `<name>` and `<description>` fields, and ExtendedData values do not line up cleanly with field names.
  - Treat as generated output, not canonical source.

- `highpoints_out.csv`
  - 40-row CSV companion to `Highpoints_per_Hidrant.kml`.
  - Columns: `Name`, `Lat`, `Lon`, `Alt`, `Sector`, `Rank`.
  - Cleaner than the KML and useful as a legacy output snapshot.
  - Still generated data, so probably `legacy/` unless needed for comparison tests.

- `octant_highpnts.geojson`
  - 80 point features.
  - Ten points each for `NW`, `NW5`, `SW`, `SW5`, `SE`, `SE5`, `NE`, `NE5`.
  - Legacy octant result snapshot.
  - CRS metadata should be treated with suspicion.

- `octant_og_500.geojson`
  - 80 point features.
  - Same octant grouping shape as `octant_highpnts.geojson`.
  - Likely an older parameter/output variant.
  - Generated output.

- `octant_x_2000.geojson`
  - 80 point features.
  - Same octant grouping shape, apparently from `octant_heights.py` with a larger separation setting.
  - Generated output.

- `Hilltops.kml`
  - 93 point placemarks.
  - Mostly named `H*`, plus at least one named `NO`.
  - Looks more curated/manual than the generated peak snapshots.
  - Possible keep candidate if those hilltop points are still reference data.
  - Otherwise move to `legacy/`, not trash immediately.

- `PeakPnts.kml`
  - 24 point placemarks named `Pk01` through `Pk24`.
  - Many points are outside DC.
  - Looks like broader regional peak reference data.
  - Possible keep candidate only if broader regional peak points remain part of the new purpose.

- `highpoint_results.jpg`
  - 1024x768 raster image.
  - Likely a map/screenshot output.
  - Pairs with `highpoint_lines.pgw`.
  - Keep only if the static visualization matters.

- `highpoint_lines.pgw`
  - World file for `highpoint_results.jpg`.
  - Six georeferencing values.
  - Only useful together with the JPG.

- `__pycache__/`
  - Python bytecode cache.
  - Contains Python 3.12 and 3.14 `.pyc` files.
  - Safe to delete during cleanup.

### Related workspace pieces useful for the new direction

- `data/tif/dc_dem.tif`
  - Actual DEM found in the workspace.
  - This should probably be the default/reference DEM path for `gridheights.py`.

- `qgis/qgis_runtime.py`
  - Existing helper for bootstrapping PyQGIS from OSGeo4W/QGIS on Windows.
  - Handles PROJ data discovery and QGIS Python paths.
  - Reuse this instead of re-solving QGIS 4 runtime setup inside `highpoints`.

- `qgis/dcdem_custom_contours.py`
  - Very relevant to the planned `gridheights.py`.
  - Already contains logic for:
    - extracting boundary points,
    - transforming points into the DEM/project CRS,
    - building a parallel quadrilateral/subdiamond grid,
    - rasterizing cell geometries into masks,
    - reading DEM windows,
    - writing GeoPackages with OGR,
    - loading outputs into QGIS.
  - It is currently a hardcoded QGIS-console-style script, but the geometry/grid/mask ideas are reusable.

- `coverage/scripts/coverage_los_project.py`
  - Good reference for writing GeoPackages and QGIS projects programmatically.
  - Uses `qgis_runtime.init_qgis_app()`.
  - Writes a `.gpkg`, loads layers, styles them, writes a `.qgs`, and emits practical "open 3D view manually" instructions.

- `coverage/scripts/coverage_los_qgis.py`
  - Useful reference for QGIS loader-script generation and 3D vector styling.
  - It still tells the user to open `View -> New 3D Map View` manually, which suggests fully launching a 3D map view from a standalone CLI may remain awkward or version-sensitive.

### Suggested cleanup classification

- Keep near root:
  - `highdeas.md`
  - `__init__.py`
  - `__main__.py`
  - `_paths.py`
  - `cli.py`
  - `pipeline.py` only if hidrant peaks remain a supported command
  - a refactored/new `gridheights.py`
  - possibly a shared DEM utility module extracted from `highpoints.py` / `get_alts.py`

- Move to `legacy/` unless explicitly kept:
  - `highpoints.py`
  - `octant_heights.py`
  - `get_alts.py`
  - `highpoints.qgs`
  - `highpoints.qgs~`
  - `highpoints_attachments.zip`
  - all KML/GeoJSON/CSV/JPG/PGW output snapshots

- Delete outright:
  - `__pycache__/`

- Decide manually before trashing:
  - `Hilltops.kml`
  - `PeakPnts.kml`
  - `highpoint_results.jpg` plus `highpoint_lines.pgw`
  - These look more like possible curated reference assets than pure generated outputs.

## What we want there *now*, ASAP

### Keep anything useful, but avoid "over-keepage"

- Completed mostly as-suggested above ^
- cleaned-out unused unnecessary clutter from `highpoints` --> a `legacy` subfolder

### New directions, wider focus

- **Priority** `gridheights.py`
  - overlay a grid within boundary points on a map and in each resulting 'cell' of the grid determine the highest peak (from dc_dem.tif)
  - boundary points must form a parallel quadrilateral; gridlines run "parallel" to boundary lines
  - variable gridsize; takes a value from 1 to 100 by which to divide the boundary lines (so anywhere from 1x1 to 100x100 grid cells)
    - maybe other specs will be specifiable in other ways later
  - produce a geopackage to import into QGIS Desktop to visualize

- QGIS integration
  - fix the issues plaguing use of QGIS, gdal, etc. since installing to v4.0.1 (but it doesn't crash all the time now, at least!)
  - 3D Map View
    - can it be launched from code or CLI?
    - grid from `gridheights.py` --> "large-grained" 3D map?

## Ideas for future development

- Coming soon
