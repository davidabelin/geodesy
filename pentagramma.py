# C:\Users\David\Documents\Local_Python\geodesy\pentagramma.py
# Needs a geometry.py in same dir to import from
# Version 3.6

import math
import argparse
from geometry import direct_geodetic  # use accurate ellipsoid handling and forward azimuth

# Mean Earth radius in miles
R_MILES = 3958.7613
LAT = 38.8898
LON = -77.036565
bearing = 0.0

#φ = (1+math.sqrt(5))/2 radians
#σ₁ = arctan(√φ) = 0.904557 radians
#side₁ = σ₁ * R_MILES = 3580.924827 mi

#side₁ = (1+math.sqrt(5))/2 miles
#σ₁ = side₁ / R_MILES radians
#φ = tan^2(σ₁) radians^2
# ----------------------------------------------------------------------------
# Gauss's Pentagramma Mirificum: solve for tan^2 of the five sides
# ----------------------------------------------------------------------------

def solve_irregular_tan2(alpha, tol=1e-12, maxiter=100):
    """
    Given alpha = tan^2(sigma1), solve for delta = tan^2(sigma4)
    using bisection on f(delta) = alpha*beta*gamma*delta*epsilon - (3 + alpha+beta+gamma+delta+epsilon)
    where beta = (1+delta)/alpha, gamma=(1+alpha)/delta, epsilon=(1+gamma)/alpha.
    """
    def f(d):
        b = (1 + d) / alpha
        g = (1 + alpha) / d
        e = (1 + g) / alpha
        p = alpha * b * g * d * e
        s = 3 + alpha + b + g + d + e
        return p - s

    lo, hi = 1e-8, max(alpha * 10, 1.0)
    f_lo, f_hi = f(lo), f(hi)
    # expand hi until sign change
    while f_lo * f_hi > 0 and hi < 1e10:
        hi *= 10
        f_hi = f(hi)
    if f_lo * f_hi > 0:
        raise RuntimeError("Cannot bracket root for irregular pentagramma")
    for _ in range(maxiter):
        mid = 0.5 * (lo + hi)
        f_mid = f(mid)
        if abs(f_mid) < tol:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return 0.5 * (lo + hi)

def compute_side_angles(side1_miles, regular=False):
    """
    Compute the central angles sigma_i (radians) for each pentagon side.
      - regular: all equal = side1_miles / R_MILES
      - irregular: solve via Gauss's identities
    """
    sigma1 = side1_miles / R_MILES
    if regular:
        return [sigma1] * 5

    a = math.tan(sigma1) ** 2
    delta = solve_irregular_tan2(a)
    beta = (1 + delta) / a
    gamma = (1 + a) / delta
    epsilon = (1 + gamma) / a
    tan2s = [a, beta, gamma, delta, epsilon]
    # recover central angles
    return [math.atan(math.sqrt(t)) for t in tan2s]

def compute_pentagramma(lat1, lon1, bearing1, side1, regular=False):
    """
    Compute the vertices of a spherical pentagon with all right angles.
    Returns list of (lat, lon) for V1..V5, plus area in steradians and miles^2.
    """
    angles = compute_side_angles(side1, regular)
    verts = [(lat1, lon1)]
    lat, lon, brng = lat1, lon1, bearing1

    for sigma in angles:
        distance = sigma * R_MILES
        # use accurate ellipsoid for endpoints and forward azimuth
        lat2, lon2, fwd_az = direct_geodetic(lat, lon, brng, distance)
        verts.append((lat2, lon2))
        # interior angle at each vertex is 90°: new bearing = outgoing azimuth - 90°
        brng = (fwd_az - 90.0) % 360
        lat, lon = lat2, lon2

    # drop duplicate end-point
    verts = verts[:-1]
    # compute spherical excess: E = 2π - sum(sigma)
    excess = 2 * math.pi - sum(angles)
    area_mi2 = excess * (R_MILES ** 2)
    return verts, excess, area_mi2

def main():
    p = argparse.ArgumentParser(description="Pentagramma Mirificum on a sphere")
    p.add_argument('lat', type=float, help='Latitude of V1 (deg)')
    p.add_argument('lon', type=float, help='Longitude of V1 (deg)')
    p.add_argument('bearing', type=float, help='Initial bearing at V1 (deg)')
    p.add_argument('side', type=float, help='First side length (in miles)')
    p.add_argument('--regular', action='store_true', help='Regular (equal sides)')
    p.add_argument('--csv', action='store_true', help='CSV output')
    args = p.parse_args()

    verts, sr, mi2 = compute_pentagramma(args.lat, args.lon, args.bearing, args.side, args.regular)
    
    if args.csv:
        print('V,Lat,Lon')
        for i, (la, lo) in enumerate(verts, 1): print(f'{i},{la:.6f},{lo:.6f}')
    else:
        shape = 'Regular' if args.regular else 'Irregular'
        print(f"{shape} Pentagramma Vertices:")
        for i,(la,lo) in enumerate(verts,1): print(f"  V{i}: {la:.6f}°, {lo:.6f}°")
        print(f"Area: {sr:.6f} sr ({mi2:.2f} mi^2)")

if __name__ == '__main__':
    main()
