# Roadways

Canonical entrypoint: `python -m roadways ...`

## Dependencies

- `export-kml`: `simplekml`
- `dc-centerlines`, `dc-roadways`: `geopandas`, `shapely`, `pyproj` (and optionally `matplotlib` for the `dc-street-v1` color scheme)

## Commands

- `intersections`
  - Computes point intersections between two vector layers (or two filtered views of one layer) and can emit GeoJSON/CSV/KML.
  - Typical use: intersections between `STREETTYPE` groups, or between “rays” (GeoJSON) and centerlines (SHP).
  - Args (high-level):
    - `--a-input PATH` (required), `--b-input PATH` (optional; defaults to A)
    - `--a-field FIELD --a-value VALUE` (optional), `--b-field FIELD --b-value VALUE` (optional)
    - `--group-by FIELD` (optional dissolve before intersecting; recommended to reduce duplicates)
    - `--output-geojson PATH` (default: `intersections.geojson`, goes under `roadways/out/`)
    - `--output-csv PATH` (optional), `--output-kml PATH` (optional)

- `dc-centerlines`
  - Preferred pipeline for separation work: uses `Street_Centerlines_2013.shp` (centerline geometry).
  - Args:
    - `--input PATH` (default: `roadways/data/DC_Street_Centerlines/Street_Centerlines_2013.shp`)
    - `--output-kml PATH` (default: `roadways/out/classified_centerlines.kml`)
    - `--ew-csv PATH` (default: `roadways/out/centerlines_ew_street_distances.csv`)
    - `--ns-csv PATH` (default: `roadways/out/centerlines_ns_street_distances.csv`)
    - `--roadtype VALUE` (default: `Street`; set to empty string to disable filtering)
    - `--streettype VALUE` (example: `AVE`; empty disables)
    - `--style dc-street-v1|none|cmap:<name>|solid:<hex>` (default: `dc-street-v1`)
    - `--keep-other`
    - `--line-width FLOAT` (default: `1.0`)
    - `--snap-gap-m FLOAT` (default: `0.0`)
    - `--snap-gap-frac FLOAT` (default: `0.0`)

- `export-kml`
  - Converts a LineString/MultiLineString GeoJSON to KML, using a per-feature color field.
  - Args:
    - `--input PATH` (required)
    - `--output PATH` (required)
    - `--name-field FIELD` (default: `route`, fallback: `ROUTENAME`)
    - `--group-by FIELD` (optional; creates KML folders)
    - `--color-field FIELD` (default: `color`)
    - `--default-color AABBGGRR` (default: `ff0000ff`)
    - `--line-width FLOAT` (default: `1.0`)
    - `--doc-name NAME` (optional)

- `dc-roadways`
  - SubBlock GeoJSON pipeline: reads `Roadway_SubBlock.geojson`, merges by route name, classifies EW/NS, styles, exports KML + distance CSVs.
  - Args:
    - `--input PATH` (default: `roadways/data/Roadway_SubBlock.geojson`)
    - `--output-kml PATH` (default: `roadways/out/classified_roadways.kml`)
    - `--ew-csv PATH` (default: `roadways/out/ew_street_distances.csv`)
    - `--ns-csv PATH` (default: `roadways/out/ns_street_distances.csv`)
    - `--road-type VALUE` (default: `ST`)
    - `--name-field FIELD` (default: `ROUTENAME`)
    - `--type-field FIELD` (default: `STREETTYPE`)
    - `--style dc-street-v1|none|cmap:<name>|solid:<hex>` (default: `dc-street-v1`)
    - `--keep-other` (include unclassified roads)
    - `--line-width FLOAT` (default: `1.0`)
    - `--snap-gap-m FLOAT` (default: `0.0`)
    - `--snap-gap-frac FLOAT` (default: `0.0`)

## Examples

- Streets vs avenues intersections (merged per named road):
  - `python -m roadways intersections --a-input roadways/data/DC_Street_Centerlines/Street_Centerlines_2013.shp --b-input roadways/data/DC_Street_Centerlines/Street_Centerlines_2013.shp --a-field STREETTYPE --a-value ST --b-field STREETTYPE --b-value AVE --derive-full-name --group-by FULL_NAME --output-geojson st_x_ave.geojson --output-csv st_x_ave.csv --output-kml st_x_ave.kml`

- DC centerlines end-to-end (defaults):
  - `python -m roadways dc-centerlines`

- DC roadways end-to-end (defaults):
  - `python -m roadways dc-roadways`

- Convert the existing colored avenues GeoJSON:
  - `python -m roadways export-kml --input roadways/data/dc_avenues.geojson --output dc_avenues.kml --name-field route --group-by route`

## Legacy

The older ad-hoc scripts are kept under `roadways/legacy/` (not the preferred entrypoints).
