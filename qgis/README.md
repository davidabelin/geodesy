# qgis

QGIS Desktop projects, layer styles, and PyQGIS automation scripts for
visualizing this repo's geodesy outputs — the modern DC street grid, the
1791-92 boundary stones, the L'Enfant and Ellicott historic plans, and the
1892 Hawkins topographic map.

All PyQGIS scripts here run under the OSGeo4W-provided QGIS Python runtime
(`python-qgis.bat`), **not** a pip-installed `qgis`/`gdal` stack — plain
`rasterio`/`geopandas`/`shapely`/`pyproj` code elsewhere in the repo doesn't
need this runtime and should stay that way.

## Shared runtime helper

- **`qgis_runtime.py`** — bootstraps PyQGIS from an OSGeo4W install:
  locates the repo root, finds a PROJ database compatible with the
  installed GDAL/QGIS build, prefers the QGIS 4.x runtime, adds QGIS's
  Python paths, and works around a Windows WMI lookup issue during
  `platform` calls. Standalone scripts and QGIS-console snippets alike
  import this before touching `qgis.core`/`osgeo`.

## Hawkins 3D topography pipeline

A CLI pipeline that turns the scanned 1892 Hawkins topographic map into
QGIS terrain-review assets, without treating the scan itself as a real DEM.
Full design/status notes: [Hawkins/HAWKINS_3D_HANDOFF.md](Hawkins/HAWKINS_3D_HANDOFF.md).

- `hawkins_3d_pipeline.py` — CLI entrypoint (`--mode audit|pilot|full`).
- `hawkins_3d_core.py` — pipeline implementation: raster audit/scoring,
  work-package GeoPackage (`Hawkins/3D map/hawkins_work.gpkg`) with
  `hawkins_aoi`, `hawkins_contours_candidates` (auto-extracted, DEM-hinted,
  *not* trusted elevations), and `hawkins_contours_manual`/
  `hawkins_labels_manual` layers for hand-vetted work.
- `hawkins_3d_console_helper.py` — thin wrapper (`run_hawkins(mode=...)`)
  for rerunning the pipeline from inside the QGIS Python console.
- `run_hawkins_3d_pipeline.bat` — Windows launcher that calls the pipeline
  through `python-qgis.bat`.

  ```bat
  qgis\run_hawkins_3d_pipeline.bat --mode audit
  qgis\run_hawkins_3d_pipeline.bat --mode pilot
  ```

  Note: close any open Hawkins workbench `.qgs` project before rerunning —
  Windows file-locks the generated raster/GeoPackage outputs otherwise. As
  of the last handoff, vertical control (assigning real historical
  elevations to auto-traced contours) is the main unresolved problem.

## Other automation scripts

- **`dcdem_custom_contours.py`** — QGIS-console/standalone script that
  classifies DEM values within a DC "diamond" boundary into 10 outer
  deciles with a repeated 10-step inner pattern, builds a matching
  parallel-quadrilateral grid, rasterizes cells to masks, and writes the
  result as a styled GeoPackage layer. Conceptually the precursor to
  `highpoints/highgrid.py`'s grid/mask approach.
- **`apply_centerline_qgis_style.py`** — applies consistent line/marker
  symbology (via `QgsProperty`-driven expressions) to the
  `ew_segments`/`ns_segments`, `ew_midpoints`/`ns_midpoints`, and
  `ew_extensions`/`ns_extensions` layers produced by
  `roadways/centerlines/centerlines.py`.

## QGIS projects and layer packages

- `Ellicot/` — Ellicott's 1791-92 plan of the federal territory: scanned
  plan raster, NAD83-reprojected QGIS projects (`Ellicott_Plan_NAD83.qgs`),
  and a combined "hawkicott" overlay comparing it against the Hawkins
  topography.
- `Hawkins/` — the raw Hawkins topography source (`.img`, GIMP `.xcf`
  working files) and generated 3D-pipeline workbench projects (see above).
- `LEnfant/` — the L'Enfant Plan: georeferenced plan raster and the
  `L_Enfant_Centerlines` shapefile (the historic counterpart to the modern
  centerlines in `roadways/centerlines/`).
- `canals/` — an 1851 downtown canals reference image.
- `Schematic Detail/`, `Shaded Relief/` — large raster working files (GIMP
  `.xcf`, multi-hundred-MB `.tif`/`.img` mosaics) for detailed schematic
  and historic shaded-relief rendering; not checked into git.
- Root-level `.qgs`/`.qgs~`/`_attachments.zip` projects — a long-running
  set of exploratory QGIS projects/snapshots (`NAD83.qgs`, `stones.qgs`,
  `washington_dc.qgs`, `pseudomerc_*.qgs`, `pythotiles.qgs`,
  `dc_dem_contours.qgs`, `hilltops.qgs`, `multilayered.qgs`,
  `from_scratch.qgs`, `scratch.qgs`, etc.) — treat these as working
  checkpoints rather than a single canonical project; only a handful
  (listed under Notes) are tracked in git.
- `*.qlr` files — standalone QGIS layer packages (colored avenues/circles,
  Mapzen colorshade contours, map reference points) that can be dragged
  into any project.
- `stylish_A.db`, `symbology-style.db` (repo root) — shared QGIS style
  databases.

## Notes

- Tracked in git: `qgis_runtime.py`, the Hawkins pipeline scripts +
  handoff doc, `apply_centerline_qgis_style.py`, `run_hawkins_3d_pipeline.bat`,
  the `Ellicot/`, `LEnfant/`, and `canals/` project files/rasters listed
  above, and the two `Hawkins/3D map/*.qgs` workbench projects. Everything
  else in this directory (root-level `.qgs`/`.qlr` snapshots, `Schematic
  Detail/`, `Shaded Relief/`, `dcdem_custom_contours.py`, style databases,
  and most raster working files) is local/generated and excluded by the
  repo's `.gitignore`.
- `.qgs~` files are QGIS's own auto-save backups of the same-named `.qgs`
  project; `_attachments.zip` files are QGIS's bundled style/asset archive
  for a project and should be kept alongside it.
