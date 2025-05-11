'''
diamond.py

Consolidated functions for computing diamond-shaped regions on a sphere:
  - solve_diamond_approx: approximate half-extents from area
  - solve_diamond_exact: numerically solve half-extents for exact area
  - diamond_area_spherical: compute exact area via two spherical triangles
  - diamond_corners: corner coordinates in degrees

Also includes supporting spherical-triangle-area and vector utilities.
'''
import math

# ----------------------------------------
# Constants
# ----------------------------------------
EARTH_RADIUS_MILES = 3958.8

# ----------------------------------------
# Vector and Spherical-Triangle Utilities
# ----------------------------------------
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

# ----------------------------------------
# Diamond Region Functions
# ----------------------------------------
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

# ----------------------------------------
# End of diamond.py
# ----------------------------------------
