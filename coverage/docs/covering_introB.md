# Covering Preliminaries Part II

## A Concrete QGIS Question

This is the practical follow-on to Part I.

Suppose you have:

- a small set of reference points, such as `refpnts`
- a larger set of probe points, such as `prbpnts`
- latitude and longitude for each point
- a 2 m DEM that can supply terrain elevation

And suppose you want to know:

- the straight-line 3D distances between reference points and probe points
- the azimuth or local bearing of one point from another
- the subtending angle between two lines of sight meeting at a point of interest

That is a very reasonable QGIS-side problem, but it helps to separate three different ideas that are easy to blur together:

- `Geodesic distance`: distance measured along the ellipsoid.
- `3D point-to-point distance`: straight-line distance through space after altitude is included.
- `Terrain-obstructed line of sight`: whether terrain blocks one point from another.

A script can compute the second item directly. The third item needs an additional terrain-intersection or viewshed-style test.

## Short Answer

If you want high-precision 3D geometry between known points, convert each point from geodetic coordinates into `ECEF` coordinates and do the vector math there.

That gives you:

- stable 3D distances
- clean vector operations for angles
- a good basis for deriving local directional measurements

It does not automatically prove that the line between the two points is terrain-clear. For that, you still need to test the segment against the DEM.

---

## Practical QGIS Workflow

1. Load the point layers.
   - A reference layer such as `refpnts`
   - A probe layer such as `prbpnts`
2. Load the DEM.
   - Sample elevation at each point location.
3. Convert each point from `(lat, lon, alt)` to `ECEF (X, Y, Z)`.
4. Compute the measurements you care about.
   - 3D point-to-point distance
   - vector-based subtending angle
   - optional local azimuth or bearing
5. If required, add a separate terrain-clearance test.
   - This is what upgrades point-to-point geometry into true terrain-aware line-of-sight analysis.

## Clean QGIS Python Example

The script below focuses on the reusable geometry core:

- sample elevation from a DEM
- convert points to ECEF
- compute 3D distances
- compute an angle at one point from two other points

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


def distance_3d(a, b):
    """Euclidean distance between two ECEF points."""
    return math.sqrt(sum((u - v) ** 2 for u, v in zip(a, b)))


def subtending_angle(origin, point_a, point_b):
    """Angle at origin between origin->point_a and origin->point_b."""
    oa = tuple(a - o for a, o in zip(point_a, origin))
    ob = tuple(b - o for b, o in zip(point_b, origin))

    dot = sum(u * v for u, v in zip(oa, ob))
    mag_oa = math.sqrt(sum(v * v for v in oa))
    mag_ob = math.sqrt(sum(v * v for v in ob))

    if mag_oa == 0 or mag_ob == 0:
        raise ValueError("Subtending angle is undefined for zero-length vectors.")

    cos_theta = dot / (mag_oa * mag_ob)
    cos_theta = max(-1.0, min(1.0, cos_theta))
    return math.degrees(math.acos(cos_theta))


ref_layer = QgsProject.instance().mapLayersByName("refpnts")[0]
probe_layer = QgsProject.instance().mapLayersByName("prbpnts")[0]
dem_layer = QgsProject.instance().mapLayersByName("your_2m_dem")[0]

probe_cache = []
for probe in probe_layer.getFeatures():
    probe_point = probe.geometry().asPoint()
    probe_alt = sample_dem_elevation(dem_layer, probe_point.x(), probe_point.y())
    probe_ecef = geodetic_to_ecef(probe_point.y(), probe_point.x(), probe_alt)
    probe_cache.append((probe.id(), probe_point, probe_ecef))

for ref in ref_layer.getFeatures():
    ref_point = ref.geometry().asPoint()
    ref_alt = sample_dem_elevation(dem_layer, ref_point.x(), ref_point.y())
    ref_ecef = geodetic_to_ecef(ref_point.y(), ref_point.x(), ref_alt)

    for probe_id, probe_point, probe_ecef in probe_cache:
        dist = distance_3d(ref_ecef, probe_ecef)
        print(f"Ref {ref.id()} to Probe {probe_id}: {dist:.3f} m")


# Example angle at one reference point using the first two probes.
ref_features = list(ref_layer.getFeatures())
if len(ref_features) >= 1 and len(probe_cache) >= 2:
    probe_a = probe_cache[0][2]
    probe_b = probe_cache[1][2]
    first_ref = ref_features[0]
    first_ref_point = first_ref.geometry().asPoint()
    first_ref_alt = sample_dem_elevation(
        dem_layer, first_ref_point.x(), first_ref_point.y()
    )
    first_ref_ecef = geodetic_to_ecef(
        first_ref_point.y(), first_ref_point.x(), first_ref_alt
    )

    angle = subtending_angle(first_ref_ecef, probe_a, probe_b)
    print(f"Angle at Ref {first_ref.id()}: {angle:.6f} deg")
```

## What This Example Does and Does Not Do

It does:

- use DEM-sampled altitude
- compute straight-line 3D distances
- compute angles between sight lines in 3D space
- give you reusable geometry for later CLI or UI work

It does not:

- test whether terrain blocks the line between points
- compute a full raster viewshed
- solve a covering optimization problem

That distinction matters. A 3D distance calculation can say how far apart two elevated points are in space, but it cannot by itself say whether a ridge lies between them.

---

## Subtending Angles

The subtending angle at an observation point is the angle between two vectors drawn from that observation point to two other points.

In plain notation:

```text
theta = arccos( dot(OA, OB) / (|OA| * |OB|) )
```

Where:

- `O` is the point of interest
- `A` and `B` are the two target points
- `OA` and `OB` are vectors from `O`

## Azimuth Notes

If you need an ordinary geodetic azimuth, compute it in a local tangent frame or with geodesic formulas on the ellipsoid.

If you need a 3D directional measurement tied to the local horizon, project the ECEF vector into a local east-north-up frame first. Raw ECEF coordinates alone are not yet a local azimuth.

---

## Why This Matters for the Covering Work

This Part II material is not a covering solver yet. It is one of the geometry layers that a terrain-aware covering workflow may need.

It helps define:

- how a candidate site and a demand point are compared in 3D
- how a future coverage rule might use a DEM-backed visibility test
- how later CLI commands could report pairwise geometry before building a coverage matrix

In other words:

- Part I explains the covering models.
- Part II explains one concrete geometric subproblem behind those models.
