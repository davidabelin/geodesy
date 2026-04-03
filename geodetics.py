import csv
import os
import math
import json
import argparse
import sys
from collections import defaultdict
from functools import lru_cache
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Tuple, Callable, Optional, Union, Iterable, Dict
from pyproj import CRS, Geod

# Optional dependencies
try:
    import numpy as np
    import re
except ImportError:
    np = None
    re = None

# I/O dependencies
try:
    import rasterio
    from rasterio.mask import mask as rio_mask
    import geopandas as gpd
    import pandas as pd
    import simplekml
    from shapely.geometry import LineString, MultiLineString, Point, shape
    from shapely.ops import nearest_points
except ImportError:
    rasterio = None
    gpd = None
    pd = None
    simplekml = None
    LineString = None
    MultiLineString = None
    Point = None
    nearest_points = None

# Visualization (deferred imports in functions)

#===============================================================================
# Constants and Unit Conversions
#===============================================================================
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
STREETS_DEFAULT_INPUT = Path("roadways") / "data" / "DC_Street_Centerlines" / "Street_Centerlines_2013.shp"
STREETS_DEFAULT_OUTPUT = Path("roadways") / "data" / "centerline_stats.csv"
STREET_NAME_FIELDS = ("ST_NAME", "FULLNAME", "NAME", "ROUTENAME")
STREET_ID_FIELDS = ("STREETSEGID", "STREETSEGI", "OBJECTID", "FID")
STREET_LENGTH_FIELDS = ("SHAPE.LEN", "SHAPELEN", "LENGTH", "SHAPE_LENGTH")
STREET_ROADTYPE_FIELDS = ("ROADTYPE",)
STREET_CARDINAL_TOLERANCE_DEG = 5.0

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

#===============================================================================
# Core Geodesic Functions
#===============================================================================

# ----------------------------------------
# Vector and Spherical-Triangle Utilities
# ----------------------------------------

def spherical_triangle_properties(point_A, point_B, point_C):
    """
    Calculates the interior angles and side lengths of a spherical triangle
    defined by three points on the Earth's surface.

    Args:
        point_A: Tuple (latitude, longitude) of point A (in degrees).
        point_B: Tuple (latitude, longitude) of point B (in degrees).
        point_C: Tuple (latitude, longitude) of point C (in degrees).

    Returns:
        A dictionary containing:
        - angles: A dictionary of angles {A, B, C} (in radians).
        - side_lengths: A dictionary of side lengths {AB, BC, CA} (same units as R).
    """

    lat_A, lon_A = point_A
    lat_B, lon_B = point_B
    lat_C, lon_C = point_C

    # Calculate side lengths using the Haversine formula
    side_AB = haversine(lat_A, lon_A, lat_B, lon_B)
    side_BC = haversine(lat_B, lon_B, lat_C, lon_C)
    side_CA = haversine(lat_C, lon_C, lat_A, lon_A)

    # Calculate angles using the Spherical Law of Cosines
    angle_A, angle_B, angle_C = spherical_law_of_cosines_angles(side_BC, side_CA, side_AB)
    
    return {
        "angles": {"A": angle_A, "B": angle_B, "C": angle_C},
        "side_lengths": {"AB": side_AB, "BC": side_BC, "CA": side_CA},
    }

def latlon_to_xyz(phi_rad, lam_rad):
    """Convert latitude, longitude in radians to 3D unit vector."""
    cos_phi = math.cos(phi_rad)
    return (
        cos_phi * math.cos(lam_rad),
        cos_phi * math.sin(lam_rad),
        math.sin(phi_rad)
    )

def angle_between(u, v):
    """Central angle in radians between two 3D unit vectors."""
    dot = max(-1.0, min(1.0, u[0]*v[0] + u[1]*v[1] + u[2]*v[2]))
    return math.acos(dot)

def spherical_law_of_cosines_angles(a, b, c, radius=EARTH_RADIUS_MILES):
    """Return interior angles (radians) of a spherical triangle from side lengths a,b,c."""
    a_r, b_r, c_r = a/radius, b/radius, c/radius
    def clamp(x): return max(-1.0, min(1.0, x))
    cosA = clamp((math.cos(a_r) - math.cos(b_r)*math.cos(c_r)) /
                  (math.sin(b_r)*math.sin(c_r)))
    cosB = clamp((math.cos(b_r) - math.cos(a_r)*math.cos(c_r)) /
                  (math.sin(a_r)*math.sin(c_r)))
    cosC = clamp((math.cos(c_r) - math.cos(a_r)*math.cos(b_r)) /
                  (math.sin(a_r)*math.sin(b_r)))
    return math.acos(cosA), math.acos(cosB), math.acos(cosC)

def spherical_triangle_area(radius, p1, p2, p3):
    """
    Compute area of spherical triangle on sphere of given radius.
    Each p? is (phi_rad, lam_rad).
    Uses Girard's theorem: E = A+B+C - pi, area = E*R^2.
    """
    # Convert vertices to unit vectors
    u = latlon_to_xyz(p1[0], p1[1])
    v = latlon_to_xyz(p2[0], p2[1])
    w = latlon_to_xyz(p3[0], p3[1])
    # Side central angles (radians)
    a = angle_between(v, w)
    b = angle_between(u, w)
    c = angle_between(u, v)
    # Interior angles
    A, B, C = spherical_law_of_cosines_angles(a*radius, b*radius, c*radius, radius)
    # Spherical excess
    E = A + B + C - math.pi
    return E * radius * radius

#===============================================================================
# Spherical Trigonometry Utilities
#===============================================================================

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

# shorthand endpoint throws
def throw_point(start_lat: float,
                   start_lon: float,
                   bearing: float,
                   distance: float,
                   units: str='miles', radius: float=EARTH_RADIUS_MILES,
                   ellipsoid: Union[str, bool, None]=DEFAULT_ELLIPSOID) -> Tuple[float,float,float]:
    """
    Compute endpoint given start, distance, and bearing. Supports feet/yards/poles.
    """
    ellps = _normalize_ellipsoid(ellipsoid)
    if ellps != ELLIPSOID_SPHERE:
        return direct_geodetic(start_lat, start_lon, bearing, distance, unit=units, ellipsoid=ellps)
    dist_miles = _to_miles(distance, units)
    phi1 = math.radians(start_lat)
    lam1 = math.radians(start_lon)
    az1 = math.radians(bearing)
    sigma = dist_miles / radius
    phi2 = math.asin(math.sin(phi1)*math.cos(sigma) + math.cos(phi1)*math.sin(sigma)*math.cos(az1))
    lam2 = lam1 + math.atan2(math.sin(az1)*math.sin(sigma)*math.cos(phi1),
                              math.cos(sigma) - math.sin(phi1)*math.sin(phi2))
    az2 = math.atan2(math.sin(az1)*math.cos(phi1)*math.cos(sigma) - math.sin(phi1)*math.sin(sigma),
                     math.cos(az1)*math.cos(sigma))
    lat2 = math.degrees(phi2)
    lon2 = math.degrees((lam2 + 3*math.pi) % (2*math.pi) - math.pi)
    return lat2, lon2, az2*RAD2DEG

#===============================================================================
# Root-Solving Wrappers
#===============================================================================
from scipy.optimize import brentq, fsolve

def bracket_root(func: Callable[..., float], a: float, b: float, *args, **kwargs) -> float:
    """
    Find a root of func(x, *args) in [a,b] via Brent's method; raises on failure.
    """
    try:
        return brentq(func, a, b, args=args, **kwargs)
    except Exception as e:
        raise ValueError(f"Brentq failed: {e}")

def fsolve_root(func: Callable[..., float], x0: float, *args, **kwargs) -> float:
    """
    Find a root near x0 using fsolve; raises if no convergence.
    """
    sol, info, ier, mesg = fsolve(lambda x: func(x, *args), x0, full_output=True, **kwargs)
    if ier != 1:
        raise ValueError(f"fsolve did not converge (ier={ier}): {mesg}")
    return sol[0]

#===============================================================================
# Latitude/Longitude Finder
#===============================================================================

def find_longitudes_for_known_latitude_and_distance(
                            lat1_deg, lon1_deg, distance_miles, 
                            lat2_target_deg, ellipsoid: Union[str, bool, None]=DEFAULT_ELLIPSOID, tol=1e-9
    ):
    """
    Finds the longitude(s) of a second point, given the first point (lat1, lon1),
    the distance to the second point, and the latitude of the second point (lat2).

    Args:
        lat1_deg (float): Latitude of the first point in degrees.
        lon1_deg (float): Longitude of the first point in degrees.
        distance_miles (float): Distance from the first point to the second in miles.
        lat2_target_deg (float): Latitude of the second point in degrees.
        ellipsoid (str|bool|None): WGS84 (default), NAD83, or sphere.
        tol (float): Tolerance for floating point comparisons.

    Returns:
        list: A list of possible longitudes (in degrees) for the second point.
              Returns an empty list if no solution is found.
              May return a special list like [float('nan')] if lat1 is a pole
              and infinite solutions exist for lon2 in spherical case.
    """
    from scipy.optimize import fsolve

    ellps = _normalize_ellipsoid(ellipsoid)

    if distance_miles < 0:
        raise ValueError("Distance cannot be negative.")

    if abs(distance_miles) < tol: # Effectively zero distance
        if abs(lat1_deg - lat2_target_deg) < tol:
            return [normalize_longitude(lon1_deg)]
        else:
            return [] # Cannot be at lat2_target_deg if distance is zero and lats differ

    lat1_rad = math.radians(lat1_deg)
    lon1_rad = math.radians(lon1_deg)
    lat2_target_rad = math.radians(lat2_target_deg)
    
    # Spherical calculation for initial guesses or direct spherical solution
    sigma = distance_miles / EARTH_RADIUS_MILES # Angular distance

    # Handle cases where lat1 or lat2 is a pole for spherical formula
    if abs(math.cos(lat1_rad)) < tol: # lat1 is a pole
        # All points at distance_miles are on a circle of latitude.
        # For spherical case, if lat1 is North Pole, lat2_target_rad must be pi/2 - sigma.
        # If so, any longitude is a solution. This is ill-defined for "the" longitude.
        # Ellipsoidal solver below might handle this better if pyproj is robust.
        # For now, let spherical part indicate this ambiguity for non-ellipsoidal.
        if ellps == ELLIPSOID_SPHERE:
            return [float('nan')] # Indicates infinite solutions
        # If ellipsoidal, proceed, fsolve might work or fail gracefully.

    if abs(math.cos(lat2_target_rad)) < tol: # lat2_target is a pole
        # Distance from (lat1, lon1) to this pole must be distance_miles
        _, _, dist_to_pole = inverse_geodetic(lat1_deg, lon1_deg, lat2_target_deg, lon1_deg, unit='miles', ellipsoid=ellps)
        if abs(dist_to_pole - distance_miles) < tol:
            # The point is the pole. The longitude can be considered that of the geodesic.
            # For a sphere, it's lon1_deg. For ellipsoid, direct_geodetic can find it.
            if ellps != ELLIPSOID_SPHERE:
                az_to_pole, _, _ = inverse_geodetic(lat1_deg, lon1_deg, lat2_target_deg, lon1_deg, unit='miles', ellipsoid=ellps)
                _, lon_at_pole, _ = direct_geodetic(lat1_deg, lon1_deg, az_to_pole, distance_miles, unit='miles', ellipsoid=ellps)
                return [normalize_longitude(lon_at_pole)]
            else: # Spherical
                return [normalize_longitude(lon1_deg)] # Longitude of meridian to pole
        else:
            return [] # Pole cannot be reached with this distance

    # General spherical formula for delta_lambda
    cos_lat1 = math.cos(lat1_rad)
    sin_lat1 = math.sin(lat1_rad)
    cos_lat2_target = math.cos(lat2_target_rad)
    sin_lat2_target = math.sin(lat2_target_rad)

    numerator = math.cos(sigma) - sin_lat1 * sin_lat2_target
    denominator = cos_lat1 * cos_lat2_target
    
    if abs(denominator) < tol : # Should have been caught by pole checks, but as safeguard
        return [] # Or handle as pole if appropriate

    cos_delta_lambda = numerator / denominator

    if cos_delta_lambda > 1.0 + tol or cos_delta_lambda < -1.0 - tol:
        return [] # No solution
    
    cos_delta_lambda = max(-1.0, min(1.0, cos_delta_lambda)) # Clamp for acos
    delta_lambda_rad = math.acos(cos_delta_lambda)

    solutions = []
    lon2_guess_rad_1 = lon1_rad + delta_lambda_rad
    lon2_guess_rad_2 = lon1_rad - delta_lambda_rad

    if ellps == ELLIPSOID_SPHERE: # Purely spherical solution
        solutions.append(normalize_longitude(math.degrees(lon2_guess_rad_1)))
        if abs(delta_lambda_rad) > tol and abs(delta_lambda_rad - math.pi) > tol: # Avoid duplicate for 0 or pi
            solutions.append(normalize_longitude(math.degrees(lon2_guess_rad_2)))
        return sorted(list(set(solutions))) # Unique sorted solutions

    # Ellipsoidal refinement using fsolve
    def objective_func(lon2_arr_deg, lat1_d, lon1_d, lat2_target_d, dist_miles_target, ellipsoid_setting):
        lon2_cand_deg = lon2_arr_deg[0]
        _, _, calculated_dist = inverse_geodetic(
            lat1_d, lon1_d, lat2_target_d, lon2_cand_deg, unit='miles', ellipsoid=ellipsoid_setting
        )
        return calculated_dist - dist_miles_target

    # Refine first guess
    lon2_guess_deg_1 = normalize_longitude(math.degrees(lon2_guess_rad_1))
    lon2_sol1_arr, _, ier1, _ = fsolve(
        objective_func, x0=[lon2_guess_deg_1], 
        args=(lat1_deg, lon1_deg, lat2_target_deg, distance_miles, ellps),
        full_output=True, xtol=1e-10 # Set tolerance for fsolve
    )
    if ier1 == 1: # Solution found
        # Check if the solution is valid (distance matches closely)
        final_check_dist_1 = objective_func(lon2_sol1_arr, lat1_deg, lon1_deg, lat2_target_deg, distance_miles, ellps) + distance_miles
        if abs(final_check_dist_1 - distance_miles) < distance_miles * 1e-7: # Relative tolerance for distance match
             solutions.append(normalize_longitude(lon2_sol1_arr[0]))

    # Refine second guess (if distinct)
    if abs(delta_lambda_rad) > tol and abs(delta_lambda_rad - math.pi) > tol:
        lon2_guess_deg_2 = normalize_longitude(math.degrees(lon2_guess_rad_2))

        lon2_sol2_arr, _, ier2, _ = fsolve(
            objective_func, x0=[lon2_guess_deg_2],
            args=(lat1_deg, lon1_deg, lat2_target_deg, distance_miles, ellps),
            full_output=True, xtol=1e-10
        )
        if ier2 == 1:
            final_check_dist_2 = objective_func(lon2_sol2_arr, lat1_deg, lon1_deg, lat2_target_deg, distance_miles, ellps) + distance_miles
            if abs(final_check_dist_2 - distance_miles) < distance_miles * 1e-7:
                solutions.append(normalize_longitude(lon2_sol2_arr[0]))

    return sorted(list(set(s for s in solutions if not math.isnan(s)))) # Unique, sorted, non-NaN solutions


def find_latitudes_for_known_longitude_and_distance(
                        lat1_deg: float, lon1_deg: float,
                        distance_miles: float, lon2_target_deg: float,
                        ellipsoid: Union[str, bool, None]=DEFAULT_ELLIPSOID, tol: float=1e-9
    ) -> List[float]:
    """
    Find latitude(s) lat2 such that distance(P1, P2)=distance and lon2=lon2_target.
    """
    ellps = _normalize_ellipsoid(ellipsoid)
    def obj(lat2_param) -> float: # lat2_param is a 1-element numpy array from fsolve
        # Extract the scalar float value from the numpy array passed by fsolve
        current_lat2_deg = float(lat2_param[0])
        _, _, d = inverse_geodetic(lat1_deg, lon1_deg, current_lat2_deg,
                                   lon2_target_deg, unit='miles', ellipsoid=ellps)
        return d - distance_miles
    # initial guesses (approx deg lat per mile)
    delta_deg = distance_miles / 69.0
    guesses = [lat1_deg + delta_deg, lat1_deg - delta_deg]
    sols = set()
    for g in guesses:
        try:
            sol = fsolve_root(obj, g, xtol=tol)
            if -90 <= sol <= 90:
                sols.add(round(sol, 10))
        except ValueError:
            continue
    return sorted(sols)

# ----------------------------------------
# Diamond Region Functions
# ----------------------------------------
# Taken from washdc_code/diamond.py
# Consolidated functions for computing diamond-shaped regions on a sphere:
#   - solve_diamond_approx: approximate half-extents from area
#   - solve_diamond_exact: numerically solve half-extents for exact area
#   - diamond_area_spherical: compute exact area via two spherical triangles
#   - diamond_corners: corner coordinates in degrees

# Also includes supporting spherical-triangle-area and vector utilities.

def solve_diamond_approx(radius, area, phi_c_rad):
    """
    Approximate half-extents (dphi, dlam) in radians for a diamond region of given area.
    Assumes small region and uses planar approximation adjusted by cos(phi).
    """
    cos_phi = math.cos(phi_c_rad)
    dlam = math.sqrt(area / (2 * radius * radius * cos_phi * cos_phi))
    dphi = cos_phi * dlam
    return dphi, dlam

def diamond_area_spherical(radius, phi_c_rad, dlam, lam_c_rad=0.0):
    """
    Exact area of diamond centered at (phi_c_rad, lam_c_rad) with half-extent dlam (radians).
    Splits region into two spherical triangles.
    """
    dphi = math.cos(phi_c_rad) * dlam
    N = (phi_c_rad + dphi, lam_c_rad)
    S = (phi_c_rad - dphi, lam_c_rad)
    E = (phi_c_rad, lam_c_rad + dlam)
    W = (phi_c_rad, lam_c_rad - dlam)
    area1 = spherical_triangle_area(radius, N, E, S)
    area2 = spherical_triangle_area(radius, N, S, W)
    return area1 + area2

def solve_diamond_exact(radius, area, phi_c_rad, lam_c_rad=0.0, tol=1e-12):
    """
    Numerically solve for half-extents (dphi, dlam) so that diamond_area_spherical = area.
    Uses binary search on dlam.
    """
    def f(dl): return diamond_area_spherical(radius, phi_c_rad, dl, lam_c_rad) - area
    lo, hi = 0.0, math.pi
    if f(hi) < 0:
        raise ValueError("Requested area exceeds hemisphere")
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if f(mid) > 0:
            hi = mid
        else:
            lo = mid
    dlam = 0.5 * (lo + hi)
    dphi = math.cos(phi_c_rad) * dlam
    return dphi, dlam

def diamond_corners(phi_c_deg, lam_c_deg, dphi, dlam):
    """
    Return geographic corners of the diamond as list of (lat_deg, lon_deg):
    North, East, South, West.
    """
    return [
        (phi_c_deg + math.degrees(dphi), lam_c_deg),
        (phi_c_deg, lam_c_deg + math.degrees(dlam)),
        (phi_c_deg - math.degrees(dphi), lam_c_deg),
        (phi_c_deg, lam_c_deg - math.degrees(dlam))
    ]

#===============================================================================
# I/O Utilities
#===============================================================================

def read_geojson(path):
    if isinstance(path, dict): return path
    return gpd.read_file(path) if gpd else json.load(open(path))

def read_raster(path):
    if not rasterio: raise RuntimeError('rasterio needed')
    return rasterio.open(path)

def mask_raster_dataset(dataset, shapes, crop=True):
    if not rasterio: raise RuntimeError('rasterio needed')
    return rio_mask(dataset, shapes, crop=crop)

def geojson_to_kml(geoobj, kml_path, color_field=None, default_color='ff0000ff'):
    if not simplekml: raise RuntimeError('simplekml needed')
    geo = read_geojson(geoobj) if isinstance(geoobj, str) else geoobj
    kml = simplekml.Kml()
    for feat in geo.get('features', []):
        shp = shape(feat['geometry'])
        if shp.geom_type == 'Polygon':
            obj = kml.newpolygon()
            obj.outerboundaryis = list(shp.exterior.coords)
        elif shp.geom_type == 'LineString':
            obj = kml.newlinestring()
            obj.coords = list(shp.coords)
        else:
            continue
        obj.style.linestyle.color = default_color
    Path(kml_path).parent.mkdir(parents=True, exist_ok=True)
    kml.save(kml_path)

def print_triangles_csv(locs, triad_names):
    """
    Given locs dict mapping names to (lat, lon) and a list of triads,
    prints CSV lines: 'A','B','C', angleA,angleB,angleC, sideAB,sideBC,sideCA
    """
    for (A, B, C) in triad_names:
        props = spherical_triangle_properties(locs[A], locs[B], locs[C])
        angles = props['angles']
        sides = props['sides']
        row = f"'{A}','{B}','{C}',{angles['A']:.3f},{angles['B']:.3f},{angles['C']:.3f},"
        row += f"{sides['AB']:.8f},{sides['BC']:.8f},{sides['CA']:.8f}"
        print(row)

#TO DO CLI
def load_coords_from_csv(filepath, coords_dict):
    """
    Loads LOC, LAT, LON from a CSV file into a dictionary.
    The dictionary is updated in place.
    Assumes header row with 'LOC', 'LAT', 'LON' columns.
    """
    try:
        with open(filepath, 'r', newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            try:
                header = next(reader)
            except StopIteration:
                print(f"Warning: File {filepath} is empty or has no header.")
                return

            try:
                loc_col_idx = header.index('LOC')
                lat_col_idx = header.index('LAT')
                lon_col_idx = header.index('LON')
            except ValueError as e:
                print(f"Warning: Missing expected column in {filepath}. Error: {e}. Skipping this file.")
                return

            for row_num, row in enumerate(reader, start=2): # start=2 for 1-based data row numbering
                if not row or len(row) <= max(loc_col_idx, lat_col_idx, lon_col_idx):
                    print(f"Warning: Skipping malformed or short row {row_num} in {filepath}: {row}")
                    continue
                try:
                    loc = row[loc_col_idx]
                    lat = row[lat_col_idx]
                    lon = row[lon_col_idx]
                    coords_dict[loc] = (lat, lon)
                except IndexError:
                    print(f"Warning: Skipping row {row_num} in {filepath} due to insufficient columns: {row}")
    except FileNotFoundError:
        print(f"Warning: Data file not found: {filepath}. It will be skipped.")
    except Exception as e:
        print(f"An unexpected error occurred while reading {filepath}: {e}")
        
#===============================================================================
# Spiral Generators & Plotters
#===============================================================================

def walk_angles(init_lat: float, init_lon: float,
                init_bearing: float=0.0, num_turns: int=4,
                turn_angle: float=90.0, walk_dist: float=10.0,
                change_rate: float=1.0, ellipsoid: Union[str, bool, None]=DEFAULT_ELLIPSOID
    ) -> List[Tuple[float,float]]:
    """
    Params
    Generates a sequence of points by "walking" from a starting point.
    Each step involves moving a certain distance along a bearing, then turning.
    The distance can change with each step.

    Args:
        init_lat (float): Starting latitude in degrees.
        init_lon (float): Starting longitude in degrees.
        init_bearing (float, optional): Initial bearing in degrees clockwise from North. Defaults to 0.0.
        num_turns (int, optional): Number of legs/steps to take. Defaults to 4.
        turn_angle (float, optional): Angle to turn at each step, in degrees.
                                      Positive for clockwise. Defaults to 90.0.
        walk_dist (float, optional): Initial distance for the first leg, in miles. Defaults to 10.0.
        change_rate (float, optional): Factor by which walk_dist changes for subsequent legs.
                                       1.0 means constant distance. Defaults to 1.0.
        ellipsoid (str|bool|None, optional): WGS84 (default), NAD83, or sphere.

    Returns:
        points: list a sequence of generated (lat, lon) walk-points
    """
    pts = [(init_lat, init_lon)]
    lat, lon, bearing = init_lat, init_lon, init_bearing
    current_walk_dist = walk_dist
    for _ in range(num_turns):
        lat, lon, fwd_az = direct_geodetic(lat, lon, bearing, current_walk_dist, unit='miles', ellipsoid=ellipsoid)
        pts.append((lat, lon))
        bearing = (fwd_az + turn_angle) % 360
        current_walk_dist *= change_rate
    return pts

def golden_spiral_in(lat0: float, lon0: float,
                           initial_bearing: float, base_dist: float,
                           legs: int, phi: float=(1+math.sqrt(5))/2,
                           ccw: bool=False, ellipsoid: Union[str, bool, None]=DEFAULT_ELLIPSOID
    ) -> List[Tuple[float,float]]:
    """
    Generate points of a right-angle golden-ratio spiral.
    Spiral is INward: sides get progressively *smaller* from base_dist.
    Turns are clockwise by default; or, set --ccw True.
    Returns list of (lat, lon) including starting point.
    """
    pts = [(lat0, lon0)]
    lat, lon, brng = lat0, lon0, initial_bearing
    dist = base_dist
    for _ in range(legs):
        lat, lon, fwd = direct_geodetic(lat, lon, brng, dist,
                                        unit='miles', ellipsoid=ellipsoid)
        pts.append((lat, lon))
        brng = (fwd + (90 if ccw else -90)) % 360
        dist /= phi
    return pts

# TO DO Combine -in/-out as an option for one param: gold-spiral --in default: out
def golden_spiral_out(  lat0: float, lon0: float,
                        initial_bearing: float, base_dist: float,
                        legs: int, phi: float=(1+math.sqrt(5))/2,
                        ccw: bool=False, ellipsoid: Union[str, bool, None]=DEFAULT_ELLIPSOID
    ) -> List[Tuple[float,float]]:
    """
    Generate points of a right-angle golden-ratio spiral.
    Spiral is OUTward: sides get progressively *larger* from base_dist.
    Turns are clockwise by default; or, set --ccw True.
    Returns list of #{legs} (lat, lon) points (includes start).
    """
    pts = [(lat0, lon0)]
    lat, lon, brng = lat0, lon0, initial_bearing
    dist = base_dist
    for _ in range(legs):
        lat, lon, fwd = direct_geodetic(lat, lon, brng, dist,
                                        unit='miles', ellipsoid=ellipsoid)
        pts.append((lat, lon))
        brng = (fwd + (90 if ccw else -90)) % 360
        dist *= phi
    return pts

def golden_spiral_plot_in(pts: List[Tuple[float,float]],
                       projection: str='plate', pad: float=0.01) -> None:
    """
    Plot a golden spiral given list of (lat, lon) points.
    Uses cartopy if available, else matplotlib.
    """
    try:
        import matplotlib.pyplot as plt
        import cartopy.crs as ccrs
        import cartopy.feature as cfeature
        use_cartopy = True
    except ImportError:
        import matplotlib.pyplot as plt
        use_cartopy = False
    lats, lons = zip(*pts)
    if use_cartopy:
        # setup projection
        if projection == 'ortho':
            proj = ccrs.Orthographic(central_longitude=lons[0], central_latitude=lats[0])
        else:
            proj = ccrs.PlateCarree()
        fig = plt.figure(figsize=(6,6))
        ax = fig.add_subplot(1,1,1, projection=proj)
        ax.add_feature(cfeature.LAND.with_scale('10m'), facecolor='lightgray')
        ax.add_feature(cfeature.OCEAN.with_scale('10m'), facecolor='azure')
        gl = ax.gridlines(draw_labels=True, dms=True)
        gl.top_labels = gl.right_labels = False
        if projection == 'plate':
            ax.set_extent([min(lons)-pad, max(lons)+pad,
                           min(lats)-pad, max(lats)+pad],
                          crs=ccrs.PlateCarree())
        ax.plot(lons, lats, '-o', transform=ccrs.Geodetic())
        for i,(lat,lon) in enumerate(pts):
            ax.text(lon, lat, str(i), transform=ccrs.PlateCarree(), fontsize=9)
        plt.title(f'Golden Spiral ({len(pts)-1} legs)')
        plt.show()
    else:
        plt.figure(figsize=(6,6))
        plt.plot(lons, lats, '-o')
        for i,(lat,lon) in enumerate(pts):
            plt.text(lon, lat, str(i), fontsize=9, ha='right', va='bottom')
        plt.grid(True)
        plt.gca().set_aspect('equal', 'box')
        plt.xlabel('Longitude')
        plt.ylabel('Latitude')
        padg = pad
        plt.xlim(min(lons)-padg, max(lons)+padg)
        plt.ylim(min(lats)-padg, max(lats)+padg)
        plt.title(f'Golden Spiral ({len(pts)-1} legs)')
        plt.show()

#===============================================================================
# Placeholders for Street & Highpoint:
# TODO: Integrate functions from streets.py (GPD operations, plotting)
# TODO: Integrate functions from highpoints.py (DEM masking, peak extraction)

# Sailpath utilities
# ------------------------------------------------------------

def sail_to_target(lat0: float, lon0: float, lat1: float, lon1: float,
                   step_dist_miles: float, ellipsoid: str = DEFAULT_ELLIPSOID) -> List[Tuple[float, float, float]]:
    """
    Walk from (lat0, lon0) to (lat1, lon1) by fixed steps along the current azimuth.
    Returns list of (lat, lon, az_used_at_point). The final endpoint is included once.
    """
    ellps = _normalize_ellipsoid(ellipsoid)
    if step_dist_miles <= 0:
        raise ValueError('step_dist_miles must be positive.')

    path: List[Tuple[float, float, float]] = []
    cur_lat, cur_lon = lat0, lon0

    while True:
        fwd_az, _, dist_miles = inverse_geodetic(cur_lat, cur_lon, lat1, lon1, unit='miles', ellipsoid=ellps)
        if dist_miles <= step_dist_miles:
            path.append((lat1, lon1, float(fwd_az)))
            break
        path.append((cur_lat, cur_lon, float(fwd_az)))
        next_lat, next_lon, _ = direct_geodetic(cur_lat, cur_lon, fwd_az, step_dist_miles, unit='miles', ellipsoid=ellps)
        cur_lat, cur_lon = next_lat, next_lon

    return path


@dataclass
class StepRec:
    lat: float
    lon: float
    az: float
    step: int
    dist_to_target_miles: float
    bin_name: str = ''


@dataclass
class Leg:
    from_loc: str
    to_loc: str
    target_lat: float
    target_lon: float
    points: List[StepRec] = field(default_factory=list)

    @property
    def folder_name(self) -> str:
        return f"{self.from_loc}->{self.to_loc}"


# ------------------------------------------------------------
# CSV helpers
# ------------------------------------------------------------

def _as_float(field_name: str, s: str) -> float:
    try:
        return float(s)
    except Exception as e:
        raise ValueError(f"{field_name}='{s}' is not a float") from e


def _print_path_rows(path: Iterable[Tuple[float, float, float]],
                     from_loc: str, to_loc: str, label_fmt: str = "{from_loc} {to_loc} {step}",
                     start_step: int = 0) -> None:
    """Emits rows (no Step column). Label holds the step number."""
    for i, (lat, lon, az) in enumerate(path):
        label = label_fmt.format(from_loc=from_loc, to_loc=to_loc, step=start_step + i)
        print(f"{label}, {lat:.9f}, {lon:.9f}, {az:.4f}")


def _collect_leg(path: List[Tuple[float, float, float]],
                 from_loc: str, to_loc: str,
                 target_lat: float, target_lon: float,
                 ellipsoid: str,
                 bin_func, is_forward: bool) -> Leg:
    leg = Leg(from_loc=from_loc, to_loc=to_loc, target_lat=target_lat, target_lon=target_lon)
    for i, (lat, lon, az) in enumerate(path):
        _, _, dist_miles = inverse_geodetic(lat, lon, target_lat, target_lon, unit='miles', ellipsoid=ellipsoid)
        leg.points.append(StepRec(
            lat=lat, lon=lon, az=float(az), step=i,
            dist_to_target_miles=dist_miles,
            bin_name=bin_func(float(az), is_forward)
        ))
    return leg


# ------------------------------------------------------------
# Color & icons
# ------------------------------------------------------------

def _rgb_to_kml_abgr_hex(r: int, g: int, b: int, a: int = 255) -> str:
    """KML wants aabbggrr."""
    r = max(0, min(255, r))
    g = max(0, min(255, g))
    b = max(0, min(255, b))
    a = max(0, min(255, a))
    return f"{a:02x}{b:02x}{g:02x}{r:02x}"


def _hsv_to_rgb(h: float, s: float, v: float) -> Tuple[int, int, int]:
    h = h % 360.0
    c = v * s
    x = c * (1 - abs(((h / 60.0) % 2) - 1))
    m = v - c
    if 0 <= h < 60:
        rp, gp, bp = c, x, 0
    elif 60 <= h < 120:
        rp, gp, bp = x, c, 0
    elif 120 <= h < 180:
        rp, gp, bp = 0, c, x
    elif 180 <= h < 240:
        rp, gp, bp = 0, x, c
    elif 240 <= h < 300:
        rp, gp, bp = x, 0, c
    else:
        rp, gp, bp = c, 0, x
    r = int(round((rp + m) * 255))
    g = int(round((gp + m) * 255))
    b = int(round((bp + m) * 255))
    return r, g, b


# Custom 12-bin scheme with two-name diagonals and 'oob' fallback
# Centers (deg) and names:
#  - Cardinals: SN@0, WE@90, NS@180, EW@270  (15 deg window)
#  - Diagonals (15 deg window around centers), with forward/reverse name pairs:
#      45:  WN / SE
#      135: NE / WS
#      225: NW / ES
#      315: EN / SW

def _make_bin12(cardinal_halfwidth_deg: float = 15.0):
    cw = float(cardinal_halfwidth_deg)

    def within(center: float, az: float) -> bool:
        lo = (center - cw) % 360.0
        hi = (center + cw) % 360.0
        if lo < hi:
            return lo <= az < hi
        else:  # wrap-around
            return az >= lo or az < hi

    # Diagonal centers with (forward_name, reverse_name)
    diag_centers = [
        (45.0,  ("WN", "SE")),
        (135.0, ("NE", "WS")),
        (225.0, ("NW", "ES")),
        (315.0, ("EN", "SW")),
    ]

    # Cardinal mapping: unique names per window
    cardinal_map = {
        0.0:   "SN",
        90.0:  "WE",
        180.0: "NS",
        270.0: "EW",
    }

    def bin_func(az_deg: float, is_forward_leg: bool) -> str:
        az = (az_deg % 360.0 + 360.0) % 360.0

        # Cardinals first
        for center, name in cardinal_map.items():
            if within(center, az):
                return name

        # Diagonals
        for center, (name_fwd, name_rev) in diag_centers:
            if within(center, az):
                return name_fwd if is_forward_leg else name_rev

        # Out-of-bin (explicit): pure white style later
        return "oob"

    # Assign hues to the 12 named bins (excluding 'oob')
    ordered_bins = ["SN", "WE", "NS", "EW", "WN", "SE", "NE", "WS", "NW", "ES", "EN", "SW"]
    hue_map: Dict[str, float] = {name: (i / len(ordered_bins)) * 360.0
                                 for i, name in enumerate(ordered_bins)}

    def color_func(bin_name: str, step_idx: int, step_max: int) -> str:
        if bin_name == "oob":
            # Pure white, fully opaque
            return _rgb_to_kml_abgr_hex(255, 255, 255, a=255)
        hue = hue_map.get(bin_name, 0.0)
        sat = 0.95
        t = (step_idx / float(step_max)) if step_max > 0 else 0.0
        val = 0.95 - 0.25 * t  # subtle darken with step
        r, g, b = _hsv_to_rgb(hue, sat, val)
        return _rgb_to_kml_abgr_hex(r, g, b, a=255)

    return bin_func, color_func


# Known-good shape icons (no pushpins/paddles)
_ICON_URLS = [
    "http://maps.google.com/mapfiles/kml/shapes/star.png",
    "http://maps.google.com/mapfiles/kml/shapes/triangle.png",
    "http://maps.google.com/mapfiles/kml/shapes/cross-hairs.png",
    "http://maps.google.com/mapfiles/kml/shapes/target.png",
    "http://maps.google.com/mapfiles/kml/shapes/open-diamond.png",
    "http://maps.google.com/mapfiles/kml/shapes/shaded_dot.png",
    "http://maps.google.com/mapfiles/kml/shapes/donut.png",
    "http://maps.google.com/mapfiles/kml/shapes/square.png",
    "http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png",
    "http://maps.google.com/mapfiles/kml/shapes/placemark_square.png",
    "http://maps.google.com/mapfiles/kml/shapes/polygon.png",
    "http://maps.google.com/mapfiles/kml/paddle/ltblu-blank.png",
    "http://maps.google.com/mapfiles/kml/paddle/grn-blank.png",
    "http://maps.google.com/mapfiles/kml/paddle/wht-blank.png",
    "http://maps.google.com/mapfiles/kml/paddle/pink-blank.png",
    "http://maps.google.com/mapfiles/kml/paddle/blu-blank.png",
]


# ------------------------------------------------------------
# KML builder
# ------------------------------------------------------------

def _build_kml(legs: List[Leg], output_path: str,
               pointer_scale: float = 0.05,
               pointer_min_miles: float = 100.0 / METERS_PER_MILE,
               pointer_max_miles: float = 300.0 / METERS_PER_MILE,
               ellipsoid: str = DEFAULT_ELLIPSOID,
               cardinal_halfwidth_deg: float = 15.0,
               draw_gridlines: bool = True) -> None:
    import xml.etree.ElementTree as ET

    ellps = _normalize_ellipsoid(ellipsoid)
    ns = "http://www.opengis.net/kml/2.2"
    ET.register_namespace("", ns)
    kml = ET.Element(ET.QName(ns, "kml"))
    doc = ET.SubElement(kml, ET.QName(ns, "Document"))

    bin_func, color_func = _make_bin12(cardinal_halfwidth_deg)

    # ---- Top-level gridlines folder (one line per entered LOC0->LOC1) ----
    if draw_gridlines and legs:
        grid_folder = ET.SubElement(doc, ET.QName(ns, "Folder"))
        ET.SubElement(grid_folder, ET.QName(ns, "name")).text = "gridlines"

        # Shared style: white @ 80% opacity, width 2.0
        grid_style = ET.SubElement(doc, ET.QName(ns, "Style"), {"id": "gridline_style"})
        ls = ET.SubElement(grid_style, ET.QName(ns, "LineStyle"))
        ET.SubElement(ls, ET.QName(ns, "color")).text = "ccffffff"
        ET.SubElement(ls, ET.QName(ns, "width")).text = "2.0"

        seen = set()  # avoid duplicates (skip reverse)
        for leg in legs:
            key = (leg.from_loc, leg.to_loc)
            rkey = (leg.to_loc, leg.from_loc)
            if key in seen or rkey in seen:
                continue
            if not leg.points:
                continue
            start_lat = leg.points[0].lat
            start_lon = leg.points[0].lon
            end_lat = leg.target_lat
            end_lon = leg.target_lon

            pm = ET.SubElement(grid_folder, ET.QName(ns, "Placemark"))
            ET.SubElement(pm, ET.QName(ns, "name")).text = f"{leg.from_loc}->{leg.to_loc} gridline"
            ET.SubElement(pm, ET.QName(ns, "styleUrl")).text = "#gridline_style"
            line = ET.SubElement(pm, ET.QName(ns, "LineString"))
            ET.SubElement(line, ET.QName(ns, "tessellate")).text = "1"
            coords = ET.SubElement(line, ET.QName(ns, "coordinates"))
            coords.text = f"{start_lon:.9f},{start_lat:.9f},0 {end_lon:.9f},{end_lat:.9f},0"

            seen.add(key)

    # ---- Per-leg folders with points + pointers ----
    for leg_idx, leg in enumerate(legs):
        if not leg.points:
            continue

        folder = ET.SubElement(doc, ET.QName(ns, "Folder"))
        ET.SubElement(folder, ET.QName(ns, "name")).text = leg.folder_name

        step_max = max(p.step for p in leg.points)

        for i, pt in enumerate(leg.points):
            # Color via bin
            color_hex = color_func(pt.bin_name, pt.step, step_max)
            icon_href = _ICON_URLS[i % len(_ICON_URLS)]

            # Unique style per point
            style_id = f"s_{leg_idx}_{i}"
            style = ET.SubElement(doc, ET.QName(ns, "Style"), {"id": style_id})

            icon_style = ET.SubElement(style, ET.QName(ns, "IconStyle"))
            ET.SubElement(icon_style, ET.QName(ns, "color")).text = color_hex
            ET.SubElement(icon_style, ET.QName(ns, "scale")).text = "0.75"
            icon = ET.SubElement(icon_style, ET.QName(ns, "Icon"))
            ET.SubElement(icon, ET.QName(ns, "href")).text = icon_href

            line_style = ET.SubElement(style, ET.QName(ns, "LineStyle"))
            ET.SubElement(line_style, ET.QName(ns, "color")).text = color_hex
            ET.SubElement(line_style, ET.QName(ns, "width")).text = "2.0"

            # Labels at 80% opacity
            label_style = ET.SubElement(style, ET.QName(ns, "LabelStyle"))
            ET.SubElement(label_style, ET.QName(ns, "scale")).text = "0.75"
            ET.SubElement(label_style, ET.QName(ns, "color")).text = _rgb_to_kml_abgr_hex(255, 255, 255, a=155)

            # Placemark (point)
            pm_point = ET.SubElement(folder, ET.QName(ns, "Placemark"))
            ET.SubElement(pm_point, ET.QName(ns, "name")).text = f"{leg.from_loc} {leg.to_loc} {pt.step}"
            ET.SubElement(pm_point, ET.QName(ns, "styleUrl")).text = f"#{style_id}"
            ext = ET.SubElement(pm_point, ET.QName(ns, "ExtendedData"))
            for k, v in {
                "bin": pt.bin_name,
                "azimuth_deg": f"{pt.az:.4f}",
                "dist_to_target_miles": f"{pt.dist_to_target_miles:.6f}",
            }.items():
                data = ET.SubElement(ext, ET.QName(ns, "Data"), {"name": k})
                ET.SubElement(data, ET.QName(ns, "value")).text = v

            point = ET.SubElement(pm_point, ET.QName(ns, "Point"))
            coords = ET.SubElement(point, ET.QName(ns, "coordinates"))
            coords.text = f"{pt.lon:.9f},{pt.lat:.9f},0"

            # Pointer line (skip at target)
            if pt.dist_to_target_miles > 0.0003:
                length_miles = max(pointer_min_miles, min(pointer_max_miles, pt.dist_to_target_miles * float(pointer_scale)))
                end_lat, end_lon, _ = direct_geodetic(pt.lat, pt.lon, pt.az, length_miles, unit='miles', ellipsoid=ellps)

                pm_line = ET.SubElement(folder, ET.QName(ns, "Placemark"))
                ET.SubElement(pm_line, ET.QName(ns, "name")).text = f"{leg.from_loc} {leg.to_loc} {pt.step} pointer"
                ET.SubElement(pm_line, ET.QName(ns, "styleUrl")).text = f"#{style_id}"
                line = ET.SubElement(pm_line, ET.QName(ns, "LineString"))
                ET.SubElement(line, ET.QName(ns, "tessellate")).text = "1"
                coords2 = ET.SubElement(line, ET.QName(ns, "coordinates"))
                coords2.text = f"{pt.lon:.9f},{pt.lat:.9f},0 {end_lon:.9f},{end_lat:.9f},0"

    tree = ET.ElementTree(kml)
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    tree.write(output_path, encoding='utf-8', xml_declaration=True)


# ------------------------------------------------------------
# Sailpath orchestration
# ------------------------------------------------------------

def _process_one_run(loc0: str, lat0: float, lon0: float,
                     loc1: str, lat1: float, lon1: float,
                     step_dist_miles: float, ellipsoid: str,
                     one_way: bool, label_fmt: str,
                     legs_out: Optional[List[Leg]] = None,
                     cardinal_halfwidth_deg: float = 15.0) -> None:
    bin_func, _ = _make_bin12(cardinal_halfwidth_deg)

    # Forward leg
    forward = sail_to_target(lat0, lon0, lat1, lon1, step_dist_miles, ellipsoid=ellipsoid)
    _print_path_rows(forward, loc0, loc1, label_fmt=label_fmt)
    if legs_out is not None:
        legs_out.append(_collect_leg(forward, loc0, loc1, lat1, lon1, ellipsoid, bin_func, is_forward=True))

    # Return leg
    if not one_way:
        backward = sail_to_target(lat1, lon1, lat0, lon0, step_dist_miles, ellipsoid=ellipsoid)
        _print_path_rows(backward, loc1, loc0, label_fmt=label_fmt)
        if legs_out is not None:
            legs_out.append(_collect_leg(backward, loc1, loc0, lat0, lon0, ellipsoid, bin_func, is_forward=False))


def _process_csv(path: str, ellipsoid: str, one_way: bool, label_fmt: str,
                 kml_out_path: Optional[str],
                 step_unit: str,
                 pointer_scale: float, pointer_min_miles: float, pointer_max_miles: float,
                 cardinal_halfwidth_deg: float,
                 draw_gridlines: bool) -> None:
    legs: List[Leg] = [] if kml_out_path else None

    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        required = ["LOC0", "LAT0", "LON0", "LOC1", "LAT1", "LON1", "STEP_DIST"]
        missing = [h for h in required if h not in reader.fieldnames]
        if missing:
            raise ValueError(f"Input CSV is missing required header(s): {', '.join(missing)}")

        print("Label, Latitude, Longitude, Azimuth_To_Target")
        for rownum, row in enumerate(reader, start=2):
            try:
                loc0 = (row["LOC0"] or "").strip()
                loc1 = (row["LOC1"] or "").strip()
                lat0 = _as_float("LAT0", row["LAT0"])
                lon0 = _as_float("LON0", row["LON0"])
                lat1 = _as_float("LAT1", row["LAT1"])
                lon1 = _as_float("LON1", row["LON1"])
                step_dist_raw = _as_float("STEP_DIST", row["STEP_DIST"])
                step_dist_miles = _to_miles(step_dist_raw, step_unit)
            except Exception as e:
                print(f"# Skipping row {rownum}: {e}", file=sys.stderr)
                continue

            _process_one_run(loc0, lat0, lon0, loc1, lat1, lon1,
                             step_dist_miles, ellipsoid, one_way, label_fmt,
                             legs_out=legs, cardinal_halfwidth_deg=cardinal_halfwidth_deg)

    if kml_out_path and legs:
        _build_kml(
            legs, kml_out_path, ellipsoid=ellipsoid,
            pointer_scale=pointer_scale, pointer_min_miles=pointer_min_miles, pointer_max_miles=pointer_max_miles,
            cardinal_halfwidth_deg=cardinal_halfwidth_deg,
            draw_gridlines=draw_gridlines
        )


# ------------------------------------------------------------
# Altitude utilities
# ------------------------------------------------------------

METERS_TO_FEET = 3.28084


def _require_rasterio():
    try:
        import rasterio
        import rasterio.warp
        import rasterio.crs
    except ImportError as exc:
        raise RuntimeError('rasterio is required for altitude sampling.') from exc
    return rasterio


def get_altitudes_for_points(csv_path: str, dem_path: str, input_points_epsg: int = 4326) -> List[Dict[str, float]]:
    """
    Reads points from a CSV, queries their elevations from a DEM,
    converts elevations to feet, and returns a list of dictionaries.
    """
    rasterio = _require_rasterio()

    input_lon_lat_coords = []  # (lon, lat)
    initial_point_data = []    # LOC, LAT, LON

    try:
        with open(csv_path, 'r', newline='') as csvfile:
            reader = csv.DictReader(csvfile)
            if not reader.fieldnames or not all(col in reader.fieldnames for col in ['LOC', 'LAT', 'LON']):
                print(f"Error: CSV file must contain 'LOC', 'LAT', 'LON' columns. Found: {reader.fieldnames}")
                return []

            for row_num, row in enumerate(reader, 1):
                try:
                    loc = row['LOC']
                    lat = float(row['LAT'])
                    lon = float(row['LON'])

                    input_lon_lat_coords.append((lon, lat))
                    initial_point_data.append({'LOC': loc, 'LAT': lat, 'LON': lon, 'OriginalRow': row_num})
                except (KeyError, ValueError) as e:
                    print(f"Skipping row {row_num} due to error: {row} - {e}")
                    continue
    except FileNotFoundError:
        print(f"Error: CSV file not found at {csv_path}")
        return []
    except Exception as e:
        print(f"An unexpected error occurred while reading {csv_path}: {e}")
        return []

    if not input_lon_lat_coords:
        print('No valid points found or read from CSV file.')
        return []

    processed_points_with_alt = []

    try:
        with rasterio.open(dem_path) as src_dem:
            points_crs_input = rasterio.crs.CRS.from_epsg(input_points_epsg)
            coords_for_sampling = input_lon_lat_coords

            if src_dem.crs != points_crs_input:
                lons = [p[0] for p in input_lon_lat_coords]
                lats = [p[1] for p in input_lon_lat_coords]

                transformed_coords = rasterio.warp.transform(
                    points_crs_input,
                    src_dem.crs,
                    lons,
                    lats
                )
                coords_for_sampling = list(zip(transformed_coords[0], transformed_coords[1]))

            sampled_elevations_m_raw = src_dem.sample(coords_for_sampling)

            for i, base_data in enumerate(initial_point_data):
                elevation_m_array = next(sampled_elevations_m_raw)
                elevation_m = elevation_m_array[0]

                alt_ft = None
                valid_elevation_m = None

                if src_dem.nodata is not None and elevation_m == src_dem.nodata:
                    print(f"Warning: Point {base_data['LOC']} (Lat: {base_data['LAT']}, Lon: {base_data['LON']}) is on a NoData pixel or outside DEM extent. Elevation set to N/A.")
                elif elevation_m is None or elevation_m < -10000:
                    print(f"Warning: Point {base_data['LOC']} (Lat: {base_data['LAT']}, Lon: {base_data['LON']}) has an invalid elevation value ({elevation_m}). Elevation set to N/A.")
                else:
                    try:
                        valid_elevation_m = float(elevation_m)
                        alt_ft = valid_elevation_m * METERS_TO_FEET
                    except (ValueError, TypeError):
                        print(f"Warning: Point {base_data['LOC']} (Lat: {base_data['LAT']}, Lon: {base_data['LON']}) has a non-numeric elevation value ({elevation_m}). Elevation set to N/A.")

                processed_points_with_alt.append({
                    **base_data,
                    'ALT_M': valid_elevation_m,
                    'ALT_FT': alt_ft
                })

    except FileNotFoundError:
        print(f"Error: DEM file not found at {dem_path}")
        return []
    except rasterio.errors.RasterioIOError as e:
        print(f"Error opening or reading DEM file {dem_path}: {e}")
        return []
    except Exception as e:
        print(f"An unexpected error occurred during DEM processing: {e}")
        return []

    return processed_points_with_alt


def _write_geojson(points: List[Dict[str, float]], output_path: str) -> None:
    features = []
    for point in points:
        properties = {
            'LOC': point['LOC'],
            'LAT_Orig': point['LAT'],
            'LON_Orig': point['LON'],
            'ALT_M': round(point['ALT_M'], 3) if point['ALT_M'] is not None else None,
            'ALT_FT': round(point['ALT_FT'], 3) if point['ALT_FT'] is not None else None,
        }
        feature = {
            'type': 'Feature',
            'geometry': {
                'type': 'Point',
                'coordinates': [point['LON'], point['LAT']]
            },
            'properties': properties,
        }
        features.append(feature)

    feature_collection = {'type': 'FeatureCollection', 'features': features}

    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(feature_collection, f, indent=2)


@dataclass(frozen=True)
class StreetMeasureSpec:
    mode: str
    crs: Optional[CRS]
    geod: Optional[Geod]
    coord_unit_to_meters: float = 1.0


def _require_street_dependencies() -> None:
    missing = []
    if gpd is None:
        missing.append('geopandas')
    if pd is None:
        missing.append('pandas')
    if LineString is None or Point is None or nearest_points is None:
        missing.append('shapely')
    if missing:
        raise RuntimeError(
            "The streets centerlines command requires: "
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


def _normalize_street_ellipsoid(value: Optional[str]) -> str:
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


def _parse_street_ellipsoid_arg(value: str) -> str:
    try:
        return _normalize_street_ellipsoid(value)
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


def _build_street_measure_spec(crs_like: Optional[Any], ellipsoid: str) -> StreetMeasureSpec:
    crs = CRS.from_user_input(crs_like) if crs_like else None
    if crs is not None:
        if crs.is_projected:
            return StreetMeasureSpec(
                mode='projected',
                crs=crs,
                geod=None,
                coord_unit_to_meters=_crs_linear_unit_to_meters(crs),
            )
        if crs.is_geographic:
            return StreetMeasureSpec(
                mode='geodesic',
                crs=crs,
                geod=_get_geod_from_crs(crs),
                coord_unit_to_meters=1.0,
            )
        raise ValueError(f"Unsupported input CRS for street analysis: {crs!s}")

    ell = _normalize_street_ellipsoid(ellipsoid)
    if ell == 'pseudomerc':
        return StreetMeasureSpec(
            mode='projected',
            crs=CRS.from_epsg(3857),
            geod=None,
            coord_unit_to_meters=1.0,
        )
    if ell == 'none':
        return StreetMeasureSpec(mode='sphere', crs=None, geod=None, coord_unit_to_meters=1.0)
    return StreetMeasureSpec(
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


def _coerce_street_text(value: Any) -> str:
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


def _planar_segment_length_meters(x1: float, y1: float, x2: float, y2: float, spec: StreetMeasureSpec) -> float:
    return math.hypot(x2 - x1, y2 - y1) * spec.coord_unit_to_meters


def _point_distance_meters(x1: float, y1: float, x2: float, y2: float, spec: StreetMeasureSpec) -> float:
    if spec.mode == 'projected':
        return _planar_segment_length_meters(x1, y1, x2, y2, spec)
    if spec.mode == 'geodesic':
        _, _, dist_m = spec.geod.inv(x1, y1, x2, y2)
        return float(dist_m)
    return haversine(y1, x1, y2, x2, unit='m')


def _segment_bearing_degrees(x1: float, y1: float, x2: float, y2: float, spec: StreetMeasureSpec) -> float:
    if spec.mode == 'projected':
        return (math.degrees(math.atan2(x2 - x1, y2 - y1)) + 360.0) % 360.0
    if spec.mode == 'geodesic':
        az1, _, _ = spec.geod.inv(x1, y1, x2, y2)
        return az1 % 360.0
    return initial_bearing(y1, x1, y2, x2)


def _linestring_length_meters(line: Any, spec: StreetMeasureSpec) -> float:
    coords = list(line.coords)
    if len(coords) < 2:
        return 0.0
    total = 0.0
    for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
        total += _point_distance_meters(x1, y1, x2, y2, spec)
    return total


def _stored_length_to_meters(value: Any, spec: StreetMeasureSpec) -> Optional[float]:
    if spec.mode != 'projected' or not _is_usable_length_value(value):
        return None
    return float(value) * spec.coord_unit_to_meters


def _interpolate_segment_point(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    spec: StreetMeasureSpec,
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


def _segment_midpoint(line: Any, spec: StreetMeasureSpec) -> Any:
    coords = list(line.coords)
    if not coords:
        return Point(0.0, 0.0)
    if len(coords) == 1:
        return Point(coords[0])
    segment_lengths = [
        _point_distance_meters(x1, y1, x2, y2, spec)
        for (x1, y1), (x2, y2) in zip(coords, coords[1:])
    ]
    total_length = sum(segment_lengths)
    if total_length <= 0:
        return Point(coords[0])
    target = total_length / 2.0
    travelled = 0.0
    for ((x1, y1), (x2, y2)), seg_len_m in zip(zip(coords, coords[1:]), segment_lengths):
        if seg_len_m <= 0:
            continue
        if travelled + seg_len_m >= target:
            return _interpolate_segment_point(x1, y1, x2, y2, spec, target - travelled)
        travelled += seg_len_m
    return Point(coords[-1])


def _angular_distance_deg(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _classify_street_bearing(bearing: float) -> str:
    if min(_angular_distance_deg(bearing, 90.0), _angular_distance_deg(bearing, 270.0)) <= STREET_CARDINAL_TOLERANCE_DEG:
        return 'EW'
    if min(_angular_distance_deg(bearing, 0.0), _angular_distance_deg(bearing, 180.0)) <= STREET_CARDINAL_TOLERANCE_DEG:
        return 'NS'
    return 'DIAGONAL'


def _build_segment_identifier(street_name: str, segment_id: Any, part_idx: Optional[int]) -> str:
    base_id = _coerce_street_text(segment_id) or 'unknown'
    identifier = f"{street_name} | seg={base_id}"
    if part_idx is not None:
        identifier += f":part{part_idx + 1}"
    return identifier


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
    spec: StreetMeasureSpec,
) -> Tuple[str, str, str]:
    candidate_positions = candidates.sindex.query(ray, predicate='intersects')
    midpoint_point = Point(midpoint.x, midpoint.y)
    best_identifier = ''
    best_distance = ''
    best_meters = None
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
            best_identifier = candidates['identifier'].iloc[int(candidate_pos)]
            best_distance = dist_m
    if best_meters is None:
        return '', '', ''
    return best_identifier, side, best_distance


def _prepare_street_centerline_segments(gdf: Any, ellipsoid: str) -> Tuple[Any, StreetMeasureSpec]:
    _require_street_dependencies()
    if gdf is None or gdf.empty:
        raise ValueError('Input street dataset is empty.')

    gdf = gdf.copy()
    name_field = _find_preferred_field(gdf.columns, STREET_NAME_FIELDS)
    if name_field is None:
        raise ValueError(
            'Could not infer a street-name field. Tried: '
            + ', '.join(STREET_NAME_FIELDS)
        )
    roadtype_field = _find_preferred_field(gdf.columns, STREET_ROADTYPE_FIELDS)
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

    gdf = gdf[gdf.geometry.notnull()].copy()
    gdf = gdf[gdf.geometry.geom_type.isin(['LineString', 'MultiLineString'])].copy()
    if gdf.empty:
        raise ValueError('No line geometries remain after street filtering.')

    id_field = _find_preferred_field(gdf.columns, STREET_ID_FIELDS)
    length_field = _find_preferred_field(gdf.columns, STREET_LENGTH_FIELDS)
    spec = _build_street_measure_spec(gdf.crs, ellipsoid)

    records = []
    for row in gdf.itertuples(index=True):
        geometry = row.geometry
        street_name = _coerce_street_text(getattr(row, name_field))
        if not street_name:
            continue
        parts = _iter_linestring_parts(geometry)
        if not parts:
            continue
        raw_segment_id = getattr(row, id_field) if id_field is not None else row.Index
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
                'identifier': _build_segment_identifier(street_name, raw_segment_id, part_label),
                'street_name': street_name,
                'length_m': float(length_m),
                'bearing': round(bearing, 3),
                'street_class': _classify_street_bearing(bearing),
                'midpoint': midpoint,
                'geometry': part,
            })

    if not records:
        raise ValueError('No usable street segments were found in the input dataset.')

    segments = gpd.GeoDataFrame(records, geometry='geometry', crs=gdf.crs)
    return segments[segments['street_class'].isin(['EW', 'NS'])].copy(), spec


def _build_street_centerline_rows(segments: Any, spec: StreetMeasureSpec, unit: str) -> List[Dict[str, Any]]:
    if segments.empty:
        return []

    segments = segments.reset_index(drop=True).copy()
    bounds = tuple(float(value) for value in segments.total_bounds)
    class_groups = {}
    class_positions = {}
    for street_class in ('EW', 'NS'):
        class_gdf = segments[segments['street_class'] == street_class].copy()
        class_gdf['source_pos'] = class_gdf.index
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
            hit_identifier, hit_dir, hit_distance_m = _first_hit_for_ray(
                class_pos,
                row['midpoint'],
                ray,
                side,
                class_gdf,
                spec,
            )
            if hit_identifier:
                hits.append((hit_identifier, hit_dir, _normalize_measurement_output(hit_distance_m, unit)))
            else:
                hits.append(('', '', ''))

        rows.append({
            'identifier': row['identifier'],
            'length': _normalize_measurement_output(row['length_m'], unit),
            'bearing': round(float(row['bearing']), 3),
            'street_1': hits[0][0],
            'dir_1': hits[0][1],
            'dist_1': hits[0][2],
            'street_2': hits[1][0],
            'dir_2': hits[1][1],
            'dist_2': hits[1][2],
        })
    return rows


def _load_street_centerline_dataset(input_path: Union[str, Path]) -> Any:
    _require_street_dependencies()
    return gpd.read_file(str(input_path))


def _write_street_centerline_csv(rows: List[Dict[str, Any]], output_path: Union[str, Path]) -> None:
    fieldnames = ['identifier', 'length', 'bearing', 'street_1', 'dir_1', 'dist_1', 'street_2', 'dir_2', 'dist_2']
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def analyze_street_centerlines(
    input_path: Union[str, Path] = STREETS_DEFAULT_INPUT,
    output_path: Union[str, Path] = STREETS_DEFAULT_OUTPUT,
    unit: str = 'feet',
    ellipsoid: str = 'wgs84',
) -> int:
    dataset = _load_street_centerline_dataset(input_path)
    segments, spec = _prepare_street_centerline_segments(dataset, ellipsoid)
    rows = _build_street_centerline_rows(segments, spec, unit)
    _write_street_centerline_csv(rows, output_path)
    return len(rows)


def _run_streets_centerlines(args: argparse.Namespace) -> None:
    rows_written = analyze_street_centerlines(
        input_path=args.input,
        output_path=args.output,
        unit=args.units,
        ellipsoid=args.ell,
    )
    print(f"Wrote {rows_written} rows to {args.output}")


#===============================================================================
# CLI
#===============================================================================
# Developing CLI Interfaces for *every* defined geodesy function
# all centrally located here.
### TO DO
### Eventually all these functions will be in active use by a dynamic web app.
### Having everything here in one place now will help with that migration later.
#===============================================================================

def _parse_ellipsoid_arg(value: str) -> str:
    try:
        return _normalize_ellipsoid(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc))

def _add_ellipsoid_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        '--ellipsoid',
        nargs='?',
        const=DEFAULT_ELLIPSOID,
        default=DEFAULT_ELLIPSOID,
        type=_parse_ellipsoid_arg,
        help='Ellipsoid: WGS84 (default), NAD83, or sphere.'
    )

def main() -> None:
    parser = argparse.ArgumentParser(description='Geodesy toolkit')
    sub = parser.add_subparsers(dest='cmd')

    streets = sub.add_parser('streets', help='Street segment analysis tools')
    streets_sub = streets.add_subparsers(dest='streets_cmd', required=True)

    centerlines = streets_sub.add_parser(
        'centerlines',
        help='Analyze centerline street segments and midpoint adjacencies',
    )
    centerlines.add_argument(
        '--input',
        default=str(STREETS_DEFAULT_INPUT),
        help='Input line dataset path (default: %(default)s)',
    )
    centerlines.add_argument(
        '--output',
        default=str(STREETS_DEFAULT_OUTPUT),
        help='Output CSV path (default: %(default)s)',
    )
    centerlines.add_argument(
        '--units',
        choices=['feet', 'miles', 'poles', 'm', 'km'],
        default='feet',
        help='Output distance units (default: %(default)s)',
    )
    centerlines.add_argument(
        '--ell',
        type=_parse_street_ellipsoid_arg,
        default='wgs84',
        help='Fallback ellipsoid when the input has no CRS: wgs84, nad83, pseudomerc, or none.',
    )

    d_help = 'Forward geodetic: lat1, lon1, az1, dist -> lat2, lon2, az2'
    d = sub.add_parser('direct', help=d_help)
    d.add_argument('lat1', type=float)
    d.add_argument('lon1', type=float)
    d.add_argument('az1', type=float)
    d.add_argument('dist', type=float)
    d.add_argument('--unit', choices=['miles', 'feet'], default='miles')
    _add_ellipsoid_arg(d)

    i_help = 'Inverse geodetic: lat1, lon1, lat2, lon2 -> az1, az2, dist'
    i = sub.add_parser('inverse', help=i_help)
    i.add_argument('lat1', type=float)
    i.add_argument('lon1', type=float)
    i.add_argument('lat2', type=float)
    i.add_argument('lon2', type=float)
    i.add_argument('--unit', choices=['miles', 'feet'], default='miles')
    _add_ellipsoid_arg(i)

    spw = sub.add_parser('walk', help='Find points on a progression of angles and side lengths')
    spw.add_argument('lat0', type=float, help='Start latitude')
    spw.add_argument('lon0', type=float, help='Start longitude')
    spw.add_argument('init_bearing', type=float, default=45.0, help='Initial bearing (cw: N=0, E=90)')
    spw.add_argument('turn_angle', type=float, default=90.0, help='Change in bearing each step (deg, cw)')
    spw.add_argument('walk_dist', type=float, default=1.0, help='Initial side length (mi)')
    spw.add_argument('num_turns', type=int, default=4, help='Number of steps to take')
    spw.add_argument('change_rate', type=float, default=1.0, help='Ratio of side distance each step (1.0=same)')
    spw.add_argument('tag', type=str, default='', help='Point label (default is digits in sequence)')
    _add_ellipsoid_arg(spw)

    sp = sub.add_parser('golden-spiral-in', help='Generate inward-growing golden spiral points')
    sp.add_argument('lat0', type=float, default=39.0, help='Start latitude')
    sp.add_argument('lon0', type=float, default=-77.0, help='Start longitude')
    sp.add_argument('base', type=float, default=10.0, help='Base leg length (mi)')
    sp.add_argument('bearing', type=float, default=45.0, help='Initial bearing')
    sp.add_argument('legs', type=int, default=5, help='Number of legs')
    sp.add_argument('tag', type=str, default='', help='Point label (default=serial_num)')
    sp.add_argument('--ccw', action='store_true', help='Counter-clockwise spiral')
    _add_ellipsoid_arg(sp)

    spp = sub.add_parser('golden-spiral-plot-in', help='Plot golden spiral')
    spp.add_argument('--lat0', type=float, required=True)
    spp.add_argument('--lon0', type=float, required=True)
    spp.add_argument('--base', type=float, default=1.0)
    spp.add_argument('--bearing', type=float, default=90.0)
    spp.add_argument('--legs', type=int, default=5)
    spp.add_argument('--ccw', action='store_true')
    _add_ellipsoid_arg(spp)
    spp.add_argument('--projection', choices=['plate', 'ortho'], default='ortho')
    spp.add_argument('--pad', type=float, default=0.01)

    sp = sub.add_parser('golden-spiral-out', help='Generate outward-growing golden spiral points')
    sp.add_argument('lat0', type=float, default=39.0, help='Start latitude')
    sp.add_argument('lon0', type=float, default=-77.0, help='Start longitude')
    sp.add_argument('base', type=float, default=1.0, help='Base leg length (mi)')
    sp.add_argument('bearing', type=float, default=45.0, help='Initial bearing (deg)')
    sp.add_argument('legs', type=int, default=5, help='Number of legs')
    sp.add_argument('tag', type=str, default='', help='Point label (default=serial_num)')
    sp.add_argument('--ccw', action='store_true', help='Counter-clockwise spiral')
    _add_ellipsoid_arg(sp)

    f3 = sub.add_parser('find-lat', help='Find latitudes for fixed lon and distance')
    f3.add_argument('lat1', type=float)
    f3.add_argument('lon1', type=float)
    f3.add_argument('lon2', type=float)
    f3.add_argument('dist', type=float)
    _add_ellipsoid_arg(f3)

    f2 = sub.add_parser('find-lon', help='Find longitudes for fixed lat and distance')
    f2.add_argument('lat1', type=float)
    f2.add_argument('lon1', type=float)
    f2.add_argument('lat2', type=float)
    f2.add_argument('dist', type=float)
    _add_ellipsoid_arg(f2)

    sail = sub.add_parser('sailpath', help='Compute sail paths and emit CSV + optional KML')
    sail.add_argument('--input-csv', help='CSV headers: LOC0,LAT0,LON0,LOC1,LAT1,LON1,STEP_DIST')

    sail.add_argument('--loc0')
    sail.add_argument('--lat0', type=float)
    sail.add_argument('--lon0', type=float)
    sail.add_argument('--loc1')
    sail.add_argument('--lat1', type=float)
    sail.add_argument('--lon1', type=float)
    sail.add_argument('--step-dist', type=float, help='Step distance (default unit: miles).')
    sail.add_argument('--step-unit', choices=['miles', 'feet', 'meters', 'yards', 'poles', 'km'],
                      default='miles', help='Step distance unit.')
    _add_ellipsoid_arg(sail)
    sail.add_argument('--one-way', action='store_true')
    sail.add_argument('--label-format', default='{from_loc} {to_loc} {step}',
                      help='Must include {from_loc},{to_loc},{step}.')

    sail.add_argument('--output-kml', default=None,
                      help='If omitted in batch mode, defaults to input CSV path with .kml')
    sail.add_argument('--no-kml', action='store_true')
    sail.add_argument('--kml-pointer-scale', type=float, default=0.05)
    sail.add_argument('--kml-pointer-min', type=float, default=100.0 / METERS_PER_MILE)
    sail.add_argument('--kml-pointer-max', type=float, default=300.0 / METERS_PER_MILE)
    sail.add_argument('--kml-pointer-unit', choices=['miles', 'feet', 'meters', 'yards', 'poles', 'km'],
                      default='miles', help='Pointer length unit.')
    sail.add_argument('--kml-cardinal-halfwidth', type=float, default=15.0,
                      help='Half-width (degrees) for bins around centers.')
    sail.add_argument('--no-gridlines', action='store_true',
                      help='Disable drawing a top-level gridlines folder.')

    alt = sub.add_parser('alt', help='Sample elevations for CSV points using a DEM')
    alt.add_argument('--input-csv', required=True, help='CSV headers: LOC,LAT,LON')
    alt.add_argument('--dem', required=True, help='DEM GeoTIFF path')
    alt.add_argument('--input-epsg', type=int, default=4326, help='Input EPSG (default: 4326)')
    alt.add_argument('--geojson', nargs='?', const='AUTO', default=None,
                     help='Write GeoJSON output; omit value to use input basename.')
    alt.add_argument('--include-feet', action='store_true', help='Include ALT_FT in CSV output.')

    g = sub.add_parser('gauss')
    g.add_argument('alpha', type=float)
    g.add_argument('beta', type=float)
    g.add_argument('gamma', type=float)
    g.add_argument('delta', type=float)
    g.add_argument('epsilon', type=float)
    sub.add_parser('testgauss')

    args = parser.parse_args()
    if args.cmd == 'streets':
        if args.streets_cmd == 'centerlines':
            _run_streets_centerlines(args)
            return
        parser.error('A streets subcommand is required.')
    if args.cmd == 'direct':
        print(direct_geodetic(args.lat1, args.lon1, args.az1, args.dist, unit=args.unit, ellipsoid=args.ellipsoid))
        return
    if args.cmd == 'inverse':
        print(inverse_geodetic(args.lat1, args.lon1, args.lat2, args.lon2, unit=args.unit, ellipsoid=args.ellipsoid))
        return
    if args.cmd == 'golden-spiral-in':
        pts = golden_spiral_in(args.lat0, args.lon0,
                               args.bearing, args.base,
                               args.legs, ccw=args.ccw, ellipsoid=args.ellipsoid)
        for i, (lat, lon) in enumerate(pts):
            print(f'{args.tag}{i}, {lat}, {lon}')
        return
    if args.cmd == 'golden-spiral-plot-in':
        pts = golden_spiral_in(args.lat0, args.lon0,
                               args.bearing, args.base,
                               args.legs, ccw=args.ccw, ellipsoid=args.ellipsoid)
        golden_spiral_plot_in(pts, projection=args.projection, pad=args.pad)
        return
    if args.cmd == 'golden-spiral-out':
        pts = golden_spiral_out(args.lat0, args.lon0,
                                args.bearing, args.base,
                                args.legs, ccw=args.ccw,
                                ellipsoid=args.ellipsoid)
        for i, (lat, lon) in enumerate(pts):
            print(f'{args.tag}{i}, {lat}, {lon}')
        return
    if args.cmd == 'find-lat':
        sols = find_latitudes_for_known_longitude_and_distance(
            args.lat1, args.lon1, args.dist, args.lon2, ellipsoid=args.ellipsoid)
        print(sols)
        return
    if args.cmd == 'find-lon':
        sols = find_longitudes_for_known_latitude_and_distance(
            args.lat1, args.lon1, args.dist, args.lat2, ellipsoid=args.ellipsoid)
        print(sols)
        return
    if args.cmd == 'walk':
        pts = walk_angles(args.lat0, args.lon0, args.init_bearing,
                          args.num_turns, args.turn_angle, args.walk_dist,
                          args.change_rate, ellipsoid=args.ellipsoid)
        for i, (lat, lon) in enumerate(pts):
            print(f'{i}, {lat:.9f}, {lon:.9f}')
        return
    if args.cmd == 'sailpath':
        want_kml = not args.no_kml
        pointer_min_miles = _to_miles(args.kml_pointer_min, args.kml_pointer_unit)
        pointer_max_miles = _to_miles(args.kml_pointer_max, args.kml_pointer_unit)

        if args.input_csv:
            kml_out = None
            if want_kml:
                if args.output_kml:
                    kml_out = args.output_kml
                else:
                    base, _ = os.path.splitext(args.input_csv)
                    kml_out = f'{base}.kml'

            _process_csv(args.input_csv, args.ellipsoid, args.one_way, args.label_format,
                         kml_out_path=kml_out,
                         step_unit=args.step_unit,
                         pointer_scale=args.kml_pointer_scale,
                         pointer_min_miles=pointer_min_miles,
                         pointer_max_miles=pointer_max_miles,
                         cardinal_halfwidth_deg=args.kml_cardinal_halfwidth,
                         draw_gridlines=(not args.no_gridlines))
            return

        required = ['lat0', 'lon0', 'lat1', 'lon1', 'step_dist']
        missing = [r for r in required if getattr(args, r) is None]
        if missing:
            parser.error(f"Missing required args for single-run mode: {', '.join(missing)} (or provide --input-csv).")

        step_dist_miles = _to_miles(args.step_dist, args.step_unit)

        print('Label, Latitude, Longitude, Azimuth_To_Target')
        kml_out_single = args.output_kml if want_kml else None
        if want_kml and not kml_out_single:
            kml_out_single = 'sailpath_output.kml'

        legs: List[Leg] = [] if kml_out_single else None
        _process_one_run(args.loc0 or '', args.lat0, args.lon0,
                         args.loc1 or '', args.lat1, args.lon1,
                         step_dist_miles, args.ellipsoid, args.one_way, args.label_format,
                         legs_out=legs, cardinal_halfwidth_deg=args.kml_cardinal_halfwidth)

        if kml_out_single and legs:
            _build_kml(
                legs, kml_out_single, ellipsoid=args.ellipsoid,
                pointer_scale=args.kml_pointer_scale,
                pointer_min_miles=pointer_min_miles,
                pointer_max_miles=pointer_max_miles,
                cardinal_halfwidth_deg=args.kml_cardinal_halfwidth,
                draw_gridlines=(not args.no_gridlines)
            )
        return
    if args.cmd == 'alt':
        points_with_altitudes = get_altitudes_for_points(args.input_csv, args.dem, args.input_epsg)

        if not points_with_altitudes:
            print('No data processed. Exiting.')
            return

        if args.include_feet:
            print('LOC,LAT,LON,ALT_M,ALT_FT')
        else:
            print('LOC,LAT,LON,ALT_M')

        for point in points_with_altitudes:
            alt_m_str = f"{point['ALT_M']:.3f}" if point['ALT_M'] is not None else 'N/A'
            alt_ft_str = f"{point['ALT_FT']:.3f}" if point['ALT_FT'] is not None else 'N/A'
            lat_str = f"{point['LAT']:.7f}"
            lon_str = f"{point['LON']:.7f}"
            if args.include_feet:
                print(f"{point['LOC']},{lat_str},{lon_str},{alt_m_str},{alt_ft_str}")
            else:
                print(f"{point['LOC']},{lat_str},{lon_str},{alt_m_str}")

        if args.geojson:
            if args.geojson == 'AUTO':
                base, _ = os.path.splitext(args.input_csv)
                geojson_path = f'{base}.geojson'
            else:
                geojson_path = args.geojson
            _write_geojson(points_with_altitudes, geojson_path)
            print(f"GeoJSON output saved to: {geojson_path}")
        return

    parser.print_help()


if __name__ == '__main__':
    main()
