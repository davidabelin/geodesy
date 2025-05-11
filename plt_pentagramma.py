import math
import matplotlib.pyplot as plt

# Try to import Cartopy; if unavailable, fall back to simple matplotlib
try:
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    CARTOPY = True
except ImportError:
    CARTOPY = False

from geometry import direct_geodetic

# Mean Earth radius in miles
R_MILES = 3958.7613

# ---- Pentagramma solver (reuse from pentagramma_fixed) ----
import math

def solve_irregular_tan2(a, tol=1e-12, maxiter=100):
    def f(d):
        b = (1 + d) / a
        g = (1 + a) / d
        e = (1 + g) / a
        return a * b * g * d * e - (3 + a + b + g + d + e)
    lo, hi = 1e-8, max(a * 10, 1.0)
    f_lo, f_hi = f(lo), f(hi)
    while f_lo * f_hi > 0 and hi < 1e10:
        hi *= 10
        f_hi = f(hi)
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
    sigma1 = side1_miles / R_MILES
    if regular:
        return [sigma1] * 5
    a = math.tan(sigma1) ** 2
    delta = solve_irregular_tan2(a)
    beta = (1 + delta) / a
    gamma = (1 + a) / delta
    epsilon = (1 + gamma) / a
    tan2s = [a, beta, gamma, delta, epsilon]
    return [math.atan(math.sqrt(t)) for t in tan2s]


def compute_pentagramma(lat1, lon1, bearing1, side1, regular=False):
    angles = compute_side_angles(side1, regular)
    verts = [(lat1, lon1)]
    lat, lon, brng = lat1, lon1, bearing1
    for sigma in angles:
        dist = sigma * R_MILES
        lat, lon, fwd = direct_geodetic(lat, lon, brng, dist)
        verts.append((lat, lon))
        brng = (fwd - 90.0) % 360
    return verts[:-1]

# ---- Plotting ----
if __name__ == '__main__':
    # Example parameters
    lat0, lon0 = 38.892751, -77.051444
    bearing0 = 0.0
    side0 = 71.565/360*2*math.pi * R_MILES
    reg = False
    verts = compute_pentagramma(lat0, lon0, bearing0, side0, regular=reg)
    lats = [v[0] for v in verts] + [verts[0][0]]
    lons = [v[1] for v in verts] + [verts[0][1]]

    if CARTOPY:
        #proj = ccrs.PlateCarree()
        proj = ccrs.Orthographic(39.0, -77.0)   # centred on (_°E, _°N)
        fig = plt.figure(figsize=(8, 6))
        ax = fig.add_subplot(1, 1, 1, projection=proj)
        # draw land and ocean for context
        ax.add_feature(cfeature.LAND.with_scale('50m'), facecolor='lightgray')
        ax.add_feature(cfeature.OCEAN.with_scale('50m'), facecolor='azure')
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
