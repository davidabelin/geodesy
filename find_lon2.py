import math
from geometry import find_longitudes_for_known_latitude_and_distance, inverse_geodetic # (assuming it's in geometry.py)
PHIMR2 = math.sqrt(2)*((math.sqrt(5)-1)/2)
PHIPR2 = math.sqrt(2)*((math.sqrt(5)+1)/2)

lat1 = 38.892751  # degrees
lon1 = -77.051444 # degrees
distance = PHIPR2 # miles
target_lat2 = 38.8898 # degrees

possible_lon2s = find_longitudes_for_known_latitude_and_distance(
    lat1, lon1, distance, target_lat2, ellipsoid=True
)

if possible_lon2s:
    print(f"Found {len(possible_lon2s)} solution(s) for lon2:")
    for lon2 in possible_lon2s:
        print(f"  Longitude: {lon2:.8f}°")
        # You can verify the solution:
        # _, _, dist_check = inverse_geodetic(lat1, lon1, target_lat2, lon2, unit='miles', ellipsoid=True)
        # print(f"    Verification: distance = {dist_check:.4f} miles (target was {distance:.4f})")
else:
    print("No solution found for lon2.")

