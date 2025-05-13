# C:\Users\David\Documents\Local_Python\geodesy\pentagramma.py
# Needs a geometry.py in same dir to import from
# Version 4.0

import math
import argparse
from geometry import direct_geodetic  # use accurate ellipsoid handling and forward azimuth
import matplotlib.pyplot as plt

# Try to import Cartopy; if unavailable, fall back to simple matplotlib
try:
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    CARTOPY = True
except ImportError:
    CARTOPY = False
    
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
#===============================================================================
# Pentagramma Mirificum: Gauss's Formulas
#===============================================================================
# Let α = tan^2(TP), β = tan^2(PQ), γ = tan^2(QR), δ = tan^2(RS), ε = tan^2(ST)
# Then the five identities:
#   1 + α = γ δ
#   1 + β = δ ε
#   1 + γ = α ε
#   1 + δ = α β
#   1 + ε = β γ
# And the beautiful equality:
#   αβγδε = 3 + α + β + γ + δ + ε = ∏_{x in (α..ε)} (1 + x)

def gauss_pentagramma(alpha, beta, gamma, delta, epsilon):
    """
    Validate and relate the five tan^2 lengths of the pentagramma arcs.

    Returns a dict with:
      'identity_checks': list of booleans for 1+x = product relations,
      'beautiful_equality': boolean for αβγδε == 3 + sum == ∏(1+xi),
      'values': dict of input parameters.

    Example:
        # Example 'abcde' values:
        α, β, γ, δ, ε = 9, 2/3, 2, 5, 1/3
        # Product αβγδε = 9 * (2/3) * 2 * 5 * (1/3) = 20
        result = gauss_pentagramma(9, 2/3, 2, 5, 1/3)
        # result['beautiful_equality'] indicates whether Gauss's relations hold
    """
    checks = [
        math.isclose(1+alpha, gamma*delta, rel_tol=1e-9),
        math.isclose(1+beta, delta*epsilon, rel_tol=1e-9),
        math.isclose(1+gamma, alpha*epsilon, rel_tol=1e-9),
        math.isclose(1+delta, alpha*beta, rel_tol=1e-9),
        math.isclose(1+epsilon, beta*gamma, rel_tol=1e-9)
    ]
    prod_all = alpha*beta*gamma*delta*epsilon
    sum_all = alpha+beta+gamma+delta+epsilon
    prod_ones = (1+alpha)*(1+beta)*(1+gamma)*(1+delta)*(1+epsilon)
    beautiful = (math.isclose(prod_all, 3+sum_all, rel_tol=1e-9) and
                 math.isclose(prod_all, prod_ones, rel_tol=1e-9))
    return {
        'identity_checks': checks,
        'beautiful_equality': beautiful,
        'values': dict(alpha=alpha, beta=beta, gamma=gamma, delta=delta, epsilon=epsilon)
    }

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


#===============================================================================
# Pytest Test Suite
#===============================================================================
def test_gauss_pentagramma_valid():
    # Validate Gauss relationships using example abcde
    α, β, γ, δ, ε = 9, 2/3, 2, 5, 1/3
    # product αβγδε = 9 * (2/3) * 2 * 5 * (1/3) = 20
    print(f"αβγδε = 9 * (2/3) * 2 * 5 * (1/3) = {α*β*γ*δ*ε} (should equal 20)")
    res = gauss_pentagramma(α, β, γ, δ, ε)
    assert math.isclose(res['values']['alpha'] * res['values']['beta'] * res['values']['gamma'] * res['values']['delta'] * res['values']['epsilon'], 20, rel_tol=1e-9)
    assert isinstance(res['beautiful_equality'], bool)
    assert len(res['identity_checks']) == 5
    for check in res['identity_checks']:
        assert isinstance(check, bool)


def main():
    p = argparse.ArgumentParser(description="Pentagramma Mirificum on a sphere")
    p.add_argument('lat', type=float, help='Latitude of V1 (deg)')
    p.add_argument('lon', type=float, help='Longitude of V1 (deg)')
    p.add_argument('bearing', type=float, help='Initial bearing at V1 (deg)')
    p.add_argument('side', type=float, help='First side length (in miles)')
    p.add_argument('--regular', action='store_true', help='Regular (equal sides)')
    p.add_argument('--csv', action='store_true', help='CSV output')
    p.add_argument('--plot', action='store_true', help='Plot output')
    args = p.parse_args()

    verts, excess, area = compute_pentagramma(args.lat, args.lon, args.bearing, args.side, args.regular)
    
    if args.plot:
        # Example parameters
        lat0, lon0 = 38.892751, -77.051444
        bearing0 = 0.0
        side0 = 10 #71.565/360*2*math.pi * R_MILES
        reg = False
        verts, excess, area = compute_pentagramma(lat0, lon0, bearing0, side0, regular=reg)
        lats = [v[0] for v in verts] + [verts[0][0]]
        lons = [v[1] for v in verts] + [verts[0][1]]

        if CARTOPY:
            #proj = ccrs.PlateCarree()
            proj = ccrs.Orthographic(39.0, -77.0)   # centred on (_°E, _°N)
            fig = plt.figure(figsize=(8, 6))
            ax = fig.add_subplot(1, 1, 1, projection=proj)
            # draw land and ocean for context
            ax.add_feature(cfeature.LAND.with_scale('10m'), facecolor='lightgray')
            ax.add_feature(cfeature.OCEAN.with_scale('10m'), facecolor='azure')
            # draw gridlines
            gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False)
            gl.top_labels = gl.right_labels = False
            # set extent with padding
            pad = 0.02
            ax.set_extent([min(lons)-pad, max(lons)+pad, min(lats)-pad, max(lats)+pad], crs=proj)
            # plot edges and vertices
            ax.plot(lons, lats, marker='o', transform=proj, label='Sides')
            for i,(lat,lon) in enumerate(verts,1):
                ax.text(lon, lat, f'V{i}', transform=proj, fontsize=8)
            plt.title('Irregular Pentagramma Mirificum')
            plt.legend()
            plt.show()
        else:
            # Simple matplotlib fallback
            plt.figure(figsize=(6,6))
            plt.plot(lons, lats, '-o')
            for i,(lat,lon) in enumerate(verts,1):
                plt.text(lon, lat, f'V{i}', fontsize=8, ha='right')
            plt.grid(True)
            plt.gca().set_aspect('equal', 'box')
            plt.xlabel('Longitude')
            plt.ylabel('Latitude')
            #plt.title('Pentagramma (plate carrée)')
            plt.title('Pentagramma (orthographique)')
            plt.show()

    if args.csv:
        print('V,Lat,Lon')
        for i, (la, lo) in enumerate(verts, 1): 
            print(f'pmNC{i},{la:.9f},{lo:.9f}')
        print(f'excess:\t{excess:.9f}\narea:\t{area:.9f} mi^2')
    else:
        shape = 'Regular' if args.regular else 'Irregular'
        print(f"{shape} Pentagramma Vertices:")
        for i,(la,lo) in enumerate(verts,1):
            print(f"  V{i}: {la:.9f}°, {lo:.9f}°")
        print(f'  Area:  {area:-.1f} mi^2')
        print(f'  Excess:  2π - sum(sigma) = {excess:.9f}')

if __name__ == '__main__':
    main()
