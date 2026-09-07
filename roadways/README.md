# roadways

Street-centerline extraction and roadway-geometry analysis for DC, built on
the DC GIS `Street_Centerlines` and `Roadway_SubBlock` datasets, plus the
historic L'Enfant Plan centerlines.

## Main tools

- **`centerlines/centerlines.py`** (launched via `cl.bat` from the repo
  root) — the primary, actively-developed centerline analysis tool.
  Classifies segments as N-S/E-W/other by azimuth, merges short segments
  along a street per the rules in `centerlines/merge_rule.md`, and reports
  per-segment and per-street length statistics.

  ```
  cl.bat --input roadways\data\DC_Street_Centerlines\Street_Centerlines_2013.shp --units feet
  ```

  Key options: `--output` (CSV), `--summary-output`/`--chart-output`
  (per-street CSV/PNG), `--gis-output` (GeoPackage/Shapefile — reuses the
  `--output` stem when given `gpkg`/`shp`), `--units
  feet|miles|poles|m|km`, `--ell wgs84|nad83|pseudomerc|none` (fallback
  ellipsoid when the input has no CRS), and `--min-segment-length` to
  trigger the short-segment merge pass.

- **`roadway_analysis.py`** — classifies `Roadway_SubBlock.geojson`
  centerlines by cardinal azimuth (EW/NS bands), merges same-street
  segments, and writes a classified KML plus per-direction distance CSVs.

- **`streets.py`** — earlier street-geometry/merging exploration over the
  same `Roadway_SubBlock.geojson` (2D-flattening, segment merging,
  plotting); largely superseded by `centerlines/centerlines.py`.

- **`triangle_measured.py`** — ad hoc great-circle distance/angle
  calculations between a handful of hardcoded DC reference points (`K*`,
  `J*`, `C*`, `O`) and named golden-ratio/trig-derived candidate points.

- **`read_data_img.py`** — inspects a raster (`.img`/GeoTIFF) file's
  metadata (driver, dimensions, band count, CRS, bounds, transform) via
  rasterio; a diagnostic utility, not a pipeline step.

- **`geojson_to_kml.py`** — converts a colored roadway/avenue GeoJSON
  (RGBA fill from matplotlib colormaps) into styled KML.

## Layout

- `centerlines/` — the centerline tool, its QGIS projects (plain and
  NAD83), generated GeoPackages/CSVs/PNGs for the modern street grid
  (`cl.*`) and the L'Enfant Plan (`clenfant.*`), and `merge_rule.md`
  documenting the short-segment merge algorithm in detail.
- `data/` — source vector data: `DC_Street_Centerlines/` shapefile,
  `Roadway_SubBlock.geojson`, downtown roads GeoJSON, and legacy colored
  KMZ exports.
- `legacy/` — earlier/retired script versions and their data.
- `pipelines/` — placeholder for a future consolidated roadway pipeline
  (currently empty besides bytecode cache).
- `out/` — generated KML/CSV outputs from centerline and classification
  runs (smoke-test and named runs); not part of the tracked repo.
- `images/` — rendered map PNGs (colored grids, colorized circles,
  contour/street overlays) and their `.pgw` world files.
- `custom_color_ramp.qml`, `strid_turbo.qml` — QGIS styling for
  street/segment layers.
- `gridmap.txt` — notes/idea sketch for a possible "moiré pattern between
  street spacing sequences" analysis; not yet implemented.

## Notes

- Only `centerlines/` (scripts, `.qgs`/`.qml` styles, and small generated
  GeoPackages/PNGs) and the top-level `.py`/`.qml`/`.txt` files are tracked
  in git; `data/`, `legacy/`, `pipelines/`, `out/`, and `images/` are
  git-ignored working directories — see the repo-root `.gitignore`.
- `centerlines.py` shares its unit-conversion constants
  (`EARTH_RADIUS_MILES`, `METERS_PER_MILE`, etc.) with `geodetics.py` at
  the repo root; keep them in sync if either changes.
