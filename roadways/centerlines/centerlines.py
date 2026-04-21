import argparse
import colorsys
import csv
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from pyproj import CRS, Geod

try:
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import LineString, MultiLineString, Point
    from shapely.ops import nearest_points
except ImportError:
    gpd = None
    pd = None
    LineString = None
    MultiLineString = None
    Point = None
    nearest_points = None

EARTH_RADIUS_MILES = 3958
EARTH_RADIUS_FEET = EARTH_RADIUS_MILES * 5280
MILES_PER_FOOT = 1 / 5280.0
MILES_PER_YARD = 3 / 5280.0
MILES_PER_POLE = 16.5 / 5280.0  # 1 rod = 16.5 ft
METERS_PER_MILE = 1609.344
MILES_PER_METER = 1 / METERS_PER_MILE
KM_PER_MILE = 1.609344
MILES_PER_KM = 1 / KM_PER_MILE
DEG2RAD = math.pi / 180.0
RAD2DEG = 180.0 / math.pi
FIp = (math.sqrt(5)+1)/2
FIm = (math.sqrt(5)-1)/2
DEFAULT_ELLIPSOID = "WGS84"
ELLIPSOID_SPHERE = "sphere"
CL_DEFAULT_INPUT = Path("roadways") / "data" / "DC_Street_Centerlines" / "Street_Centerlines_2013.shp"
CL_DEFAULT_OUTPUT = Path("roadways") / "centerlines" / "cl_out.csv"
CL_DEFAULT_GIS_OUTPUT = Path("roadways") / "centerlines" / "cl.gpkg"
CL_NAME_FIELDS = ("ST_NAME", "FULLNAME", "NAME", "ROUTENAME")
CL_ID_FIELDS = ("STREETSEGID", "STREETSEGI", "OBJECTID", "FID")
CL_LENGTH_FIELDS = ("SHAPE.LEN", "SHAPELEN", "LENGTH", "SHAPE_LENGTH")
CL_ROADTYPE_FIELDS = ("ROADTYPE",)
CL_TYPE_FIELDS = ("STREETTYPE", "USPS_ABBRE", "ST_TYPE", "TYPE")
CL_QUADRANT_FIELDS = ("QUADRANT", "QUAD", "QUADRANT_NM")
CL_CARDINAL_TOLERANCE_DEG = 5.0
CL_GEOMETRY_CONNECT_TOLERANCE = 1e-9
CL_OUTPUT_FIELDS = [
    'street',
    'str_id',
    'street_class',
    'length',
    'bearing',
    'street_1',
    'str_id1',
    'dir_1',
    'dist_1',
    'street_2',
    'str_id2',
    'dir_2',
    'dist_2',
]
CL_SUMMARY_FIELDS = [
    'street',
    'street_class',
    'segment_count',
    'str_id_min',
    'str_id_max',
    'length_sum',
    'length_mean',
    'length_min',
    'length_max',
    'bearing_mean',
    'bearing_min',
    'bearing_max',
    'dist_1_count',
    'dist_1_mean',
    'dist_1_min',
    'dist_1_max',
    'dist_2_count',
    'dist_2_mean',
    'dist_2_min',
    'dist_2_max',
    'unit',
]
CL_GPKG_LAYER = 'centerline_bundles'
CL_GPKG_SEGMENT_LAYERS = {'EW': 'ew_segments', 'NS': 'ns_segments'}
CL_GPKG_MIDPOINT_LAYERS = {'EW': 'ew_midpoints', 'NS': 'ns_midpoints'}
CL_GPKG_EXTENSION_LAYERS = {'EW': 'ew_extensions', 'NS': 'ns_extensions'}

def _normalize_unit(unit: Optional[str]) -> str:
    if not unit:
        return "miles"
    return unit.strip().lower()

def _to_miles(distance: float, unit: Optional[str] = "miles") -> float:
    u = _normalize_unit(unit)
    if u in ("mile", "miles", "mi"):
        return distance
    if u in ("foot", "feet", "ft"):
        return distance * MILES_PER_FOOT
    if u in ("yard", "yards", "yd"):
        return distance * MILES_PER_YARD
    if u in ("pole", "poles", "rod", "rods"):
        return distance * MILES_PER_POLE
    if u in ("meter", "meters", "m"):
        return distance * MILES_PER_METER
    if u in ("kilometer", "kilometers", "km"):
        return distance * MILES_PER_KM
    raise ValueError(f"Unknown distance unit: {unit}")

def _from_miles(distance_miles: float, unit: Optional[str] = "miles") -> float:
    u = _normalize_unit(unit)
    if u in ("mile", "miles", "mi"):
        return distance_miles
    if u in ("foot", "feet", "ft"):
        return distance_miles / MILES_PER_FOOT
    if u in ("yard", "yards", "yd"):
        return distance_miles / MILES_PER_YARD
    if u in ("pole", "poles", "rod", "rods"):
        return distance_miles / MILES_PER_POLE
    if u in ("meter", "meters", "m"):
        return distance_miles / MILES_PER_METER
    if u in ("kilometer", "kilometers", "km"):
        return distance_miles / MILES_PER_KM
    raise ValueError(f"Unknown distance unit: {unit}")

def _normalize_ellipsoid(ellipsoid: Union[str, bool, None]) -> str:
    if isinstance(ellipsoid, bool):
        return DEFAULT_ELLIPSOID if ellipsoid else ELLIPSOID_SPHERE
    if ellipsoid is None:
        return DEFAULT_ELLIPSOID
    if isinstance(ellipsoid, str):
        key = ellipsoid.strip().replace("-", "").replace("_", "").upper()
        if key in ("WGS84", "NAD83"):
            return key
        if key in ("SPHERE", "SPHERICAL"):
            return ELLIPSOID_SPHERE
    raise ValueError(f"Unsupported ellipsoid: {ellipsoid} (use WGS84, NAD83, or sphere)")

@lru_cache(maxsize=None)
def _get_geod(ellipsoid: str) -> Geod:
    return Geod(ellps=ellipsoid)

def haversine(phi1, lam1, phi2, lam2, unit='miles'):
    """
    phi, lam = lat, lon in degrees
    Great-circle distance between two lat/lon (decimal degrees) via haversine.
    Returns distance in miles or feet.
    """
    dphi = (phi2 - phi1) * DEG2RAD
    dlam = (lam2 - lam1) * DEG2RAD
    a = math.sin(dphi/2)**2 + math.cos(phi1*DEG2RAD)*math.cos(phi2*DEG2RAD)*math.sin(dlam/2)**2
    c = 2 * math.asin(min(1, math.sqrt(a)))
    dist_miles = EARTH_RADIUS_MILES * c
    return _from_miles(dist_miles, unit)


def initial_bearing(phi1: float, lam1: float, phi2: float, lam2: float) -> float:
    """
    phi, lam = lat, lon in degrees
    Returns initial bearing from point1 to point2 on a sphere,
    in degrees clockwise from north.
    """
    phi1r, phi2r = phi1*DEG2RAD, phi2*DEG2RAD
    dl = (lam2 - lam1)*DEG2RAD
    x = math.sin(dl)*math.cos(phi2r)
    y = math.cos(phi1r)*math.sin(phi2r) - math.sin(phi1r)*math.cos(phi2r)*math.cos(dl)
    return (math.atan2(x, y)*RAD2DEG + 360) % 360


def final_bearing(phi1: float, lam1: float, phi2: float, lam2: float) -> float:
    """
    phi, lam = lat, lon in degrees
    Returns final bearing arriving at point2 from point1, in degrees.
    """
    return (initial_bearing(phi2, lam2, phi1, lam1) + 180) % 360

#===============================================================================
# Spherical Trigonometric Identities
#===============================================================================

def normalize_longitude(lon_deg):
    """Normalize longitude to the range [-180, 180)."""
    return (lon_deg + 180) % 360 - 180

def cos_supplement(x):
    """Cosine of (pi - x) = -cos(x)."""
    return -math.cos(x)

def sin_supplement(x):
    """Sine of (pi - x) = sin(x)."""
    return math.sin(x)

def cot_a(a, b, c):
    """Cotangent identity: cot(a) = [cos(b)cos(c) - cos(a)] / [sin(b)sin(c)]."""
    return (math.cos(b)*math.cos(c) - math.cos(a)) / (math.sin(b)*math.sin(c))

def tan_half_angle(a, b, c):
    """tan(E/4) formula for sides a,b,c: L'Huilier's half-excess form."""
    s = 0.5*(a + b + c)
    t = math.tan(s/2)*math.tan((s-a)/2)*math.tan((s-b)/2)*math.tan((s-c)/2)
    return math.sqrt(abs(t))

def delambre(a, b, c, A, B, C):
    """
    Returns Delambre analogies:
      tan((A+B)/2) = [cos((a-b)/2)/cos((a+b)/2)] * tan(C/2)
      tan((a+b)/2) = [cos((A-B)/2)/cos((A+B)/2)] * tan(c/2)
    """
    return {
        'tan_ab2': math.tan((A+B)/2),
        'rhs1': (math.cos((a-b)/2)/math.cos((a+b)/2))*math.tan(C/2),
        'tan_ab_half': math.tan((a+b)/2),
        'rhs2': (math.cos((A-B)/2)/math.cos((A+B)/2))*math.tan(c/2)
    }

#===============================================================================
# Geodetic Problems (Sphere & Ellipsoid)
#===============================================================================

def direct_geodetic(phi1: float, lam1: float, az1: float, dist: float,
                    unit: str='miles', ellipsoid: Union[str, bool, None]=DEFAULT_ELLIPSOID) -> Tuple[float, float, float]:
    """
    Forward geodetic: given lat1, lon1, azimuth, distance -> lat2, lon2, back azimuth.
    Ellipsoid can be WGS84 (default), NAD83, or sphere.
    """
    ellps = _normalize_ellipsoid(ellipsoid)
    dist_miles = _to_miles(dist, unit)
    if ellps != ELLIPSOID_SPHERE:
        geod = _get_geod(ellps)
        dist_m = dist_miles * METERS_PER_MILE
        lon2, lat2, az2 = geod.fwd(lam1, phi1, az1, dist_m)
        return lat2, lon2, az2
    # spherical fallback
    sigma = dist_miles / EARTH_RADIUS_MILES
    phi1r, lam1r, az1r = phi1*DEG2RAD, lam1*DEG2RAD, az1*DEG2RAD
    phi2 = math.asin(math.sin(phi1r)*math.cos(sigma) +
                     math.cos(phi1r)*math.sin(sigma)*math.cos(az1r))
    lam2 = lam1r + math.atan2(math.sin(az1r)*math.sin(sigma)*math.cos(phi1r),
                               math.cos(sigma) - math.sin(phi1r)*math.sin(phi2))
    az2 = math.atan2(math.sin(az1r)*math.cos(phi1r)*math.cos(sigma) -
                     math.sin(phi1r)*math.sin(sigma),
                     math.cos(az1r)*math.cos(sigma))
    return phi2*RAD2DEG, lam2*RAD2DEG, az2*RAD2DEG

def inverse_geodetic(phi1: float, lam1: float, phi2: float, lam2: float,
                      unit: str='miles', ellipsoid: Union[str, bool, None]=DEFAULT_ELLIPSOID) -> Tuple[float, float, float]:
    """
    Inverse geodetic: given two lat/lon points -> az1, az2, distance.
    Ellipsoid can be WGS84 (default), NAD83, or sphere.
    """
    ellps = _normalize_ellipsoid(ellipsoid)
    if ellps != ELLIPSOID_SPHERE:
        geod = _get_geod(ellps)
        az1, az2, dist_m = geod.inv(lam1, phi1, lam2, phi2)
        dist_miles = dist_m * MILES_PER_METER
        return az1 % 360, az2 % 360, _from_miles(dist_miles, unit)
    # spherical fallback
    phi1r, phi2r = phi1*DEG2RAD, phi2*DEG2RAD
    dlam = (lam2 - lam1)*DEG2RAD
    cos_sigma = math.sin(phi1r)*math.sin(phi2r) + math.cos(phi1r)*math.cos(phi2r)*math.cos(dlam)
    sigma = math.acos(max(-1, min(1, cos_sigma)))
    az1 = math.atan2(math.sin(dlam)*math.cos(phi2r),
                      math.cos(phi1r)*math.sin(phi2r) - math.sin(phi1r)*math.cos(phi2r)*math.cos(dlam))
    az2 = math.atan2(math.sin(-dlam)*math.cos(phi1r),
                      math.cos(phi2r)*math.sin(phi1r) - math.sin(phi2r)*math.cos(phi1r)*math.cos(dlam))
    dist_miles = EARTH_RADIUS_MILES * sigma
    return az1*RAD2DEG % 360, az2*RAD2DEG % 360, _from_miles(dist_miles, unit)

@dataclass(frozen=True)
class ClMeasureSpec:
    mode: str
    crs: Optional[CRS]
    geod: Optional[Geod]
    coord_unit_to_meters: float = 1.0


def _require_cl_dependencies() -> None:
    missing = []
    if gpd is None:
        missing.append('geopandas')
    if pd is None:
        missing.append('pandas')
    if LineString is None or MultiLineString is None or Point is None or nearest_points is None:
        missing.append('shapely')
    if missing:
        raise RuntimeError(
            "The cl command requires: "
            + ", ".join(sorted(set(missing)))
        )


def _normalize_field_name(name: str) -> str:
    return ''.join(ch for ch in str(name).upper() if ch.isalnum())


def _find_preferred_field(columns: Iterable[str], preferred: Iterable[str]) -> Optional[str]:
    actual_by_key = {_normalize_field_name(column): column for column in columns}
    for candidate in preferred:
        resolved = actual_by_key.get(_normalize_field_name(candidate))
        if resolved:
            return resolved
    return None


def _normalize_cl_ellipsoid(value: Optional[str]) -> str:
    if value is None:
        return 'wgs84'
    key = value.strip().lower().replace('-', '').replace('_', '')
    aliases = {
        'wgs84': 'wgs84',
        'nad83': 'nad83',
        'pseudomerc': 'pseudomerc',
        'none': 'none',
    }
    if key in aliases:
        return aliases[key]
    raise ValueError("Unsupported --ell value (use wgs84, nad83, pseudomerc, or none).")


def _parse_cl_ellipsoid_arg(value: str) -> str:
    try:
        return _normalize_cl_ellipsoid(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc))


def _get_geod_from_crs(crs: CRS) -> Geod:
    geod = crs.get_geod()
    if geod is None:
        raise ValueError(f"Unable to derive a geodesic model from CRS {crs!s}.")
    return geod


def _crs_linear_unit_to_meters(crs: Optional[CRS]) -> float:
    if crs is None:
        return 1.0
    try:
        axis_info = crs.axis_info
    except Exception:
        axis_info = None
    if axis_info:
        factor = getattr(axis_info[0], 'unit_conversion_factor', None)
        if factor and factor > 0:
            return float(factor)
    return 1.0


def _build_cl_measure_spec(crs_like: Optional[Any], ellipsoid: str) -> ClMeasureSpec:
    crs = CRS.from_user_input(crs_like) if crs_like else None
    if crs is not None:
        if crs.is_projected:
            return ClMeasureSpec(
                mode='projected',
                crs=crs,
                geod=None,
                coord_unit_to_meters=_crs_linear_unit_to_meters(crs),
            )
        if crs.is_geographic:
            return ClMeasureSpec(
                mode='geodesic',
                crs=crs,
                geod=_get_geod_from_crs(crs),
                coord_unit_to_meters=1.0,
            )
        raise ValueError(f"Unsupported input CRS for street analysis: {crs!s}")

    ell = _normalize_cl_ellipsoid(ellipsoid)
    if ell == 'pseudomerc':
        return ClMeasureSpec(
            mode='projected',
            crs=CRS.from_epsg(3857),
            geod=None,
            coord_unit_to_meters=1.0,
        )
    if ell == 'none':
        return ClMeasureSpec(mode='sphere', crs=None, geod=None, coord_unit_to_meters=1.0)
    return ClMeasureSpec(
        mode='geodesic',
        crs=None,
        geod=_get_geod(ell.upper()),
        coord_unit_to_meters=1.0,
    )


def _normalize_measurement_output(meters: float, unit: str) -> float:
    return round(_from_miles(meters * MILES_PER_METER, unit), 3)


def _is_usable_length_value(value: Any) -> bool:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(numeric) and numeric > 0


def _coerce_cl_text(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, float) and math.isnan(value):
        return ''
    return str(value).strip()


def _iter_linestring_parts(geometry: Any) -> List[Any]:
    if geometry is None or getattr(geometry, 'is_empty', False):
        return []
    geom_type = getattr(geometry, 'geom_type', None)
    if geom_type == 'LineString':
        return [geometry]
    if geom_type == 'MultiLineString':
        return [part for part in geometry.geoms if not part.is_empty]
    return []


def _planar_segment_length_meters(x1: float, y1: float, x2: float, y2: float, spec: ClMeasureSpec) -> float:
    return math.hypot(x2 - x1, y2 - y1) * spec.coord_unit_to_meters


def _point_distance_meters(x1: float, y1: float, x2: float, y2: float, spec: ClMeasureSpec) -> float:
    if spec.mode == 'projected':
        return _planar_segment_length_meters(x1, y1, x2, y2, spec)
    if spec.mode == 'geodesic':
        _, _, dist_m = spec.geod.inv(x1, y1, x2, y2)
        return float(dist_m)
    return haversine(y1, x1, y2, x2, unit='m')


def _segment_bearing_degrees(x1: float, y1: float, x2: float, y2: float, spec: ClMeasureSpec) -> float:
    if spec.mode == 'projected':
        return (math.degrees(math.atan2(x2 - x1, y2 - y1)) + 360.0) % 360.0
    if spec.mode == 'geodesic':
        az1, _, _ = spec.geod.inv(x1, y1, x2, y2)
        return az1 % 360.0
    return initial_bearing(y1, x1, y2, x2)


def _linestring_length_meters(line: Any, spec: ClMeasureSpec) -> float:
    coords = list(line.coords)
    if len(coords) < 2:
        return 0.0
    total = 0.0
    for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
        total += _point_distance_meters(x1, y1, x2, y2, spec)
    return total


def _stored_length_to_meters(value: Any, spec: ClMeasureSpec) -> Optional[float]:
    if spec.mode != 'projected' or not _is_usable_length_value(value):
        return None
    return float(value) * spec.coord_unit_to_meters


def _interpolate_segment_point(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    spec: ClMeasureSpec,
    distance_from_start_m: float,
) -> Any:
    seg_len_m = _point_distance_meters(x1, y1, x2, y2, spec)
    if seg_len_m <= 0:
        return Point(x1, y1)
    frac = max(0.0, min(1.0, distance_from_start_m / seg_len_m))
    if spec.mode == 'projected':
        return Point(
            x1 + frac * (x2 - x1),
            y1 + frac * (y2 - y1),
        )
    if spec.mode == 'geodesic':
        az1, _, seg_dist = spec.geod.inv(x1, y1, x2, y2)
        lon, lat, _ = spec.geod.fwd(x1, y1, az1, seg_dist * frac)
        return Point(lon, lat)
    az1, _, seg_dist_miles = inverse_geodetic(y1, x1, y2, x2, unit='miles', ellipsoid=ELLIPSOID_SPHERE)
    lat, lon, _ = direct_geodetic(
        y1,
        x1,
        az1,
        seg_dist_miles * frac,
        unit='miles',
        ellipsoid=ELLIPSOID_SPHERE,
    )
    return Point(lon, lat)


def _segment_midpoint(line: Any, spec: ClMeasureSpec) -> Any:
    parts = _iter_linestring_parts(line)
    if not parts:
        return Point(0.0, 0.0)

    first_coords = list(parts[0].coords)
    if not first_coords:
        return Point(0.0, 0.0)

    spans = []
    total_length = 0.0
    for part in parts:
        coords = list(part.coords)
        for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
            seg_len_m = _point_distance_meters(x1, y1, x2, y2, spec)
            spans.append((x1, y1, x2, y2, seg_len_m))
            total_length += seg_len_m

    if total_length <= 0:
        return Point(first_coords[0])

    target = total_length / 2.0
    travelled = 0.0
    for x1, y1, x2, y2, seg_len_m in spans:
        if seg_len_m <= 0:
            continue
        if travelled + seg_len_m >= target:
            return _interpolate_segment_point(x1, y1, x2, y2, spec, target - travelled)
        travelled += seg_len_m

    last_coords = list(parts[-1].coords)
    return Point(last_coords[-1])


def _point_coord_distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _join_linestrings_if_connected(left: Any, right: Any) -> Optional[Any]:
    left_coords = list(left.coords)
    right_coords = list(right.coords)
    if len(left_coords) < 2 or len(right_coords) < 2:
        return None

    reversed_left = list(reversed(left_coords))
    reversed_right = list(reversed(right_coords))
    candidates = [
        (_point_coord_distance(left_coords[-1], right_coords[0]), left_coords, right_coords),
        (_point_coord_distance(left_coords[0], right_coords[0]), reversed_left, right_coords),
        (_point_coord_distance(left_coords[-1], right_coords[-1]), left_coords, reversed_right),
        (_point_coord_distance(left_coords[0], right_coords[-1]), reversed_left, reversed_right),
    ]
    gap, first, second = min(candidates, key=lambda item: item[0])
    if gap > CL_GEOMETRY_CONNECT_TOLERANCE:
        return None
    return LineString(first + second[1:])


def _combine_cl_segment_geometries(left_geometry: Any, right_geometry: Any) -> Any:
    left_parts = _iter_linestring_parts(left_geometry)
    right_parts = _iter_linestring_parts(right_geometry)
    if not left_parts:
        return right_geometry
    if not right_parts:
        return left_geometry

    joined = _join_linestrings_if_connected(left_parts[-1], right_parts[0])
    if joined is None:
        parts = left_parts + right_parts
    else:
        parts = left_parts[:-1] + [joined] + right_parts[1:]

    if len(parts) == 1:
        return parts[0]
    return MultiLineString([list(part.coords) for part in parts])


def _merge_cl_segment_pair(
    left: Dict[str, Any],
    right: Dict[str, Any],
    spec: ClMeasureSpec,
) -> Dict[str, Any]:
    left_length = float(left['length_m'])
    right_length = float(right['length_m'])
    base = dict(left)
    combined_geometry = _combine_cl_segment_geometries(left['geometry'], right['geometry'])
    base['length_m'] = left_length + right_length
    base['geometry'] = combined_geometry
    base['midpoint'] = _segment_midpoint(combined_geometry, spec)
    return base


def _cl_segment_sort_key(record: Dict[str, Any]) -> Tuple[float, float, int, int]:
    midpoint = record['midpoint']
    if record['street_class'] == 'NS':
        primary = float(midpoint.y)
        secondary = float(midpoint.x)
    else:
        primary = float(midpoint.x)
        secondary = float(midpoint.y)
    source_index = int(record.get('source_index', 0))
    part_idx = record.get('part_idx')
    if part_idx is None or (isinstance(part_idx, float) and math.isnan(part_idx)):
        part_order = -1
    else:
        part_order = int(part_idx)
    return primary, secondary, source_index, part_order


def _neighbor_index_for_short_segment(records: List[Dict[str, Any]], index: int) -> Optional[int]:
    neighbors = []
    if index > 0:
        neighbors.append(index - 1)
    if index < len(records) - 1:
        neighbors.append(index + 1)
    if not neighbors:
        return None
    return min(neighbors, key=lambda candidate: (float(records[candidate]['length_m']), candidate))


def _merge_short_cl_segment_records(
    records: List[Dict[str, Any]],
    spec: ClMeasureSpec,
    min_segment_length_m: float,
) -> List[Dict[str, Any]]:
    records = sorted((dict(record) for record in records), key=_cl_segment_sort_key)
    while len(records) > 1:
        short_indexes = [
            index
            for index, record in enumerate(records)
            if float(record['length_m']) < min_segment_length_m
        ]
        if not short_indexes:
            break
        short_index = min(short_indexes, key=lambda index: (float(records[index]['length_m']), index))
        neighbor_index = _neighbor_index_for_short_segment(records, short_index)
        if neighbor_index is None:
            break

        left_index = min(short_index, neighbor_index)
        right_index = max(short_index, neighbor_index)
        merged = _merge_cl_segment_pair(records[left_index], records[right_index], spec)
        records = records[:left_index] + [merged] + records[right_index + 1:]
    return records


def _merge_short_cl_segments(segments: Any, spec: ClMeasureSpec, min_segment_length_m: Optional[float]) -> Any:
    if min_segment_length_m is None or min_segment_length_m <= 0 or segments.empty:
        return segments

    records: List[Dict[str, Any]] = []
    for _, group in segments.groupby('street_label', sort=False):
        group_records = group.to_dict('records')
        records.extend(_merge_short_cl_segment_records(group_records, spec, float(min_segment_length_m)))
    merged = gpd.GeoDataFrame(records, geometry='geometry', crs=segments.crs)
    return _assign_cl_segment_identifiers(merged)


def _angular_distance_deg(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _classify_cl_bearing(bearing: float) -> str:
    if min(_angular_distance_deg(bearing, 90.0), _angular_distance_deg(bearing, 270.0)) <= CL_CARDINAL_TOLERANCE_DEG:
        return 'EW'
    if min(_angular_distance_deg(bearing, 0.0), _angular_distance_deg(bearing, 180.0)) <= CL_CARDINAL_TOLERANCE_DEG:
        return 'NS'
    return 'DIAGONAL'


def _build_cl_display_name(street_name: str, quadrant: str) -> str:
    if quadrant:
        return f"{street_name} {quadrant}".strip()
    return street_name


def _build_cl_segment_identifier(street_label: str, ordinal: int, width: int) -> str:
    return f"{street_label} | {ordinal:0{width}d}"


def _assign_cl_segment_identifiers(segments: Any) -> Any:
    if segments.empty:
        return segments

    segments = segments.copy()
    counts = segments['street_label'].value_counts()
    sequence_widths = {
        street_label: max(2, len(str(int(count))))
        for street_label, count in counts.items()
    }
    segments['segment_number'] = segments.groupby('street_label', sort=False).cumcount() + 1
    segments['identifier'] = segments.apply(
        lambda row: _build_cl_segment_identifier(
            row['street_label'],
            int(row['segment_number']),
            sequence_widths[row['street_label']],
        ),
        axis=1,
    )
    return segments


def _optional_length_to_meters(value: Optional[float], unit: str) -> Optional[float]:
    if value is None:
        return None
    numeric = float(value)
    if numeric <= 0:
        return None
    return _to_miles(numeric, unit) * METERS_PER_MILE


def _default_cl_summary_output_path(output_path: Union[str, Path]) -> Path:
    output = Path(output_path)
    return output.with_name(f"{output.stem}_by_street.csv")


def _default_cl_chart_output_path(output_path: Union[str, Path]) -> Path:
    output = Path(output_path)
    return output.with_name(f"{output.stem}_by_street.png")


def _default_cl_gis_output_path() -> Path:
    return CL_DEFAULT_GIS_OUTPUT


def _resolve_cl_gis_output_path(
    gis_output_path: Optional[Union[str, Path]],
    output_path: Union[str, Path],
) -> Path:
    if gis_output_path is None:
        return _default_cl_gis_output_path()
    if isinstance(gis_output_path, Path):
        candidate = gis_output_path
        raw_value = gis_output_path.as_posix()
    else:
        raw_value = str(gis_output_path).strip()
        candidate = Path(raw_value)
    shorthand = raw_value.lower().lstrip(".")
    if shorthand in {"gpkg", "shp"}:
        return Path(output_path).with_suffix(f".{shorthand}")
    return candidate


def _infer_cl_vector_driver(output_path: Union[str, Path]) -> str:
    suffix = Path(output_path).suffix.lower()
    if suffix == '.gpkg':
        return 'GPKG'
    if suffix == '.shp':
        return 'ESRI Shapefile'
    raise ValueError(
        "Unsupported GIS output extension. Use .gpkg (recommended) or .shp."
    )


def _normalize_numeric_range(value: Optional[float], minimum: float, maximum: float) -> float:
    if value is None:
        return 0.5
    if maximum <= minimum:
        return 0.5
    return max(0.0, min(1.0, (float(value) - minimum) / (maximum - minimum)))


def _sample_cl_colormap(street_class: str, normalized_value: float) -> Tuple[int, int, int]:
    try:
        import matplotlib as mpl
    except ImportError as exc:
        raise RuntimeError(
            "CL styling requires matplotlib."
        ) from exc

    cmap_name = 'turbo' if street_class == 'EW' else 'viridis'
    rgba = mpl.colormaps[cmap_name](max(0.0, min(1.0, normalized_value)))
    return tuple(int(round(channel * 255.0)) for channel in rgba[:3])


def _adjust_rgb_lightness(rgb: Tuple[int, int, int], normalized_value: float) -> Tuple[int, int, int]:
    r, g, b = (channel / 255.0 for channel in rgb)
    hue, _, saturation = colorsys.rgb_to_hls(r, g, b)
    lightness = 0.22 + (0.56 * max(0.0, min(1.0, normalized_value)))
    adjusted = colorsys.hls_to_rgb(hue, lightness, saturation)
    return tuple(int(round(channel * 255.0)) for channel in adjusted)


def _rgb_to_hex(rgb: Tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"


def _build_cl_style_context(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    context: Dict[str, Dict[str, Any]] = {}
    for street_class in ('EW', 'NS'):
        class_rows = [row for row in rows if row['street_class'] == street_class]
        str_ids = sorted({int(row['str_id']) for row in class_rows})
        base_colors: Dict[int, Tuple[int, int, int]] = {}
        if str_ids:
            min_str_id = min(str_ids)
            max_str_id = max(str_ids)
            for str_id in str_ids:
                normalized = _normalize_numeric_range(str_id, min_str_id, max_str_id)
                base_colors[str_id] = _sample_cl_colormap(street_class, normalized)
        dist_values = []
        for row in class_rows:
            if row.get('dist_1') is not None:
                dist_values.append(float(row['dist_1']))
            if row.get('dist_2') is not None:
                dist_values.append(float(row['dist_2']))
        context[street_class] = {
            'palette': 'Turbo' if street_class == 'EW' else 'Viridis',
            'base_colors': base_colors,
            'dist_min': min(dist_values) if dist_values else 0.0,
            'dist_max': max(dist_values) if dist_values else 0.0,
        }
    return context


def _resolve_row_style(row: Dict[str, Any], style_context: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    class_style = style_context[row['street_class']]
    base_rgb = class_style['base_colors'][int(row['str_id'])]
    return {
        'palette': class_style['palette'],
        'base_rgb': base_rgb,
        'base_hex': _rgb_to_hex(base_rgb),
    }


def _build_cl_vector_layers(segments: Any, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    layer_columns = [
        'source_id',
        'street',
        'str_id',
        'str_class',
        'bearing',
        'length',
        'length_m',
        'street_1',
        'str_id1',
        'dir_1',
        'dist_1',
        'dist1_m',
        'street_2',
        'str_id2',
        'dir_2',
        'dist_2',
        'dist2_m',
        'unit',
        'palette',
        'base_hex',
        'base_r',
        'base_g',
        'base_b',
        'ext_hex',
        'ext_r',
        'ext_g',
        'ext_b',
        'tone_norm',
        'stroke_px',
        'point_px',
        'side',
        'mid_x',
        'mid_y',
        'hit_x',
        'hit_y',
        'geometry',
    ]
    empty_gdf = gpd.GeoDataFrame(columns=layer_columns, geometry='geometry', crs=getattr(segments, 'crs', None))
    layers = {
        CL_GPKG_LAYER: _build_cl_bundle_layer(segments, rows),
        CL_GPKG_SEGMENT_LAYERS['EW']: empty_gdf.copy(),
        CL_GPKG_SEGMENT_LAYERS['NS']: empty_gdf.copy(),
        CL_GPKG_MIDPOINT_LAYERS['EW']: empty_gdf.copy(),
        CL_GPKG_MIDPOINT_LAYERS['NS']: empty_gdf.copy(),
        CL_GPKG_EXTENSION_LAYERS['EW']: empty_gdf.copy(),
        CL_GPKG_EXTENSION_LAYERS['NS']: empty_gdf.copy(),
    }
    if segments.empty or not rows:
        return layers

    segments = segments.reset_index(drop=True).copy()
    segments['source_pos'] = segments.index
    segment_by_source = {int(row.source_pos): row for row in segments.itertuples(index=False)}
    style_context = _build_cl_style_context(rows)
    segment_records: Dict[str, List[Dict[str, Any]]] = {name: [] for name in CL_GPKG_SEGMENT_LAYERS.values()}
    midpoint_records: Dict[str, List[Dict[str, Any]]] = {name: [] for name in CL_GPKG_MIDPOINT_LAYERS.values()}
    extension_records: Dict[str, List[Dict[str, Any]]] = {name: [] for name in CL_GPKG_EXTENSION_LAYERS.values()}

    for row in rows:
        source_segment = segment_by_source[int(row['source_pos'])]
        style = _resolve_row_style(row, style_context)
        street_class = row['street_class']
        base_layer = CL_GPKG_SEGMENT_LAYERS[street_class]
        midpoint_layer = CL_GPKG_MIDPOINT_LAYERS[street_class]
        extension_layer = CL_GPKG_EXTENSION_LAYERS[street_class]

        segment_common = {
            'source_id': row['source_segment_id'],
            'street': row['street'],
            'str_id': row['str_id'],
            'str_class': street_class,
            'bearing': row['bearing'],
            'length': row['length'],
            'length_m': row['length_m'],
            'street_1': row['street_1'],
            'str_id1': row['str_id1'],
            'dir_1': row['dir_1'],
            'dist_1': row['dist_1'],
            'dist1_m': row['dist_1_m'],
            'street_2': row['street_2'],
            'str_id2': row['str_id2'],
            'dir_2': row['dir_2'],
            'dist_2': row['dist_2'],
            'dist2_m': row['dist_2_m'],
            'unit': row['unit'],
            'palette': style['palette'],
            'base_hex': style['base_hex'],
            'base_r': style['base_rgb'][0],
            'base_g': style['base_rgb'][1],
            'base_b': style['base_rgb'][2],
            'ext_hex': None,
            'ext_r': None,
            'ext_g': None,
            'ext_b': None,
            'tone_norm': None,
            'stroke_px': 1.5,
            'point_px': 2.0,
            'side': None,
            'mid_x': row['mid_x'],
            'mid_y': row['mid_y'],
            'hit_x': None,
            'hit_y': None,
        }
        segment_records[base_layer].append({
            **segment_common,
            'geometry': source_segment.geometry,
        })
        midpoint_records[midpoint_layer].append({
            **segment_common,
            'geometry': Point(row['mid_x'], row['mid_y']),
        })

        for side_idx, side in enumerate(('1', '2'), start=1):
            ext_geom = row.get(f'ext_geom_{side_idx}')
            distance = row.get(f'dist_{side}')
            if ext_geom is None or distance is None:
                continue
            tone_norm = _normalize_numeric_range(
                float(distance),
                style_context[street_class]['dist_min'],
                style_context[street_class]['dist_max'],
            )
            ext_rgb = _adjust_rgb_lightness(style['base_rgb'], tone_norm)
            extension_records[extension_layer].append({
                **segment_common,
                'ext_hex': _rgb_to_hex(ext_rgb),
                'ext_r': ext_rgb[0],
                'ext_g': ext_rgb[1],
                'ext_b': ext_rgb[2],
                'tone_norm': round(tone_norm, 4),
                'side': row.get(f'dir_{side}'),
                'hit_x': row.get(f'hit{side_idx}_x'),
                'hit_y': row.get(f'hit{side_idx}_y'),
                'geometry': ext_geom,
            })

    for layer_name, records in segment_records.items():
        if records:
            layers[layer_name] = gpd.GeoDataFrame(records, geometry='geometry', crs=segments.crs)
    for layer_name, records in midpoint_records.items():
        if records:
            layers[layer_name] = gpd.GeoDataFrame(records, geometry='geometry', crs=segments.crs)
    for layer_name, records in extension_records.items():
        if records:
            layers[layer_name] = gpd.GeoDataFrame(records, geometry='geometry', crs=segments.crs)
    return layers


def _make_midpoint_ray(midpoint: Any, bounds: Tuple[float, float, float, float], side: str) -> Any:
    minx, miny, maxx, maxy = bounds
    width = maxx - minx
    height = maxy - miny
    margin = max(width, height, 1.0) * 2.0
    if side == 'N':
        endpoint = (midpoint.x, maxy + margin)
    elif side == 'S':
        endpoint = (midpoint.x, miny - margin)
    elif side == 'E':
        endpoint = (maxx + margin, midpoint.y)
    elif side == 'W':
        endpoint = (minx - margin, midpoint.y)
    else:
        raise ValueError(f"Unsupported side {side}")
    return LineString([(midpoint.x, midpoint.y), endpoint])


def _point_is_on_side(midpoint: Any, point: Any, side: str) -> bool:
    eps = 1e-12
    if side == 'N':
        return point.y > midpoint.y + eps
    if side == 'S':
        return point.y < midpoint.y - eps
    if side == 'E':
        return point.x > midpoint.x + eps
    if side == 'W':
        return point.x < midpoint.x - eps
    raise ValueError(f"Unsupported side {side}")


def _first_hit_for_ray(
    source_pos: int,
    midpoint: Any,
    ray: Any,
    side: str,
    candidates: Any,
    spec: ClMeasureSpec,
) -> Optional[Dict[str, Any]]:
    candidate_positions = candidates.sindex.query(ray, predicate='intersects')
    midpoint_point = Point(midpoint.x, midpoint.y)
    best_meters = None
    best_hit = None
    for candidate_pos in candidate_positions:
        if int(candidate_pos) == source_pos:
            continue
        candidate_geom = candidates.geometry.iloc[int(candidate_pos)]
        intersection = candidate_geom.intersection(ray)
        if intersection.is_empty:
            continue
        hit_point = nearest_points(midpoint_point, intersection)[1]
        if not _point_is_on_side(midpoint_point, hit_point, side):
            continue
        dist_m = _point_distance_meters(midpoint.x, midpoint.y, hit_point.x, hit_point.y, spec)
        if dist_m <= 0:
            continue
        if best_meters is None or dist_m < best_meters:
            best_meters = dist_m
            candidate = candidates.iloc[int(candidate_pos)]
            best_hit = {
                'street': candidate['street_label'],
                'str_id': int(candidate['segment_number']),
                'side': side,
                'distance_m': float(dist_m),
                'hit_point': hit_point,
            }
    return best_hit


def _prepare_cl_segments(
    gdf: Any,
    ellipsoid: str,
    min_segment_length_m: Optional[float] = None,
) -> Tuple[Any, ClMeasureSpec]:
    _require_cl_dependencies()
    if gdf is None or gdf.empty:
        raise ValueError('Input street dataset is empty.')

    gdf = gdf.copy()
    name_field = _find_preferred_field(gdf.columns, CL_NAME_FIELDS)
    if name_field is None:
        raise ValueError(
            'Could not infer a street-name field. Tried: '
            + ', '.join(CL_NAME_FIELDS)
        )
    quadrant_field = _find_preferred_field(gdf.columns, CL_QUADRANT_FIELDS)
    roadtype_field = _find_preferred_field(gdf.columns, CL_ROADTYPE_FIELDS)
    streettype_field = _find_preferred_field(gdf.columns, CL_TYPE_FIELDS)
    if roadtype_field is not None:
        roadtype_mask = (
            gdf[roadtype_field]
            .fillna('')
            .astype(str)
            .str.strip()
            .str.upper()
            == 'STREET'
        )
        gdf = gdf[roadtype_mask].copy()
    if streettype_field is not None:
        streettype_mask = (
            gdf[streettype_field]
            .fillna('')
            .astype(str)
            .str.strip()
            .str.upper()
            .isin({'ST', 'STREET'})
        )
        gdf = gdf[streettype_mask].copy()

    gdf = gdf[gdf.geometry.notnull()].copy()
    gdf = gdf[gdf.geometry.geom_type.isin(['LineString', 'MultiLineString'])].copy()
    if gdf.empty:
        raise ValueError('No line geometries remain after street filtering.')

    id_field = _find_preferred_field(gdf.columns, CL_ID_FIELDS)
    length_field = _find_preferred_field(gdf.columns, CL_LENGTH_FIELDS)
    spec = _build_cl_measure_spec(gdf.crs, ellipsoid)

    records = []
    for row in gdf.itertuples(index=True):
        geometry = row.geometry
        street_name = _coerce_cl_text(getattr(row, name_field))
        if not street_name:
            continue
        quadrant = _coerce_cl_text(getattr(row, quadrant_field)) if quadrant_field is not None else ''
        street_label = _build_cl_display_name(street_name, quadrant)
        parts = _iter_linestring_parts(geometry)
        if not parts:
            continue
        stored_length_value = getattr(row, length_field) if length_field is not None else None
        for part_idx, part in enumerate(parts):
            length_m = None
            if len(parts) == 1:
                length_m = _stored_length_to_meters(stored_length_value, spec)
            if length_m is None:
                length_m = _linestring_length_meters(part, spec)
            if length_m <= 0:
                continue
            coords = list(part.coords)
            if len(coords) < 2:
                continue
            bearing = _segment_bearing_degrees(coords[0][0], coords[0][1], coords[-1][0], coords[-1][1], spec)
            midpoint = _segment_midpoint(part, spec)
            part_label = part_idx if len(parts) > 1 else None
            records.append({
                'source_segment_id': getattr(row, id_field) if id_field is not None else row.Index,
                'street_name': street_name,
                'street_label': street_label,
                'length_m': float(length_m),
                'bearing': round(bearing, 3),
                'street_class': _classify_cl_bearing(bearing),
                'midpoint': midpoint,
                'geometry': part,
                'part_idx': part_label,
                'source_index': int(row.Index),
            })

    if not records:
        raise ValueError('No usable street segments were found in the input dataset.')

    segments = gpd.GeoDataFrame(records, geometry='geometry', crs=gdf.crs)
    segments = segments[segments['street_class'].isin(['EW', 'NS'])].copy()
    if segments.empty:
        return segments, spec

    segments = _assign_cl_segment_identifiers(segments)
    segments = _merge_short_cl_segments(segments, spec, min_segment_length_m)
    return segments.copy(), spec


def _build_cl_rows(segments: Any, spec: ClMeasureSpec, unit: str) -> List[Dict[str, Any]]:
    if segments.empty:
        return []

    segments = segments.reset_index(drop=True).copy()
    segments['source_pos'] = segments.index
    bounds = tuple(float(value) for value in segments.total_bounds)
    class_groups = {}
    class_positions = {}
    for street_class in ('EW', 'NS'):
        class_gdf = segments[segments['street_class'] == street_class].copy()
        class_gdf = class_gdf.reset_index(drop=True)
        class_groups[street_class] = class_gdf
        class_positions[street_class] = {
            int(source_pos): idx for idx, source_pos in enumerate(class_gdf['source_pos'])
        }

    rows = []
    for source_pos, row in segments.iterrows():
        street_class = row['street_class']
        class_gdf = class_groups[street_class]
        if class_gdf.empty:
            continue
        class_pos = class_positions[street_class][int(source_pos)]
        if street_class == 'EW':
            sides = ('N', 'S')
        else:
            sides = ('E', 'W')
        hits = []
        for side in sides:
            ray = _make_midpoint_ray(row['midpoint'], bounds, side)
            hit = _first_hit_for_ray(
                class_pos,
                row['midpoint'],
                ray,
                side,
                class_gdf,
                spec,
            )
            hits.append(hit)

        midpoint = row['midpoint']
        ext_geom_1 = None
        ext_geom_2 = None
        if hits[0] is not None:
            ext_geom_1 = LineString([
                (midpoint.x, midpoint.y),
                (hits[0]['hit_point'].x, hits[0]['hit_point'].y),
            ])
        if hits[1] is not None:
            ext_geom_2 = LineString([
                (midpoint.x, midpoint.y),
                (hits[1]['hit_point'].x, hits[1]['hit_point'].y),
            ])

        rows.append({
            'source_pos': int(row['source_pos']),
            'source_segment_id': row['source_segment_id'],
            'identifier': row['identifier'],
            'street': row['street_label'],
            'str_id': int(row['segment_number']),
            'street_class': row['street_class'],
            'length': _normalize_measurement_output(row['length_m'], unit),
            'length_m': float(row['length_m']),
            'bearing': round(float(row['bearing']), 3),
            'street_1': hits[0]['street'] if hits[0] is not None else None,
            'str_id1': hits[0]['str_id'] if hits[0] is not None else None,
            'dir_1': hits[0]['side'] if hits[0] is not None else None,
            'dist_1': (
                _normalize_measurement_output(hits[0]['distance_m'], unit)
                if hits[0] is not None else None
            ),
            'dist_1_m': hits[0]['distance_m'] if hits[0] is not None else None,
            'street_2': hits[1]['street'] if hits[1] is not None else None,
            'str_id2': hits[1]['str_id'] if hits[1] is not None else None,
            'dir_2': hits[1]['side'] if hits[1] is not None else None,
            'dist_2': (
                _normalize_measurement_output(hits[1]['distance_m'], unit)
                if hits[1] is not None else None
            ),
            'dist_2_m': hits[1]['distance_m'] if hits[1] is not None else None,
            'unit': unit,
            'mid_x': midpoint.x,
            'mid_y': midpoint.y,
            'hit1_x': hits[0]['hit_point'].x if hits[0] is not None else None,
            'hit1_y': hits[0]['hit_point'].y if hits[0] is not None else None,
            'hit2_x': hits[1]['hit_point'].x if hits[1] is not None else None,
            'hit2_y': hits[1]['hit_point'].y if hits[1] is not None else None,
            'ext_geom_1': ext_geom_1,
            'ext_geom_2': ext_geom_2,
        })
    return rows


def _load_cl_dataset(input_path: Union[str, Path]) -> Any:
    _require_cl_dependencies()
    return gpd.read_file(str(input_path))


def _write_cl_csv(rows: List[Dict[str, Any]], output_path: Union[str, Path]) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=CL_OUTPUT_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                field: '' if row.get(field) is None else row.get(field)
                for field in CL_OUTPUT_FIELDS
            })


def _build_cl_summary(rows: List[Dict[str, Any]], unit: str) -> Any:
    if not rows:
        return pd.DataFrame(columns=CL_SUMMARY_FIELDS)

    frame = pd.DataFrame([
        {
            'street': row['street'],
            'street_class': row['street_class'],
            'str_id': row['str_id'],
            'length': row['length'],
            'bearing': row['bearing'],
            'dist_1': row['dist_1'],
            'dist_2': row['dist_2'],
        }
        for row in rows
    ])
    summary = (
        frame
        .groupby(['street', 'street_class'], dropna=False, sort=True)
        .agg(
            segment_count=('str_id', 'count'),
            str_id_min=('str_id', 'min'),
            str_id_max=('str_id', 'max'),
            length_sum=('length', 'sum'),
            length_mean=('length', 'mean'),
            length_min=('length', 'min'),
            length_max=('length', 'max'),
            bearing_mean=('bearing', 'mean'),
            bearing_min=('bearing', 'min'),
            bearing_max=('bearing', 'max'),
            dist_1_count=('dist_1', 'count'),
            dist_1_mean=('dist_1', 'mean'),
            dist_1_min=('dist_1', 'min'),
            dist_1_max=('dist_1', 'max'),
            dist_2_count=('dist_2', 'count'),
            dist_2_mean=('dist_2', 'mean'),
            dist_2_min=('dist_2', 'min'),
            dist_2_max=('dist_2', 'max'),
        )
        .reset_index()
        .sort_values(['street_class', 'street'])
        .reset_index(drop=True)
    )
    float_columns = [
        'length_sum',
        'length_mean',
        'length_min',
        'length_max',
        'bearing_mean',
        'bearing_min',
        'bearing_max',
        'dist_1_mean',
        'dist_1_min',
        'dist_1_max',
        'dist_2_mean',
        'dist_2_min',
        'dist_2_max',
    ]
    if not summary.empty:
        summary[float_columns] = summary[float_columns].round(3)
        for field in ('segment_count', 'str_id_min', 'str_id_max', 'dist_1_count', 'dist_2_count'):
            summary[field] = summary[field].astype(int)
    summary['unit'] = unit
    return summary[CL_SUMMARY_FIELDS]


def _write_cl_summary_csv(summary: Any, output_path: Union[str, Path]) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(output, index=False)


def _write_cl_summary_chart(
    summary: Any,
    output_path: Union[str, Path],
    unit: str,
) -> None:
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError(
            "The cl chart output requires matplotlib."
        ) from exc

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(4, 1, figsize=(18, 12), sharex=True, constrained_layout=True)
    if summary.empty:
        axes[0].text(0.5, 0.5, 'No street segments to plot.', ha='center', va='center')
        for ax in axes:
            ax.set_axis_off()
        fig.savefig(output, dpi=200)
        plt.close(fig)
        return

    ordered = summary.reset_index(drop=True)
    x_values = list(range(len(ordered)))
    labels = ordered['street'].tolist()
    tick_step = max(1, math.ceil(len(labels) / 60.0))
    tick_positions = x_values[::tick_step] if x_values else []
    tick_labels = labels[::tick_step] if labels else []

    axes[0].bar(x_values, ordered['segment_count'].tolist(), color='#4361ee')
    axes[0].set_ylabel('Segments')
    axes[0].set_title('Street Centerline Summary')

    axes[1].bar(x_values, ordered['length_sum'].tolist(), color='#f9844a')
    axes[1].set_ylabel(f'Total Length ({unit})')

    axes[2].plot(
        x_values,
        ordered['dist_1_mean'].tolist(),
        color='#43aa8b',
        linewidth=1.5,
        label='dist_1 mean',
    )
    axes[2].plot(
        x_values,
        ordered['dist_2_mean'].tolist(),
        color='#577590',
        linewidth=1.5,
        label='dist_2 mean',
    )
    axes[2].set_ylabel(f'Mean Dist. ({unit})')
    axes[2].legend(loc='upper right')

    axes[3].plot(
        x_values,
        ordered['bearing_mean'].tolist(),
        color='#bc4749',
        linewidth=1.2,
        marker='o',
        markersize=2.5,
    )
    axes[3].set_ylabel('Bearing')
    axes[3].set_xlabel('Street')
    axes[3].set_ylim(-5.0, 365.0)
    axes[3].set_xticks(tick_positions)
    axes[3].set_xticklabels(tick_labels, rotation=90, fontsize=8)

    for ax in axes:
        ax.grid(axis='y', alpha=0.25)

    fig.savefig(output, dpi=200)
    plt.close(fig)


def _build_cl_bundle_geometry(
    segment_geometry: Any,
    extension_geometries: Iterable[Any],
) -> Any:
    parts = []
    for geometry in (segment_geometry, *[geom for geom in extension_geometries if geom is not None]):
        parts.extend(_iter_linestring_parts(geometry))
    return MultiLineString([list(part.coords) for part in parts])


def _build_cl_bundle_layer(segments: Any, rows: List[Dict[str, Any]]) -> Any:
    if segments.empty or not rows:
        return gpd.GeoDataFrame(
            columns=[
                'source_id',
                'street',
                'str_id',
                'str_class',
                'length',
                'length_m',
                'bearing',
                'street_1',
                'str_id1',
                'dir_1',
                'dist_1',
                'dist1_m',
                'street_2',
                'str_id2',
                'dir_2',
                'dist_2',
                'dist2_m',
                'unit',
                'mid_x',
                'mid_y',
                'hit1_x',
                'hit1_y',
                'hit2_x',
                'hit2_y',
                'geometry',
            ],
            geometry='geometry',
            crs=getattr(segments, 'crs', None),
        )

    segments = segments.reset_index(drop=True).copy()
    segments['source_pos'] = segments.index
    rows_by_source = {int(row['source_pos']): row for row in rows}
    records = []
    for _, segment in segments.iterrows():
        row = rows_by_source.get(int(segment['source_pos']))
        if row is None:
            continue
        records.append({
            'source_id': row['source_segment_id'],
            'street': row['street'],
            'str_id': row['str_id'],
            'str_class': row['street_class'],
            'length': row['length'],
            'length_m': row['length_m'],
            'bearing': row['bearing'],
            'street_1': row['street_1'],
            'str_id1': row['str_id1'],
            'dir_1': row['dir_1'],
            'dist_1': row['dist_1'],
            'dist1_m': row['dist_1_m'],
            'street_2': row['street_2'],
            'str_id2': row['str_id2'],
            'dir_2': row['dir_2'],
            'dist_2': row['dist_2'],
            'dist2_m': row['dist_2_m'],
            'unit': row['unit'],
            'mid_x': row['mid_x'],
            'mid_y': row['mid_y'],
            'hit1_x': row['hit1_x'],
            'hit1_y': row['hit1_y'],
            'hit2_x': row['hit2_x'],
            'hit2_y': row['hit2_y'],
            'geometry': _build_cl_bundle_geometry(
                segment.geometry,
                (row['ext_geom_1'], row['ext_geom_2']),
            ),
        })
    return gpd.GeoDataFrame(records, geometry='geometry', crs=segments.crs)


def _write_cl_vector(vector_layers: Dict[str, Any], output_path: Union[str, Path]) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    driver = _infer_cl_vector_driver(output)
    if driver == 'GPKG':
        if output.exists():
            output.unlink()
        wrote_any = False
        for layer_name, layer_gdf in vector_layers.items():
            if layer_gdf.empty:
                continue
            kwargs: Dict[str, Any] = {'driver': driver, 'layer': layer_name}
            if wrote_any:
                kwargs['mode'] = 'a'
            layer_gdf.to_file(output, **kwargs)
            wrote_any = True
        return

    bundle_layer = vector_layers.get(CL_GPKG_LAYER)
    if bundle_layer is None or bundle_layer.empty:
        return
    bundle_layer.to_file(output, driver=driver)


def analyze_street_centerlines(
    input_path: Union[str, Path] = CL_DEFAULT_INPUT,
    output_path: Union[str, Path] = CL_DEFAULT_OUTPUT,
    unit: str = 'feet',
    ellipsoid: str = 'wgs84',
    min_segment_length: Optional[float] = None,
    summary_output_path: Optional[Union[str, Path]] = None,
    chart_output_path: Optional[Union[str, Path]] = None,
    gis_output_path: Optional[Union[str, Path]] = None,
) -> int:
    dataset = _load_cl_dataset(input_path)
    min_segment_length_m = _optional_length_to_meters(min_segment_length, unit)
    segments, spec = _prepare_cl_segments(dataset, ellipsoid, min_segment_length_m)
    rows = _build_cl_rows(segments, spec, unit)
    _write_cl_csv(rows, output_path)
    summary_output = Path(summary_output_path) if summary_output_path else _default_cl_summary_output_path(output_path)
    chart_output = Path(chart_output_path) if chart_output_path else _default_cl_chart_output_path(output_path)
    gis_output = _resolve_cl_gis_output_path(gis_output_path, output_path)
    summary = _build_cl_summary(rows, unit)
    _write_cl_summary_csv(summary, summary_output)
    _write_cl_summary_chart(summary, chart_output, unit)
    vector_layers = _build_cl_vector_layers(segments, rows)
    _write_cl_vector(vector_layers, gis_output)
    return len(rows)



def _run_cl(args: argparse.Namespace) -> None:
    summary_output = Path(args.summary_output) if args.summary_output else _default_cl_summary_output_path(args.output)
    chart_output = Path(args.chart_output) if args.chart_output else _default_cl_chart_output_path(args.output)
    gis_output = _resolve_cl_gis_output_path(args.gis_output, args.output)
    rows_written = analyze_street_centerlines(
        input_path=args.input,
        output_path=args.output,
        unit=args.units,
        ellipsoid=args.ell,
        min_segment_length=args.min_segment_length,
        summary_output_path=summary_output,
        chart_output_path=chart_output,
        gis_output_path=gis_output,
    )
    print(f"Wrote {rows_written} rows to {args.output}")
    print(f"Wrote per-street summary to {summary_output}")
    print(f"Wrote summary chart to {chart_output}")
    print(f"Wrote GIS layers to {gis_output}")


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description='Street centerline analysis')
    parser.add_argument(
        '--input',
        default=str(CL_DEFAULT_INPUT),
        help='Input line dataset path (default: %(default)s)',
    )
    parser.add_argument(
        '--output',
        default=str(CL_DEFAULT_OUTPUT),
        help='Output CSV path (default: %(default)s)',
    )
    parser.add_argument(
        '--summary-output',
        default=None,
        help='Per-street summary CSV path. If omitted, uses the main --output path with _by_street.csv appended.',
    )
    parser.add_argument(
        '--chart-output',
        default=None,
        help='Per-street summary chart path. If omitted, uses the main --output path with _by_street.png appended.',
    )
    parser.add_argument(
        '--gis-output',
        default=str(CL_DEFAULT_GIS_OUTPUT),
        help='GIS output path, or use gpkg/shp to reuse the main --output stem with that extension (default: %(default)s)',
    )
    parser.add_argument(
        '--units',
        choices=['feet', 'miles', 'poles', 'm', 'km'],
        default='feet',
        help='Output distance units (default: %(default)s)',
    )
    parser.add_argument(
        '--ell',
        type=_parse_cl_ellipsoid_arg,
        default='wgs84',
        help='Fallback ellipsoid when the input has no CRS: wgs84, nad83, pseudomerc, or none.',
    )
    parser.add_argument(
        '--min-segment-length',
        type=float,
        default=None,
        help='Merge same-street segments shorter than this length into their smaller neighboring segment. Uses --units.',
    )
    _run_cl(parser.parse_args(argv))


if __name__ == '__main__':
    main()
