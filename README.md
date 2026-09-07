# geodesy

Python toolkit for geodetic calculations and geospatial analysis centered on
Washington, D.C.: the L'Enfant Plan street grid, the original 1791-92
boundary stones, DEM-derived terrain analysis, and line-of-sight coverage
between points. Most tools are argparse CLIs that read/write CSV, KML, and
GeoPackage/QGIS project files so results can be inspected in QGIS Desktop.

## Layout

| Path | What it is |
| --- | --- |
| [geodetics.py](geodetics.py) | Core geodesy CLI: direct/inverse geodetic problems, angle-and-distance "walks," golden-spiral generation, DEM altitude sampling, sail paths. Built on `pyproj.Geod`. |
| [geodetics_v0.py](geodetics_v0.py) | Earlier standalone version of the same toolkit, kept for reference/diffing. |
| [sailpath.py](sailpath.py) | Batch/single-run CLI that walks great-circle paths between points at a fixed step distance and emits CSV + KML. |
| [point_jiggler.py](point_jiggler.py) | Iteratively nudges a set of KML points to satisfy geometric, triangle, and DEM-altitude constraints simultaneously. |
| [highpoints/](highpoints/highdeas.md) | `python -m highpoints` package: lays a parallelogram grid over a boundary and finds the highest/lowest/average-elevation cell in each, plus clustering analysis on the results. `legacy/` holds earlier quadrant/octant peak-finding scripts. |
| [coverage/](coverage/docs/coverage_tool_usage.md) | `cvr.bat` toolset for point-to-point visibility: radius-based coverage selection and DEM-based line-of-sight (LOS) segment cover, with QGIS project generation. |
| [roadways/](roadways/roadway_analysis.py) | Street-centerline extraction/analysis (`centerlines.py`, `cl.bat`) and general roadway geometry scripts. |
| [border/](border) | Reference data and QGIS projects for the DC boundary stones. |
| [qgis/](qgis) | QGIS projects, styling, and PyQGIS helper scripts (e.g. `qgis_runtime.py` for bootstrapping PyQGIS from an OSGeo4W install), including the Ellicott Plan, L'Enfant Plan centerlines, and Hawkins topography workbenches. |
| [spirals/](spirals) | Golden-spiral / Fibonacci-spiral generation and plotting, with KML export. |
| [tests/](tests) | Pytest suite covering highgrid, clustering, coverage core/solver/CLI, and centerline logic. |

Several other working directories (`data/`, `dev/`, `src/`, `apps/`) exist
locally for scratch input data, experiments, and a small in-progress web app,
but are git-ignored and not part of the tracked repo — see
[.gitignore](.gitignore).

## Setup

```bash
python -m venv geodenv
geodenv\Scripts\activate.bat        # Windows
pip install -r requirements.txt
```

`geodesy_env.bat` automates the same steps (venv activation + `pip install`)
for a Windows shell.

Core dependency is `pyproj`. Full raster/vector analysis additionally needs
`rasterio`, `geopandas`, `shapely`, `pandas`, `pyogrio`, `scipy`, and
`scikit-learn` (all in `requirements.txt`); most modules degrade gracefully
if the optional geo-stack isn't installed. QGIS/PyQGIS work expects the
OSGeo4W runtime on Windows (`python-qgis.bat`), not a pip-installed
qgis/gdal stack.

## Usage

Root-level `.bat` launchers wrap the most common entry points:

```bat
geo.bat direct --lat 38.9 --lon -77.0 --azimuth 45 --distance 10 --unit miles
geo.bat inverse --lat1 38.9 --lon1 -77.0 --lat2 39.0 --lon2 -76.9
geo.bat sailpath --input-csv data\diagonal_stones.csv --output-kml data\diagonal_stones.kml
geo.bat alt --input-csv data\map_refpnts.csv --dem data\tif\dc_dem.tif

pj.bat input.kml targets.csv tarcrits.csv --dem dem.tif output.kml

cl.bat --input roadways\data\DC_Street_Centerlines\Street_Centerlines_2013.shp

cvr.bat los-cover --input coverage\alphapnts.csv --dem data\tif\dc_dem.tif ^
    --output-dir coverage\results\alpha_los --max-segment-length 25000 --line-tolerance 15
cvr.bat los-project --input-dir coverage\results\alpha_los --output-dir coverage\results\alpha_los_qgis

python -m highpoints high-grid --dem data\tif\dc_dem.tif --grid-size 10
python -m highpoints cluster --input highpoints\out\highgrid_out.csv
```

Run `geo.bat -h`, `cvr.bat -h`, or `python -m highpoints -h` for the full
subcommand list, and see [coverage/docs/coverage_tool_usage.md](coverage/docs/coverage_tool_usage.md)
and [highpoints/highdeas.md](highpoints/highdeas.md) for workflow-level
documentation of those two toolsets.

## Tests

```bash
pytest tests
```

Some tests (LOS/QGIS project generation) are skipped automatically when a
DEM backend or the OSGeo4W QGIS Python runtime isn't available locally.
