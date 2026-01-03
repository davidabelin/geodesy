# Roadways

Canonical entrypoint: `python -m roadways ...`

## Dependencies

- `export-kml`: `simplekml`
- `dc-roadways`: `geopandas`, `shapely`, `pyproj` (and optionally `matplotlib` for the `dc-street-v1` color scheme)

## Commands

- `export-kml`
  - Converts a LineString/MultiLineString GeoJSON to KML, using a per-feature color field.
  - Args:
    - `--input PATH` (required)
    - `--output PATH` (required)
    - `--name-field FIELD` (default: `route`, fallback: `ROUTENAME`)
    - `--group-by FIELD` (optional; creates KML folders)
    - `--color-field FIELD` (default: `color`)
    - `--default-color AABBGGRR` (default: `ff0000ff`)
    - `--line-width FLOAT` (default: `2.0`)
    - `--doc-name NAME` (optional)

- `dc-roadways`
  - DC pipeline: reads `Roadway_SubBlock.geojson`, merges by route name, classifies EW/NS, styles, exports KML + distance CSVs.
  - Args:
    - `--input PATH` (default: `roadways/data/Roadway_SubBlock.geojson`)
    - `--output-kml PATH` (default: `roadways/out/classified_roadways.kml`)
    - `--ew-csv PATH` (default: `roadways/out/ew_street_distances.csv`)
    - `--ns-csv PATH` (default: `roadways/out/ns_street_distances.csv`)
    - `--road-type VALUE` (default: `ST`)
    - `--name-field FIELD` (default: `ROUTENAME`)
    - `--type-field FIELD` (default: `STREETTYPE`)
    - `--style {dc-street-v1,none}` (default: `dc-street-v1`)
    - `--keep-other` (include unclassified roads)

## Examples

- DC roadways end-to-end (defaults):
  - `python -m roadways dc-roadways`

- Convert the existing colored avenues GeoJSON:
  - `python -m roadways export-kml --input roadways/data/dc_avenues.geojson --output roadways/out/dc_avenues.kml --name-field route --group-by route`

## Legacy

The older ad-hoc scripts are kept under `roadways/legacy/` (not the preferred entrypoints).
