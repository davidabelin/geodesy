<!-- Auto-generated guidance for AI coding agents in this repository -->
# Copilot / AI Agent Instructions (concise)

Enable Claude Haiku 4.5 for all clients: model preference = `claude-haiku-4.5`

Purpose
- Short, actionable guidance to get an AI coding agent productive in this repo.

Big picture
- This repository is a small, procedural geodesy toolkit (Python). Key scripts:
  - `sailpath.py` — batch or single-run CLI that builds sailing paths and KML output.
  - `geometry_v0.py` — collection of spherical/ellipsoidal geodesy utilities (haversine, geodetic, spirals, DEM utilities).
  - `data/` — sample input CSVs and KMLs; many scripts expect `data/` relative paths.

What to prioritize
- Preserve CLI behavior: many functions expose command-line subcommands; keep argument names and output CSV/KML contracts unchanged.
- Avoid changing units: defaults are miles (unless explicitly noted). `sailpath.py` uses meters for `--step-dist` flag; `geometry_v0.py` defaults to miles.

Developer workflows & run commands
- Batch run (example):
  ```bash
  python sailpath.py --input-csv data/diagonal_stones.csv --output-kml data/diagonal_stones.kml
  ```
- Single-run example:
  ```bash
  python sailpath.py --loc0 S --lat0 38.9 --lon0 -77.0 --loc1 N --lat1 39.0 --lon1 -76.9 --step-dist 100
  ```

Key dependencies (discoverable in files)
- `pyproj` (Geod) — core geodesic ops
- `scipy` (optimize) — root finding and refinements
- Optional: `numpy`, `rasterio`, `shapely`, `geopandas`, `simplekml`, `cartopy` for I/O and plotting features

Project-specific conventions
- CLI-first: functions are written to be called from the CLI; prefer preserving the existing `argparse` flags and CSV headers.
- CSV header contract for `sailpath.py`: `LOC0,LAT0,LON0,LOC1,LAT1,LON1,STEP_DIST` (exact header names expected).
- Color / binning: `sailpath.py` implements a 12-bin azimuth naming scheme (e.g. `SN`, `WE`, `WN`, `SE`, `oob`). Keep these names stable when editing style generation.

Integration points & cross-component patterns
- Many functions return lists of `(lat, lon)` tuples or emit CSV/KML lines. When modifying, keep the output formatting (precision/ordering) to avoid breaking downstream consumers.
- DEM/elevation flow in `geometry_v0.py` is optional and guarded by imports — changes should maintain graceful fallback if rasterio/geopandas aren't installed.

Editing guidance for AI agents
- When adding features, include small unit tests or a reproducible CLI example in a comment or README fragment.
- Run quick smoke checks by executing the example `sailpath.py` command above; confirm CSV header output and that a KML file is written.
- Avoid broad refactors that change public names in `sailpath.py` or `geometry_v0.py` without updating corresponding CLI help strings.

If you need to actually enable model access
- This repo cannot enable models on the platform itself. The requested phrase is documented above to instruct maintainers/operators to set the org or deployment preference to `claude-haiku-4.5` for clients.

If anything here is unclear or you'd like the file to include more detail (examples, unit tests, or CI steps), say which area to expand.
