import csv
import os
import math
import json
import argparse
from collections import defaultdict
from pathlib import Path
from typing import List, Tuple, Callable, Optional
from pyproj import Geod

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
    import simplekml
    from shapely.geometry import shape
except ImportError:
    rasterio = None
    gpd = None
    simplekml = None

# Visualization (deferred imports in functions)

#===============================================================================
# Constants and Unit Conversions
#===============================================================================
EARTH_RADIUS_MILES = 3958
EARTH_RADIUS_FEET = EARTH_RADIUS_MILES * 5280
MILES_PER_FOOT = 1 / 5280.0
MILES_PER_YARD = 3 / 5280.0
MILES_PER_POLE = 16.5 / 5280.0  # 1 rod = 16.5 ft
DEG2RAD = math.pi / 180.0
RAD2DEG = 180.0 / math.pi
FIp = (math.sqrt(5)+1)/2
FIm = (math.sqrt(5)-1)/2

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
        - angles: A dictionary of angles {A, B, C} (in degrees).
        - side_lengths: A dictionary of side lengths {AB, BC, CA} (same units a R).
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
    dist = EARTH_RADIUS_MILES * c
    return dist if unit=='miles' else dist * 5280


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
    """Cosine of (π - x) = -cos(x)."""
    return -math.cos(x)

def sin_supplement(x):
    """Sine of (π - x) = sin(x)."""
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

_GEOID = Geod(ellps='WGS84')

def direct_geodetic(phi1: float, lam1: float, az1: float, dist: float,
                    unit: str='miles', ellipsoid: bool=False) -> Tuple[float, float, float]:
    """
    Forward geodetic: given lat1, lon1, azimuth, distance -> lat2, lon2, back azimuth.
    Supports ellipsoidal (pyproj) and spherical.
    """
    if unit=='miles':
        dist_m = dist * 1609.344
    else:
        dist_m = dist * 0.3048
    if ellipsoid:
        lon2, lat2, az2 = _GEOID.fwd(lam1, phi1, az1, dist_m)
        return lat2, lon2, az2
    # spherical fallback
    sigma = dist / EARTH_RADIUS_MILES
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
                      unit: str='miles', ellipsoid: bool=False) -> Tuple[float, float, float]:
    """
    Inverse geodetic: given two lat/lon points -> az1, az2, distance.
    """
    if ellipsoid:
        az1, az2, dist_m = _GEOID.inv(lam1, phi1, lam2, phi2)
        dist = dist_m / (1609.344 if unit=='miles' else 0.3048)
        return az1 % 360, az2 % 360, dist
    # spherical fallback
    phi1r, phi2r = phi1*DEG2RAD, phi2*DEG2RAD
    dlam = (lam2 - lam1)*DEG2RAD
    cos_sigma = math.sin(phi1r)*math.sin(phi2r) + math.cos(phi1r)*math.cos(phi2r)*math.cos(dlam)
    sigma = math.acos(max(-1, min(1, cos_sigma)))
    az1 = math.atan2(math.sin(dlam)*math.cos(phi2r),
                      math.cos(phi1r)*math.sin(phi2r) - math.sin(phi1r)*math.cos(phi2r)*math.cos(dlam))
    az2 = math.atan2(math.sin(-dlam)*math.cos(phi1r),
                      math.cos(phi2r)*math.sin(phi1r) - math.sin(phi2r)*math.cos(phi1r)*math.cos(dlam))
    dist = EARTH_RADIUS_MILES * sigma
    if unit!='miles':
        dist *= 5280
    return az1*RAD2DEG % 360, az2*RAD2DEG % 360, dist

# shorthand endpoint throws
def throw_point(start_lat: float,
                   start_lon: float,
                   bearing: float,
                   distance: float,
                   units: str='miles', radius: float=EARTH_RADIUS_MILES,
                   ellipsoid: bool=False) -> Tuple[float,float,float]:
    """
    Compute endpoint given start, distance, and bearing. Supports feet/yards/poles.
    """
    if ellipsoid:
        return direct_geodetic(start_lat, start_lon, bearing, distance, unit=units)
    # convert units to miles
    if units == 'feet': d = distance * MILES_PER_FOOT
    elif units == 'yards': d = distance * MILES_PER_YARD
    elif units == 'poles': d = distance * MILES_PER_POLE
    else: d = distance
    φ1 = math.radians(start_lat)
    λ1 = math.radians(start_lon)
    θ = math.radians(bearing)
    δ = d / radius
    φ2 = math.asin(math.sin(φ1)*math.cos(δ) + math.cos(φ1)*math.sin(δ)*math.cos(θ))
    y = math.sin(θ)*math.sin(δ)*math.cos(φ1)
    x = math.cos(δ) - math.sin(φ1)*math.sin(φ2)
    λ2 = λ1 + math.atan2(y, x)
    lat2 = math.degrees(φ2)
    lon2 = math.degrees((λ2 + 3*math.pi) % (2*math.pi) - math.pi)
    final_bearing = (math.degrees(math.atan2(y, x)) + 360) % 360
    return lat2, lon2, final_bearing

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
                            lat2_target_deg, ellipsoid: bool=False, tol=1e-9
    ):
    """
    Finds the longitude(s) of a second point, given the first point (lat1, lon1),
    the distance to the second point, and the latitude of the second point (lat2).

    Args:
        lat1_deg (float): Latitude of the first point in degrees.
        lon1_deg (float): Longitude of the first point in degrees.
        distance_miles (float): Distance from the first point to the second in miles.
        lat2_target_deg (float): Latitude of the second point in degrees.
        ellipsoid (bool): If True, use ellipsoidal calculations for refinement.
                          If False, use purely spherical calculations.
        tol (float): Tolerance for floating point comparisons.

    Returns:
        list: A list of possible longitudes (in degrees) for the second point.
              Returns an empty list if no solution is found.
              May return a special list like [float('nan')] if lat1 is a pole
              and infinite solutions exist for lon2 in spherical case.
    """
    from scipy.optimize import fsolve

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
        if not ellipsoid: return [float('nan')] # Indicates infinite solutions
        # If ellipsoidal, proceed, fsolve might work or fail gracefully.

    if abs(math.cos(lat2_target_rad)) < tol: # lat2_target is a pole
        # Distance from (lat1, lon1) to this pole must be distance_miles
        _, _, dist_to_pole = inverse_geodetic(lat1_deg, lon1_deg, lat2_target_deg, lon1_deg, unit='miles', ellipsoid=ellipsoid)
        if abs(dist_to_pole - distance_miles) < tol:
            # The point is the pole. The longitude can be considered that of the geodesic.
            # For a sphere, it's lon1_deg. For ellipsoid, direct_geodetic can find it.
            if ellipsoid:
                az_to_pole, _, _ = inverse_geodetic(lat1_deg, lon1_deg, lat2_target_deg, lon1_deg, unit='miles', ellipsoid=True)
                _, lon_at_pole, _ = direct_geodetic(lat1_deg, lon1_deg, az_to_pole, distance_miles, unit='miles', ellipsoid=True)
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

    if not ellipsoid: # Purely spherical solution
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
        args=(lat1_deg, lon1_deg, lat2_target_deg, distance_miles, ellipsoid),
        full_output=True, xtol=1e-10 # Set tolerance for fsolve
    )
    if ier1 == 1: # Solution found
        # Check if the solution is valid (distance matches closely)
        final_check_dist_1 = objective_func(lon2_sol1_arr, lat1_deg, lon1_deg, lat2_target_deg, distance_miles, ellipsoid) + distance_miles
        if abs(final_check_dist_1 - distance_miles) < distance_miles * 1e-7: # Relative tolerance for distance match
             solutions.append(normalize_longitude(lon2_sol1_arr[0]))

    # Refine second guess (if distinct)
    if abs(delta_lambda_rad) > tol and abs(delta_lambda_rad - math.pi) > tol:
        lon2_guess_deg_2 = normalize_longitude(math.degrees(lon2_guess_rad_2))
        # Only solve if guess2 is significantly different from guess1 to avoid redundant computation
        # (fsolve might converge to the same root if initial guesses are too close)
        is_different_guess = True
        if solutions: # if first solution was found
            if abs(normalize_longitude(lon2_guess_deg_2 - solutions[0])) < 1e-3 or \
               abs(normalize_longitude(lon2_guess_deg_2 + solutions[0])) < 1e-3 : # check if it's same or antipodal to first solution's guess
                  # Heuristic: if guesses are very close, fsolve might find same root or struggle
                  pass # Potentially skip if too close, or let fsolve try

        lon2_sol2_arr, _, ier2, _ = fsolve(
            objective_func, x0=[lon2_guess_deg_2],
            args=(lat1_deg, lon1_deg, lat2_target_deg, distance_miles, ellipsoid),
            full_output=True, xtol=1e-10
        )
        if ier2 == 1:
            final_check_dist_2 = objective_func(lon2_sol2_arr, lat1_deg, lon1_deg, lat2_target_deg, distance_miles, ellipsoid) + distance_miles
            if abs(final_check_dist_2 - distance_miles) < distance_miles * 1e-7:
                solutions.append(normalize_longitude(lon2_sol2_arr[0]))

    return sorted(list(set(s for s in solutions if not math.isnan(s)))) # Unique, sorted, non-NaN solutions

def find_latitudes_for_known_longitude_and_distance(
                        lat1_deg: float, lon1_deg: float,
                        distance_miles: float, lon2_target_deg: float,
                        ellipsoid: bool=False, tol: float=1e-9
    ) -> List[float]:
    """
    Find latitude(s) lat2 such that distance(P1, P2)=distance and lon2=lon2_target.
    """
    def obj(lat2_param) -> float: # lat2_param is a 1-element numpy array from fsolve
        # Extract the scalar float value from the numpy array passed by fsolve
        current_lat2_deg = float(lat2_param[0])
        _, _, d = inverse_geodetic(lat1_deg, lon1_deg, current_lat2_deg,
                                   lon2_target_deg, unit='miles', ellipsoid=ellipsoid)
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


#===============================================================================
# Spiral Generators & Plotters
#===============================================================================

def walk_angles(init_lat: float, init_lon: float,
                init_bearing: float=0.0, num_turns: int=4,
                turn_angle: float=90.0, walk_dist: float=10.0,
                change_rate: float=1.0, ellipsoid: bool=False
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
        ellipsoid (bool, optional): If True, use ellipsoidal calculations. Defaults to False (spherical).

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
                           ccw: bool=False, ellipsoid: bool=False
    ) -> List[Tuple[float,float]]:
    """
    Generate points of a spherical right-angle golden-ratio spiral.
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
                        ccw: bool=False, ellipsoid: bool=False
    ) -> List[Tuple[float,float]]:
    """
    Generate points of a spherical right-angle golden-ratio spiral.
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
# TODO: Integrate get_altitudes_for_points()
def get_altitudes_for_points(csv_path, dem_path, input_points_epsg):
    """
    Reads points from a CSV, queries their elevations from a DEM,
    converts elevations to feet, and returns a list of dictionaries.
    """
    # --- Configuration ---
    import rasterio.warp
    # Absolute path to your input CSV file with point data
    CSV_FILE_PATH = r"data\start_points.csv"
    # Absolute path to your Digital Elevation Model (DEM) GeoTIFF file
    DEM_FILE_PATH = r"data\downloaded\dc_dem.tif"
    # Absolute path for the output GeoJSON file
    GEOJSON_OUTPUT_PATH = r"data\poi_alt.geojson"
    # EPSG code for the coordinate system of your input LAT/LON points
    # NAD83 geographic coordinates (latitude/longitude)
    INPUT_POINTS_EPSG = 4269 # For NAD83
    # If your points were WGS84, you would use:
    # INPUT_POINTS_EPSG = 4326 # For WGS84
    # Conversion factor
    METERS_TO_FEET = 3.28084
    # --- End Configuration ---

    input_lon_lat_coords = []  # Stores (lon, lat) tuples from CSV
    initial_point_data = []    # Stores original data like LOC, LAT, LON

    print(f"Reading points from: {csv_path}")
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
        print("No valid points found or read from CSV file.")
        return []

    print(f"Querying elevations from DEM: {dem_path}")
    processed_points_with_alt = []

    try:
        with rasterio.open(dem_path) as src_dem:
            # Define the CRS of the input points
            points_crs_input = rasterio.crs.CRS.from_epsg(input_points_epsg)
            print(f"Assuming input point coordinates are in CRS: {points_crs_input.to_string()} (EPSG:{input_points_epsg})")
            print(f"DEM CRS is: {src_dem.crs.to_string()}")

            coords_for_sampling = input_lon_lat_coords 

            # Transform coordinates if DEM's CRS is different from the input points' CRS
            if src_dem.crs != points_crs_input:
                print(f"DEM CRS ({src_dem.crs}) differs from input points CRS ({points_crs_input}). Transforming points for DEM sampling...")
                lons = [p[0] for p in input_lon_lat_coords]
                lats = [p[1] for p in input_lon_lat_coords]
                
                transformed_coords = rasterio.warp.transform(
                    points_crs_input,    # Source CRS (e.g., NAD83)
                    src_dem.crs,         # Destination CRS (DEM's CRS)
                    lons,                # List of longitudes
                    lats                 # List of latitudes
                )
                # Re-zip into (x, y) tuples for sampling
                coords_for_sampling = list(zip(transformed_coords[0], transformed_coords[1]))
            
            # Sample DEM for elevations (returns a generator)
            # The values are typically in meters for DEMs
            sampled_elevations_m_raw = src_dem.sample(coords_for_sampling)
            
            # Extract the first band value for each point
            # and combine with original data
            for i, base_data in enumerate(initial_point_data):
                elevation_m_array = next(sampled_elevations_m_raw) # Get the numpy array for the point
                elevation_m = elevation_m_array[0] # Get the first (and only) band value
                
                alt_ft = None
                valid_elevation_m = None 

                # Check if the sampled elevation is the NoData value for the DEM
                if src_dem.nodata is not None and elevation_m == src_dem.nodata:
                    print(f"Warning: Point {base_data['LOC']} (Lat: {base_data['LAT']}, Lon: {base_data['LON']}) is on a NoData pixel or outside DEM extent. Elevation set to N/A.")
                elif elevation_m is None or elevation_m < -10000: # Arbitrary large negative for potential other NoData markers
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

#===============================================================================
# CLI
#===============================================================================
# Developing CLI Interfaces for *every* defined geodesy function
# all centrally located here.
### TO DO
### Eventually all these functions will be in active use by a dynamic web app.
### Having everything here in one place now will help with that migration later.
#===============================================================================

if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Spherical Geometry Toolkit')
    sub = p.add_subparsers(dest='cmd')
    
    d_help = 'Forward geodetic: lat1, lon1, az1, dist -> lat2, lon2, az2'
    d = sub.add_parser('direct', help=d_help)
    d.add_argument('lat1',type=float); d.add_argument('lon1',type=float)
    d.add_argument('az1',type=float); d.add_argument('dist',type=float)
    d.add_argument('--unit',choices=['miles','feet'],default='miles')
    d.add_argument('--ellipsoid',action='store_true', help='Use WGS84 ellipsoid (default: spherical)')
    
    i_help = 'Inverse geodetic: lat1, lon1, lat2, lon2 -> az1, az2, dist'
    i = sub.add_parser('inverse', help=i_help)
    i.add_argument('lat1',type=float); i.add_argument('lon1',type=float)
    i.add_argument('lat2',type=float); i.add_argument('lon2',type=float)
    i.add_argument('--unit',choices=['miles','feet'],default='miles')
    i.add_argument('--ellipsoid',action='store_true', help='Use WGS84 ellipsoid (default: spherical)')
    
    alt_help = 'Look Up Altitudes for Points'
    alt = sub.add_parser('alt', help=alt_help)
    alt.add_argument('lat0',type=float)
    alt.add_argument('lon0',type=float)
    alt.add_argument('az',type=float)
    alt.add_argument('dist',type=float)
    alt.add_argument('--ellipsoid',action='store_true', help='Use WGS84 ellipsoid (default: spherical)')
    
    spw = sub.add_parser('walk', help='Find points on a progression of angles and side lengths')
    spw.add_argument('lat0', type=float, help='Start latitude')
    spw.add_argument('lon0', type=float, help='Start longitude')
    spw.add_argument('init_bearing', type=float, default=45.0, help='Initial bearing (cw: N=0, E=90)')
    spw.add_argument('turn_angle', type=float, default=90.0, help='Change in bearing each step (deg, cw)')
    spw.add_argument('walk_dist', type=float, default=1.0, help='Initial side length (mi)')
    spw.add_argument('num_turns', type=int, default=4, help='Number of steps to take')
    spw.add_argument('change_rate', type=float, default=1.0, help='Ratio of side distance each step (1.0=same)')    
    spw.add_argument('tag', type=str, default='', help='Point label (default is digits in sequence)')
    spw.add_argument('--ellipsoid', action='store_true', help='Use WGS84 ellipsoid for calculations (default: spherical)')
    
    sp = sub.add_parser('golden-spiral-in', help='Generate inward-growing golden spiral points')
    sp.add_argument('lat0', type=float, default=39.0, help='Start latitude')
    sp.add_argument('lon0', type=float, default=-77.0, help='Start longitude')
    sp.add_argument('base', type=float, default=10.0, help='Base leg length (mi)')
    sp.add_argument('bearing', type=float, default=45.0, help='Initial bearing')
    sp.add_argument('legs', type=int, default=5, help='Number of legs')
    sp.add_argument('tag', type=str, default='', help='Point label (default=serial_num)')
    sp.add_argument('--ccw', action='store_true', help='Counter-clockwise spiral')
    sp.add_argument('--ellipsoid', action='store_true', help='Use WGS84 ellipsoid for calculations (default: spherical)')
    
    spp = sub.add_parser('golden-spiral-plot-in', help='Plot golden spiral')
    spp.add_argument('--lat0', type=float, required=True)
    spp.add_argument('--lon0', type=float, required=True)
    spp.add_argument('--base', type=float, default=1.0)
    spp.add_argument('--bearing', type=float, default=90.0)
    spp.add_argument('--legs', type=int, default=5)
    spp.add_argument('--ccw', action='store_true')
    spp.add_argument('--ellipsoid', action='store_true', help='Use WGS84 ellipsoid for calculations (default: spherical)')
    spp.add_argument('--projection', choices=['plate','ortho'], default='ortho')
    spp.add_argument('--pad', type=float, default=0.01)
    
    sp = sub.add_parser('golden-spiral-out', help='Generate outward-growing golden spiral points')
    sp.add_argument('lat0', type=float, default=39.0, help='Start latitude')
    sp.add_argument('lon0', type=float, default=-77.0, help='Start longitude')
    sp.add_argument('base', type=float, default=1.0, help='Base leg length (mi)')
    sp.add_argument('bearing', type=float, default=45.0, help='Initial bearing (deg)')
    sp.add_argument('legs', type=int, default=5, help='Number of legs')
    sp.add_argument('tag', type=str, default='', help='Point label (default=serial_num)')
    sp.add_argument('--ccw', action='store_true', help='Counter-clockwise spiral')
    sp.add_argument('--ellipsoid', action='store_true', help='Use WGS84 ellipsoid for calculations (default: spherical)')
    
    f3 = sub.add_parser('find-lat', help='Find latitudes for fixed lon and distance')
    f3.add_argument('lat1', type=float)
    f3.add_argument('lon1', type=float)
    f3.add_argument('lon2', type=float)
    f3.add_argument('dist', type=float)
    
    f2 = sub.add_parser('find-lon', help='Find longitudes for fixed lat and distance')
    f2.add_argument('lat1', type=float)
    f2.add_argument('lon1', type=float)
    f2.add_argument('lat2', type=float)
    f2.add_argument('dist', type=float)
    
    g = sub.add_parser('gauss'); g.add_argument('alpha',type=float); g.add_argument('beta',type=float); g.add_argument('gamma',type=float); g.add_argument('delta',type=float); g.add_argument('epsilon',type=float)
    tg = sub.add_parser('testgauss')

    args = p.parse_args()
    if args.cmd == 'direct':
        print(direct_geodetic(args.lat1,args.lon1,args.az1,args.dist,unit=args.unit,ellipsoid=args.ellipsoid))
    elif args.cmd == 'inverse':
        print(inverse_geodetic(args.lat1,args.lon1,args.lat2,args.lon2,unit=args.unit,ellipsoid=args.ellipsoid))
    elif args.cmd == 'golden-spiral-in':
        pts = golden_spiral_in( args.lat0, args.lon0,
                                args.bearing, args.base,
                                args.legs, ccw=args.ccw, ellipsoid=args.ellipsoid)
        for i,(lat,lon) in enumerate(pts): print(f'{args.tag}{i}, {lat}, {lon}')
    elif args.cmd == 'golden-spiral-plot-in':
        pts = golden_spiral_in(args.lat0, args.lon0,
                                     args.bearing, args.base,
                                     args.legs, ccw=args.ccw)
        golden_spiral_plot_in(pts, projection=args.projection, pad=args.pad)
    elif args.cmd == 'golden-spiral-out':
        pts = golden_spiral_out(args.lat0, args.lon0,
                                args.bearing, args.base,
                                args.legs, ccw=args.ccw,
                                ellipsoid=args.ellipsoid)
        for i,(lat,lon) in enumerate(pts): print(f'{args.tag}{i}, {lat}, {lon}')
    elif args.cmd == 'find-lat':
        sols = find_latitudes_for_known_longitude_and_distance(
            args.lat1, args.lon1, args.dist, args.lon2)
        print(sols)
    elif args.cmd == 'find-lon':
        sols = find_longitudes_for_known_latitude_and_distance(
            args.lat1, args.lon1, args.dist, args.lat2)
        print(sols)
    elif args.cmd == 'walk':
        pts = walk_angles(args.lat0, args.lon0, args.init_bearing,
                          args.num_turns, args.turn_angle, args.walk_dist,
                          args.change_rate, ellipsoid=args.ellipsoid)
        for i, (lat,lon) in enumerate(pts): print(f'{i}, {lat:.9f}, {lon:.9f}')
    elif args.cmd == 'alt':
        print(throw_point(args.lat0, args.lon0, args.az, args.dist, ellipsoid=args.ellipsoid))
    else:
        p.print_help()