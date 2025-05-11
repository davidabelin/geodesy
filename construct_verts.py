import math
from geometry import throw_endpoint, initial_bearing
PHIMR2 = math.sqrt(2)*((math.sqrt(5)-1)/2)
PHIPR2 = math.sqrt(2)*((math.sqrt(5)+1)/2)

# 1. Define fixed start point K:
latK, lonK = 38.892751, -77.051444

# Define latJ, constrain lonJ = 0.8 mi east of K:
latJ = 38.889800
_, lonJ, _ = throw_endpoint(latK, lonK, 0.8,  90,
                         units='miles', ellipsoid=True)

# Define point B = 12082 feet east of K:
latB = latK
_, lonB, _ = throw_endpoint(latK, lonK, PHIPR2, 90,
                         units='miles', ellipsoid=True)

# Define endpoint C: constrain latC = LatJ
latC = latJ

# 2. function to be bisection-optimized: angle K‑C‑B minus 90°
def f(lonC):
    bCK = initial_bearing(latC, lonC, latK, lonK)
    bCB = initial_bearing(latC, lonC, latB, lonB)
    angle_C = abs((bCK - bCB + 180) % 360 - 180)
    return angle_C - 90.0

# 3. initial bracket
lonC_low, lonC_high = lonJ + 5e-5, lonB - 5e-5

# 4. solve for lonC
from scipy.optimize import bisect
lonC = bisect(f, lonC_low, lonC_high, xtol=5e-10)

# 5. construct C and define E at same lon:
C = (latC, lonC)
E = (latK, lonC)

# 6. compute α = ∠BKC
#    bearing from K to B, and from K to C, then their difference:
bKB = initial_bearing(latK, lonK, latB, lonB)
bKC = initial_bearing(latK, lonK, latC, lonC)
alpha = abs((bKB - bKC + 180) % 360 - 180)

#print(f"Point K = ({latK:.8f}, {lonK:.8f})")
#print(f"Point J = ({latJ:.8f}, {lonJ:.8f})")
#print(f"Point B = ({latB:.8f}, {lonB:.8f})")
#print(f"Point C = ({C[0]:.8f}, {C[1]:.8f})")
#print(f"Point E = ({E[0]:.8f}, {E[1]:.8f})")
print(f"Right‐angle check at C: angle ∠KCB = {f(lonC)+90:.8f}°")
print(f"Computed angle at K, α = ∠BKC = {alpha:.8f}°\n")
print(f"K, {latK:.8f}, {lonK:.8f}")
print(f"J, {latJ:.8f}, {lonJ:.8f}")
print(f"B, {latB:.8f}, {lonB:.8f}")
print(f"C, {C[0]:.8f}, {C[1]:.8f}")
print(f"E, {E[0]:.8f}, {E[1]:.8f}")

'''
### Repeat process to solve for Point W constrained to Wlon = Jlon
# Define point W = phim*r2 miles from K, with unknown bearing and Wlon=Jlon:
# Starting guess, to be optimized:
# cosKW = 0.8 / PHIMR2, angle KW = beta = acos(...), latW = PHIMR2 * sin(beta)
beta = math.acos(0.8 / PHIMR2)
latW = PHIMR2 * math.sin(beta)
lonW = lonJ

# 2. function for bisection-optimization: bearingKB = 90° minus ∠BKW
def fW(latW):
    bKB = initial_bearing(latK, lonK, latB, lonB)
    bKW = initial_bearing(latK, lonK, latW, lonW)
    angleW = abs((bKB - bKW + 180) % 360 - 180)
    return angleW

# 3. initial bracket
latW_low, latW_high = latW + 5e-5, latW - 5e-5

# 4. solve for lonC
#from scipy.optimize import bisect
#latW = bisect(fW, latW_low, latW_high, xtol=5e-10)

# 5. construct C and define E at same lon:
W = (latW, lonW)

# 6. Check that beta = ∠BKW:
bKB = initial_bearing(latK, lonK, latB, lonB)
bKW = initial_bearing(latK, lonK, latW, lonW)
beta_check = abs((bKB - bKW + 180) % 360 - 180)

print(f"Beta check at K: angle ∠BKW = {beta_check:.8f}")
print(f"Computed latitude for W if beta = ∠BKW = {fW(latW):.8f}°\n")
print(f"W, {latW:.8f}, {lonW:.8f}")
print(f"D, {latK:.8f}, {lonJ:.8f}")

print(f"\nPHIMR2 = {PHIMR2:.8f}")
print(f"PHIPR2 = {PHIPR2:.8f}")
print(f"PHIM = {(math.sqrt(5)-1)/2:.8f}")
print(f"PHIP = {(math.sqrt(5)+1)/2:.8f}")
'''