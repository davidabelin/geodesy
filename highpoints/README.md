# highpoints

`python -m highpoints` — finds high/low/representative elevation points
within a boundary using a DEM, and clusters the results. This is the
successor to a set of standalone "quadrant/octant highpoint" scripts, which
are kept under `legacy/` for reference. See [highdeas.md](highdeas.md) for
the full cleanup rationale and design notes behind the current package.

## Commands

### `high-grid`

Lays a parallelogram grid across a four-corner boundary (`W`, `N`, `E`, `S`
corners — also basis-vector names, where `W->N` is the `u` axis and `W->S`
is the `v` axis) and, for every grid cell, samples the DEM to find:

- the highest pixel (`hi`)
- the lowest pixel (`lo`)
- the pixel closest to the cell's mean elevation (`avg`)

Ties are resolved deterministically (closest to cell centroid, then by DEM
row/column). Cells are named `U##_V##`.

```
python -m highpoints high-grid --grid-size 10 --dem ..\data\tif\dc_dem.tif
```

Key options: `--boundary <file>` or `--corners "x,y;x,y;x,y;x,y"` (default
boundary is `crnrpoints.csv`), `--grid-size 1-100`, `--offset-angle` to
rotate the grid (clockwise-positive), `--all-touched` to include any pixel
touched by a cell rather than only center-in-cell pixels, and
`--parallelogram-tolerance-m` to control how strict the four-corner
parallelogram check is. Outputs default to `out/highgrid.gpkg` (DEM CRS),
a companion `highgrid_out.csv`, and a `.qgz` QGIS project archive (skip with
`--no-project`).

### `cluster`

Runs cluster analysis (DBSCAN, K-means, or agglomerative) over a
`high-grid` CSV output to find local maxima among the sampled points.

```
python -m highpoints cluster --input-csv out\highgrid_out.csv --method dbscan --gpkg-output out\clusters.gpkg
```

Key options: `--point-types hi lo avg` (default: all three), `--dims
1d/2d/3d`, `--method dbscan|kmeans|agglomerative` with its parameters
(`--eps`/`--min-samples` for DBSCAN, `--n-clusters`/`--n-clusters-agg`),
`--scale` to standardize features first, and `--csv-output`/
`--gpkg-output`/`--plot-2d`/`--plot-profile` for outputs.

## Layout

- `cli.py`, `_paths.py`, `__init__.py`, `__main__.py` — package/CLI
  scaffolding; `_paths.out_dir()` resolves relative outputs under
  `highpoints/out/`.
- `highgrid.py` — the grid-building and DEM-sampling implementation.
- `clusters.py` — clustering, local-maxima detection, plotting, and
  GeoPackage/CSV writers for `high-grid` output.
- `crnrpoints.csv` — default four-corner boundary for `high-grid`.
- `get_alts.py` — standalone script that samples DEM elevations for a CSV
  of points (`LOC,LAT,LON`) and writes GeoJSON; not yet folded into the
  package CLI.
- `templates/` — QGIS `.qml` styles and a `.qgz` template used when writing
  generated QGIS projects (grid cells, hi/lo/avg points, cluster maxima).
- `legacy/` — pre-package scripts (`highpoints.py`, `octant_heights.py`,
  `pipeline.py`) that hardcode DC cardinal control points (`N`/`S`/`E`/`W`/
  `C`) and quadrant/octant sector names; kept for reference, not imported
  by the current CLI.
- `input/`, `output/` — local working directories for point CSVs and
  generated grid/cluster results (gitignored; not part of the tracked repo
  aside from the files listed above).

## Notes

- The DEM defaults to `data/tif/dc_dem.tif` relative to the repo root.
- `high-grid` outputs are kept in the DEM's CRS end-to-end (grid, points,
  and QGIS project), so downstream tools should expect that CRS rather than
  assuming WGS84/NAD83 lat-lon.
- Corresponding tests live in the repo-level `tests/` directory
  (`test_highgrid.py`, `test_clusters.py`), not inside this package.
