'''
geocalc.py

Consolidated geodesy and spherical-geometry utilities for point calculation,
triangle measurements, and future display modules.

Sections:
  1. Constants and unit conversions
  2. Basic geodesy functions (distance, bearing, destination point)
  3. Spherical triangle functions (angles, sides)
  4. Triangle measurement CSV output
  5. Placeholders for street and highpoint modules
  6. Main entrypoint with example usage
'''
import math

# ----------------------------------------
# 1. Constants and Unit Conversions
# ----------------------------------------
EARTH_RADIUS_MILES = 3958.8
MILES_PER_FOOT = 1 / 5280.0
MILES_PER_YARD = 3 / 5280.0
MILES_PER_POLE = 16.5 / 5280.0  # 1 rod = 16.5 ft

# ----------------------------------------
# 2. Basic Geodesy Functions
# ----------------------------------------

def bearing(pt1, pt2):
    """
    Compute the initial bearing (forward azimuth) from pt1 → pt2 in degrees.
    Each point is a (lat, lon) tuple in decimal degrees.
    Result is in [0,360).
    """
    φ1 = math.radians(pt1[0])
    φ2 = math.radians(pt2[0])
    Δλ = math.radians(pt2[1] - pt1[1])

    y = math.sin(Δλ) * math.cos(φ2)
    x = math.cos(φ1)*math.sin(φ2) - math.sin(φ1)*math.cos(φ2)*math.cos(Δλ)
    θ = math.atan2(y, x)

    # normalize to 0–360°
    return (math.degrees(θ) + 360) % 360

def haversine(lat1, lon1, lat2, lon2, radius=EARTH_RADIUS_MILES):
    """
    Great-circle distance between two lat/lon points on a sphere.
    Returns distance in same units as `radius`.
    """
    # Convert degrees to radians
    φ1, λ1 = math.radians(lat1), math.radians(lon1)
    φ2, λ2 = math.radians(lat2), math.radians(lon2)
    dφ = φ2 - φ1
    dλ = λ2 - λ1
    a = math.sin(dφ/2)**2 + math.cos(φ1) * math.cos(φ2) * math.sin(dλ/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius * c

def throw_endpoint(start_lat, start_lon, distance, bearing,
                   units='miles', radius=EARTH_RADIUS_MILES):
    """
    Compute destination lat/lon from a start point, distance and bearing.
    Supports miles, feet, yards, poles.
    """
    # Convert to miles
    if units == 'feet':
        d = distance * MILES_PER_FOOT
    elif units == 'yards':
        d = distance * MILES_PER_YARD
    elif units == 'poles':
        d = distance * MILES_PER_POLE
    else:
        d = distance  # default miles
    # Convert to radians
    φ1 = math.radians(start_lat)
    λ1 = math.radians(start_lon)
    θ = math.radians(bearing)
    δ = d / radius

    φ2 = math.asin(math.sin(φ1)*math.cos(δ)
                   + math.cos(φ1)*math.sin(δ)*math.cos(θ))
    y = math.sin(θ)*math.sin(δ)*math.cos(φ1)
    x = math.cos(δ) - math.sin(φ1)*math.sin(φ2)
    λ2 = λ1 + math.atan2(y, x)

    lat2 = math.degrees(φ2)
    lon2 = math.degrees((λ2 + 3*math.pi) % (2*math.pi) - math.pi)
    return lat2, lon2

# ----------------------------------------
# 3. Spherical Triangle Functions
# ----------------------------------------
def spherical_law_of_cosines_angles(a, b, c, radius=EARTH_RADIUS_MILES):
    """
    Interior angles (A, B, C) in degrees from side lengths a, b, c.
    Sides are in same units as `radius`.
    """
    # Convert side lengths to central angles (radians)
    a_rad, b_rad, c_rad = a/radius, b/radius, c/radius
    cosA = (math.cos(a_rad) - math.cos(b_rad)*math.cos(c_rad)) / (math.sin(b_rad)*math.sin(c_rad))
    cosB = (math.cos(b_rad) - math.cos(a_rad)*math.cos(c_rad)) / (math.sin(a_rad)*math.sin(c_rad))
    cosC = (math.cos(c_rad) - math.cos(a_rad)*math.cos(b_rad)) / (math.sin(a_rad)*math.sin(b_rad))
    return math.degrees(math.acos(cosA)), math.degrees(math.acos(cosB)), math.degrees(math.acos(cosC))


def spherical_triangle_properties(ptA, ptB, ptC, radius=EARTH_RADIUS_MILES):
    """
    Compute sides (AB, BC, CA) and angles (A, B, C) for a spherical triangle.
    Each point is (lat, lon).
    """
    latA, lonA = ptA
    latB, lonB = ptB
    latC, lonC = ptC
    # Side lengths
    AB = haversine(latA, lonA, latB, lonB, radius)
    BC = haversine(latB, lonB, latC, lonC, radius)
    CA = haversine(latC, lonC, latA, lonA, radius)
    # Angles
    A, B, C = spherical_law_of_cosines_angles(BC, CA, AB, radius)
    return {
        'sides': {'AB': AB, 'BC': BC, 'CA': CA},
        'angles': {'A': A, 'B': B, 'C': C}
    }

# ----------------------------------------
# 4. Triangle Measurement CSV Output
# ----------------------------------------

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

# ----------------------------------------
# 5. Placeholders for Street & Highpoint
# ----------------------------------------
# TODO: Integrate functions from streets.py (GPD operations, plotting)
# TODO: Integrate functions from highpoints.py (DEM masking, peak extraction)

# ----------------------------------------
# 6. Main Entrypoint
# ----------------------------------------
if __name__ == '__main__':
    # Example locs and triads (to be replaced or loaded from JSON)
    locs = {
        'K*': (38.892751, -77.051444),
        'J*': (38.889800, -77.036565),
        'C*': (38.889800, -77.009075),
        'O':  (38.889800, -77.051444)
    }
    triads = [('K*','O','J*'), ('K*','O','C*'), ('K*','J*','C*'), ('J*','O','C*')]
    print_triangles_csv(locs, triads)
