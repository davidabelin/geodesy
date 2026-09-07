# border

Reference data and QGIS projects for the original boundary markers of the
District of Columbia: the 40 sandstone milestones placed in 1791-92 along
the four ~10-mile sides of Ellicott's surveyed diamond, plus the district's
official modern boundary geometry.

## Naming convention

Stones are labeled by side and mile number, e.g. `SW1`...`SW9`, `NW1`...`NW9`,
`NE1`...`NE9`, `SE1`...`SE9` (one stone per mile along each side, corner
stones shared between sides). `diagonal_*` files trace the four ~10-mile
diagonal sides themselves (`SW1 -> NE9`, etc. — the outer boundary as
originally surveyed); `horivert_*` files trace point-to-point ties across
the diamond on true north-south/east-west lines instead of along the
diagonal sides.

## Contents

- `Boundary Lines.qgs` *(tracked)* — main QGIS project for the boundary
  stones and lines.
- `Boundary Lines_attachments.zip` *(tracked)* — QGIS style/attachment
  archive that travels with the project above.
- `MyBoundaryPnts.xml` *(tracked)* — a KML-schema point set (`loc`, `lat`,
  `lon`) for the boundary stones, styled with per-point icons.
- `StonePnts.csv` — flat `LOC,LAT,LON` table of stone coordinates; the
  canonical input for altitude sampling (see `highpoints/get_alts.py`) and
  for KML/spiral generation elsewhere in the repo.
- `DC_Boundary_Stones_official.json` / `official_boundaries.geojson` — DC
  government open-data exports (DCGIS `BoundaryStonesPt`) with surveyed
  elevations (`Z`, `Z_FT`), state-plane coordinates, narrative location
  descriptions, and photo links for each stone.
- `stone_alts.geojson` — DEM-sampled elevations for the stone points,
  generated locally (compare against the official `Z`/`Z_FT` fields above).
- `diagonal_stones.csv` / `diagonal_stones.kml` — stone-to-stone pairs
  along each diagonal side, and the rendered KML.
- `diagonal_mile_pnts.csv` / `.kmz` — interpolated points along each
  diagonal side at intervals between stones, with azimuth to the next
  target stone.
- `horivert_stones.csv` / `.kml` / `.kmz` — cross-diamond stone pairings on
  cardinal (N-S / E-W) lines rather than along the diagonal sides.
- `Border Lines.kmz`, `Stones Styled.kmz` — styled KML/KMZ exports for
  viewing outside QGIS (e.g. Google Earth).

## Notes

- Only the three files marked *(tracked)* above are committed to git;
  everything else here is derived/generated data excluded by the repo's
  `.gitignore` (`*.csv`, `*.kml`, `*.kmz`, `*.geojson`, `*.json`) and is
  expected to be regenerated locally rather than versioned.
- `diagonal_stones.csv` and `horivert_stones.csv` use the `sailpath.py`
  CSV contract (`LOC0,LAT0,LON0,LOC1,LAT1,LON1,STEP_DIST`), so they can be
  fed straight back into `geo.bat sailpath` / `sailpath.py`.
- Coordinates in the hand-built CSVs are WGS84 lat/lon; the official DCGIS
  JSON/GeoJSON ships both lat/lon and Maryland State Plane (`XCOORD`,
  `YCOORD`) values.
