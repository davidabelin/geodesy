# Geodesy

This workspace contains multiple CLI tools (run with `python -m ...`) focused on KML/GeoJSON workflows and geodetic analysis.

## CLI Map

```
python -m roadways
  export-kml
    --input PATH                      (required)
    --output PATH                     (required; relative goes under roadways/out/)
    --name-field FIELD                (default: route)
    --group-by FIELD                  (optional)
    --color-field FIELD               (default: color)
    --default-color AABBGGRR          (default: ff0000ff)
    --line-width FLOAT                (default: 1.0)
    --doc-name NAME                   (optional)

  dc-centerlines
    --input PATH                      (default: roadways/data/centerlines.gpkg if present, else SHP)
    --layer NAME                      (optional; for GPKG, default: centerlines when applicable)
    --output-kml PATH                 (default: classified_centerlines.kml; relative under roadways/out/)
    --ew-csv PATH                     (default: centerlines_ew_street_distances.csv; relative under roadways/out/)
    --ns-csv PATH                     (default: centerlines_ns_street_distances.csv; relative under roadways/out/)
    --roadtype VALUE                  (default: Street; empty disables)
    --streettype VALUE                (optional; e.g. AVE; accepts Ave/Avenue)
    --style SPEC                      (default: dc-street-v1; dc-street-v1 | none | cmap:<name> | solid:<hex>)
    --keep-other                      (flag)
    --line-width FLOAT                (default: 1.0)
    --snap-gap-m FLOAT                (default: 0.0)
    --snap-gap-frac FLOAT             (default: 0.0)

  dc-roadways   (legacy SubBlock GeoJSON pipeline)
    --input PATH                      (default: roadways/data/Roadway_SubBlock.geojson)
    --output-kml PATH                 (default: classified_roadways.kml; relative under roadways/out/)
    --ew-csv PATH                     (default: ew_street_distances.csv; relative under roadways/out/)
    --ns-csv PATH                     (default: ns_street_distances.csv; relative under roadways/out/)
    --road-type VALUE                 (default: ST)
    --name-field FIELD                (default: ROUTENAME)
    --type-field FIELD                (default: STREETTYPE)
    --style SPEC                      (default: dc-street-v1; dc-street-v1 | none | cmap:<name> | solid:<hex>)
    --keep-other                      (flag)
    --line-width FLOAT                (default: 1.0)
    --snap-gap-m FLOAT                (default: 0.0)
    --snap-gap-frac FLOAT             (default: 0.0)

  intersections
    --a-input PATH                    (required)
    --b-input PATH                    (optional; defaults to A)
    --a-layer NAME                    (optional; for GPKG)
    --b-layer NAME                    (optional; for GPKG)
    --a-field FIELD --a-value VALUE   (optional filter)
    --b-field FIELD --b-value VALUE   (optional filter)
    --a-name-field FIELD              (optional label)
    --b-name-field FIELD              (optional label)
    --derive-full-name                (flag; builds FULL_NAME from ST_NAME+QUADRANT when possible)
    --full-name-field FIELD           (default: FULL_NAME)
    --group-by FIELD                  (optional dissolve before intersecting)
    --output-geojson PATH             (default: intersections.geojson; relative under roadways/out/)
    --output-csv PATH                 (optional; relative under roadways/out/)
    --output-kml PATH                 (optional; relative under roadways/out/)
    --kml-color AABBGGRR              (default: ff00ffff)
    --dedupe-grid-m FLOAT             (default: 0.5)

  datasets
    centerlines-gpkg
      --input PATH                    (default: roadways/data/DC_Street_Centerlines/Street_Centerlines_2013.shp)
      --output PATH                   (default: roadways/data/centerlines.gpkg)
      --layer NAME                    (default: centerlines)
      --overwrite                     (flag)

python -m highpoints
  hidrant-peaks
    --dem PATH                        (required)
    --buffer-m FLOAT                  (default: 1000.0)
    --separation-m FLOAT              (default: 100.0)
    --output-kml PATH                 (default: hidrant_peaks.kml; relative under highpoints/out/)
    --output-csv PATH                 (optional; relative under highpoints/out/)

python -m spirals
  kml
    --csv PATH                        (required; must include LOC, LAT, LON columns)
    --output PATH                     (optional; default: spirals/out/<csv-stem>.kml; relative under spirals/out/)
    --include-points                  (flag)
```

## Notes

- There are additional standalone scripts in the repo (e.g. `geodetics.py`, `point_jiggler.py`) that have their own CLIs, but they are not yet unified under a single top-level `python -m geodesy` entrypoint.

