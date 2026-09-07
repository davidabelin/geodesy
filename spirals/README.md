# spirals

Golden-ratio and Fibonacci spiral generation, both as `matplotlib` plots
and as geodesic KML for viewing on a real map.

## Tools

- **`draw_kml_spirals.py`** — the main KML tool, run as a CLI with
  subcommands (`python spirals/draw_kml_spirals.py <subcommand> ...`):
  - `connect` — connects `(lat, lon)` points from a CSV (grouped by `LOC`
    prefix) into KML line strings.
    ```
    python draw_kml_spirals.py connect data\refpnts.csv out\output_spirals.kml --include-points
    ```
  - `fib` — draws a single Fibonacci square spiral (squares and/or
    quarter-circle arcs) anchored at a given lat/lon, using true geodesic
    distances (`pyproj.Geod`).
    ```
    python draw_kml_spirals.py fib --lat 38.9 --lon -77.03 --terms 10 --base 100 --units m --heading 45
    ```
    Key options: `--terms`, `--base`/`--units` (m/km/ft/mi), `--cw` (default
    counter-clockwise), `--heading` (clockwise degrees from North),
    `--draw squares|arcs|both`, `--arc-points`, `--ellipsoid
    WGS84|NAD83|sphere`.
  - `fibcsv` — draws one Fibonacci spiral per row of a `LOC,LAT,LON` CSV,
    using the same options as `fib` (plus `--no-anchor` to omit the anchor
    placemark).

  Outputs default to `spirals/out/`. Requires `simplekml` and `pyproj`
  (both optional imports; the script degrades its help text if missing
  rather than hard-failing on import).

- **`gnomon_spiral_plot.py`** — plots a golden gnomon spiral (isosceles
  "gnomon" triangles scaled by the golden ratio `PHI`, connected by minor
  arcs) with matplotlib. Not geodesic — a pure abstract-geometry diagram.
  ```
  python spirals/gnomon_spiral_plot.py --steps 8 --base 1.0 --save gnomon_spiral.png
  ```
  Options: `--steps`, `--base`, `--ccw` (default clockwise), `--no-triangles`
  (arcs only), `--save <path>`, `--show`, `--pad`.

- **`square_spiral_plot.py`** — plots the classic Fibonacci square spiral
  (nested squares + quarter-circle arcs, six-color palette) with matplotlib.
  ```
  python spirals/square_spiral_plot.py --terms 8 --base 1.0 --save fib_spiral.png
  ```
  Options: `--terms`, `--base`, `--cw` (default counter-clockwise),
  `--no-labels`, `--no-centers`, `--save <path>`, `--show`, `--pad`.

## Layout

- `Spirals_DeepDive.pdf` — background/reference material on spiral
  geometry and the golden ratio.
- `gnomon_spiral.png` — a checked-in example render from
  `gnomon_spiral_plot.py`.
- `out/` — generated KML/CSV from `draw_kml_spirals.py` runs (gitignored,
  not part of the tracked repo).

## Notes

- `draw_kml_spirals.py`'s geodesic math (`EARTH_RADIUS_M`, ellipsoid
  handling) is independent of `geodetics.py` at the repo root — both exist,
  but this module doesn't import the other.
- The two `matplotlib` plotters (`gnomon_spiral_plot.py`,
  `square_spiral_plot.py`) are flat-plane diagrams for visualizing the
  golden-ratio construction itself, not map output; `draw_kml_spirals.py`
  is the one that projects a spiral onto real-world coordinates.
