# Covering Preliminaries Part I

## Optimizing Geographic Coverage

## Summary

Applying a covering problem to geographic nodes means choosing facility locations so they serve a set of targets as efficiently as possible. In practice, those facilities might be cell towers, emergency sirens, sensor stations, or drone bases, and the targets might be homes, intersections, high points, or other points of interest.

This gets harder in real terrain. A point can be close in map distance and still be unusable because of elevation differences, ridgelines, or blocked lines of sight. That is why a geographic covering workflow often combines spatial optimization with elevation data and visibility analysis.

The two core covering models are:

- `LSCP` (Location Set Covering Problem): find the fewest sites needed to cover every demand point.
- `MCLP` (Maximal Covering Location Problem): with a fixed number of sites, cover as much demand as possible.

The main practical complications are:

- Distance alone is often not enough; terrain and altitude can change whether a site really covers a target.
- Coverage calculations may need a DEM and line-of-sight or viewshed logic, not just a radius.
- Covering problems are computationally hard, so real workflows often mix exact methods with heuristics.

## Understanding Geographic Covering Problems

A geographic covering problem is a spatial decision problem: where should facilities go so service is efficient, complete, or as strong as possible within a budget?

Two classic formulations dominate the field:

- `LSCP` for full coverage requirements.
- `MCLP` for budget-limited, best-possible coverage.

### Location Set Covering Problem (LSCP)

The LSCP asks:

> What is the minimum number of facilities required so that every demand point is covered?

This is the version to use when missing even one demand point is unacceptable.

- Objective: minimize the number of chosen facility sites.
- Constraint: every demand point must be covered by at least one chosen site.
- Example: place the fewest emergency sirens so that all neighborhoods are within service range and, if needed, within a valid sound or signal path.

In plain notation:

```text
Minimize:
  sum of Y_j over all candidate sites j

Subject to:
  for every demand point i,
  sum of Y_j over all candidate sites j that can cover i must be at least 1

  each Y_j is either 0 or 1
```

Where:

- `I` is the set of demand points.
- `J` is the set of candidate facility sites.
- `Y_j = 1` means site `j` is selected.
- `N_i` is the set of candidate sites that can cover demand point `i`.

### Maximal Covering Location Problem (MCLP)

The MCLP asks:

> If only a fixed number of facilities can be built, which ones cover the most demand?

This is the version to use when budgets are fixed and full coverage may not be possible.

- Objective: maximize the total amount of covered demand.
- Constraint: only `p` sites may be selected.
- Example: choose 50 tower locations that serve the greatest total population.

In plain notation:

```text
Maximize:
  sum of w_i * y_i over all demand points i

Subject to:
  for every demand point i,
  the sum of x_j over sites j that can cover i must be at least y_i

  the sum of x_j over all candidate sites j must equal p

  each x_j and y_i is either 0 or 1
```

Where:

- `w_i` is the weight of demand point `i`, such as population or importance.
- `y_i = 1` means demand point `i` is covered.
- `x_j = 1` means site `j` is selected.
- `p` is the fixed number of facilities allowed.

---

## Why 3D Coordinates and Terrain Matter

For many real-world systems, a flat map is not enough. Altitude changes the geometry of service, especially when the service depends on visibility, radio propagation, or direct travel through space.

### Calculating Distance in 3D Space

Latitude and longitude are convenient for storage and mapping, but 3D calculations are often easier after converting coordinates into an Earth-centered Cartesian system.

One standard approach is to convert geodetic coordinates `(latitude, longitude, altitude)` into `ECEF` coordinates `(X, Y, Z)`, where `ECEF` means Earth-Centered, Earth-Fixed.

In plain notation:

```text
X = (N + h) * cos(phi) * cos(lambda)
Y = (N + h) * cos(phi) * sin(lambda)
Z = ((b^2 / a^2) * N + h) * sin(phi)
```

Where:

- `phi` is latitude.
- `lambda` is longitude.
- `h` is altitude.
- `a` and `b` are the ellipsoid axes.
- `N` is the prime-vertical radius of curvature.

Once points are in ECEF space, the straight-line 3D distance between two points is a standard Euclidean distance calculation.

### Defining Coverage with Line of Sight

In many geographic covering problems, distance is only one part of coverage. A tower may be close to a target but still fail to serve it because terrain blocks the path.

Two common terrain concepts matter here:

- `DEM` (Digital Elevation Model): a raster surface where each cell stores elevation.
- `Viewshed` or `line-of-sight` analysis: a computation that tests which locations are visible from a site, given terrain and observer height.

In terrain-aware covering, a target is covered only if it meets the chosen distance rule and also passes the required visibility rule.

---

## Formulating a 3D Geographic Covering Workflow

A practical covering workflow usually has three stages.

### Integrated Workflow

1. Prepare the inputs.
   - Gather demand points with coordinates and optional weights.
   - Gather candidate facility sites with coordinates and optional heights or costs.
   - Gather terrain data such as a DEM if visibility matters.
2. Build the coverage relationships.
   - For each candidate site, determine which demand points it can cover.
   - This may use radius only, radius plus altitude, or radius plus terrain-aware visibility.
   - The result is a coverage matrix: a table of which sites cover which demand points.
3. Solve the covering question.
   - Feed the coverage matrix, demand weights, and any budget limits into a solver.
   - Use the LSCP when full coverage is required.
   - Use the MCLP when the number of chosen sites is fixed.

### Solution Strategies and Tools

Because covering problems are NP-hard, the right solving method depends on problem size and how exact the answer must be.

- Greedy heuristics are often useful for large exploratory problems.
- Exact integer-programming solvers are useful for smaller or high-stakes cases.
- GIS tooling is often needed to produce the coverage matrix before optimization even begins.
- Open-source spatial optimization tools exist, but the hardest part is often defining realistic coverage, not writing down the objective function.

---

## Example: Clean QGIS Python Snippet

The example below is intentionally narrow. It shows one clean way to convert point elevations into ECEF coordinates and measure 3D distance between points sampled from a DEM. It does not solve a covering problem by itself, but it illustrates one building block that a terrain-aware covering workflow may need.

```python
import math
from qgis.core import QgsPointXY, QgsProject


def geodetic_to_ecef(lat, lon, alt):
    """Convert WGS84 geodetic coordinates to ECEF."""
    a = 6378137.0
    f = 1 / 298.257223563
    e2 = 2 * f - f**2

    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    n = a / math.sqrt(1 - e2 * math.sin(lat_rad) ** 2)

    x = (n + alt) * math.cos(lat_rad) * math.cos(lon_rad)
    y = (n + alt) * math.cos(lat_rad) * math.sin(lon_rad)
    z = (n * (1 - e2) + alt) * math.sin(lat_rad)
    return x, y, z


def sample_dem_elevation(layer, x, y):
    """Sample DEM elevation at a point."""
    value, ok = layer.dataProvider().sample(QgsPointXY(x, y), 1)
    return value if ok else 0.0


ref_layer = QgsProject.instance().mapLayersByName("refpnts")[0]
probe_layer = QgsProject.instance().mapLayersByName("prbpnts")[0]
dem_layer = QgsProject.instance().mapLayersByName("your_2m_dem")[0]

for ref in ref_layer.getFeatures():
    ref_point = ref.geometry().asPoint()
    ref_alt = sample_dem_elevation(dem_layer, ref_point.x(), ref_point.y())
    ref_ecef = geodetic_to_ecef(ref_point.y(), ref_point.x(), ref_alt)

    for probe in probe_layer.getFeatures():
        probe_point = probe.geometry().asPoint()
        probe_alt = sample_dem_elevation(dem_layer, probe_point.x(), probe_point.y())
        probe_ecef = geodetic_to_ecef(probe_point.y(), probe_point.x(), probe_alt)

        dist_3d = math.sqrt(
            sum((a - b) ** 2 for a, b in zip(ref_ecef, probe_ecef))
        )
        print(f"Ref {ref.id()} to Probe {probe.id()}: {dist_3d:.3f} m")
```

## Key Technical Components

- `ECEF`: a stable Cartesian frame for 3D point-to-point distance calculations.
- `3D distance`: the straight-line distance between two points in space, after coordinate conversion.
- `Subtending angle`: the angle between two lines of sight meeting at one observation point.
- `Elevation sampling`: extracting terrain height from a DEM at specific coordinates.
- `Local bearing or azimuth`: if needed, this can be derived from a local tangent-plane frame rather than from raw ECEF values alone.

---

## Vocabulary for the CLI That Comes Next

To keep the future CLI and web UI coherent, the core terms should stay stable:

- `demand point`: a location that needs to be served or evaluated.
- `candidate site`: a location that could be chosen as a facility.
- `coverage`: the result of applying a rule that decides whether one site serves one demand point.
- `coverage rule`: the rule used to decide coverage, such as radius-only, radius-plus-altitude, or terrain-aware visibility.
- `coverage matrix`: a table or sparse relation showing which candidate sites cover which demand points.
- `solver`: the algorithm that chooses sites once the coverage matrix exists.
- `full-cover query`: a query in the LSCP style, asking for the fewest sites needed to cover all demand points.
- `budgeted max-cover query`: a query in the MCLP style, asking which fixed number of sites covers the most demand.
- `terrain-aware visibility`: coverage logic that uses a DEM, viewshed, or line-of-sight test rather than distance alone.

These terms give the project a clean handoff from theory to implementation. They also map naturally onto both CLI commands and later UI labels, filters, and result views.

---

## Learn More

- [PySAL `spopt` LSCP notebook](https://pysal.org/spopt/notebooks/lscp.html): Python examples for location set covering.
- [Maximal Covering Location Problem overview](https://arxiv.org/html/2509.23334): a recent discussion of maximal covering and its applications.
- [GIS-based covering error analysis](https://www.sciencedirect.com/science/article/abs/pii/S0377221712005681): why real-world spatial assumptions matter.
- [Geodetic coordinate conversions to ECEF](http://www.ceri.memphis.edu/people/rsmalley/ESCI7355/coordcvt.pdf): background on geodetic-to-Cartesian conversion.
- [USGS explanation of DEMs](https://www.usgs.gov/faqs/what-a-digital-elevation-model-dem): baseline description of digital elevation models.
- [ArcGIS line-of-sight overview](https://pro.arcgis.com/en/pro-app/latest/tool-reference/3d-analyst/how-line-of-sight-works.htm): summary of terrain-aware visibility logic.
- [ArcGIS network analyst algorithms](https://desktop.arcgis.com/en/arcmap/latest/extensions/network-analyst/algorithms-used-by-network-analyst.htm): broader algorithm background relevant to location-allocation work.
- [Heuristic GIS search methods](https://methods.sagepub.com/book/mono/download/gis-algorithms-srm/chpt/12-heuristic-search-algorithms.pdf): background on practical search strategies.
- [Set cover algorithms for large datasets](https://dimacs.rutgers.edu/~graham/pubs/papers/ckw.pdf): useful context for greedy and scalable approaches.
