import math
from geocalc import throw_endpoint, bearing

# 1. fixed data:
latK, lonK = 38.892751, -77.051444

# J = 0.8 mi east of K:
_, lonJ = throw_endpoint(latK, lonK, 0.8,  90, units='miles')

# B = 12082 ft east of K:
_, lonB = throw_endpoint(latK, lonK, 12082, 90, units='feet')

latJ = 38.889800
latC = latJ

# 2. objective function: angle K‑C‑B minus 90° (unchanged)
def f(lonC):
    bCK = bearing((latC, lonC), (latK, lonK))
    bCB = bearing((latC, lonC), (latK, lonB))
    angle = abs((bCK - bCB + 180) % 360 - 180)
    return angle - 90.0

# 3. initial bracket
lonC_low, lonC_high = lonJ + 1e-4, lonB - 1e-4

# 4. solve for lonC
from scipy.optimize import bisect
lonC = bisect(f, lonC_low, lonC_high, xtol=1e-9)

# 5. build C and E:
C = (latC, lonC)
E = (latK, lonC)

# 6. compute α = ∠BKC
#    bearing from K to B, and from K to C, then their difference:
bKB = bearing((latK, lonK), (latK, lonB))
bKC = bearing((latK, lonK), (latC, lonC))
alpha = abs((bKB - bKC + 180) % 360 - 180)

print(f"Point C = {C}")
print(f"Point E = {E}")
print(f"Right‐angle check ∠KCB = {f(lonC)+90:.8f}°")
print(f"Computed α = ∠BKC = {alpha:.8f}°")
