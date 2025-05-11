import math
from scipy.optimize import fsolve
from geometry import (direct_geodetic, inverse_geodetic, EARTH_RADIUS_MILES, 
                     normalize_longitude, DEG2RAD, RAD2DEG)
                     
# Given V1 (example values, can be changed)
lat1_deg = 38.892751  # Latitude of V1 (e.g., Point K from construct_verts.py)
lon1_deg = -77.051444 # Longitude of V1 (e.g., Point K from construct_verts.py)

# Given information to define V2 relative to V1
dist_V1_V2_miles = 0.8
bearing_V1_V2_deg = 90.0  # East

# Constraint for V3: distance from V1
dist_V1_V3_miles = 0.87403

# --- Step 1: Determine V2 coordinates (lat2, lon2) ---
# This also gives us lon3 because the problem states lon3 = lon2.
# For short distances and a 90-degree bearing, lat2 will be very close to lat1.
lat2_deg, lon2_deg, forward_azimuth_V1_V2_return = direct_geodetic(
    lat1_deg, lon1_deg, bearing_V1_V2_deg, dist_V1_V2_miles, unit='miles', ellipsoid=True
)
lon3_deg = lon2_deg  # Constraint: V3 has the same longitude as V2

print(f"V1: (lat={lat1_deg:.8f}°, lon={lon1_deg:.8f}°)")
print(f"V2 calculated: (lat={lat2_deg:.8f}°, lon={lon2_deg:.8f}°) based on V1, dist={dist_V1_V2_miles} mi, bearing={bearing_V1_V2_deg}°")
print(f"  Constraint for V3: lon3 = {lon3_deg:.8f}°")
print(f"  Constraint for V3: dist(V1, V3) = {dist_V1_V3_miles} miles")

# --- Step 2: Define objective function for fsolve to find lat3 ---
# We want: distance_calculated_by_inverse_geodetic(V1, (lat3_candidate, lon3)) - target_dist_V1_V3_miles = 0
def objective_func_lat3(lat3_candidate_deg_arr, p_lat1_deg, p_lon1_deg, p_lon3_deg, p_target_dist_miles):
    lat3_cand_deg = lat3_candidate_deg_arr[0]
    
    # Penalize latitudes outside the valid range [-90, 90] to guide solver
    if not (-90.0 <= lat3_cand_deg <= 90.0):
        return 1e9 * (abs(lat3_cand_deg) - 90) # Large penalty

    # Calculate distance from V1 to the candidate V3(lat3_cand_deg, p_lon3_deg)
    _, _, calculated_dist_miles = inverse_geodetic(
                                        p_lat1_deg, p_lon1_deg, 
                                        lat3_cand_deg, p_lon3_deg, 
                                        unit='miles', ellipsoid=True
    )
    return calculated_dist_miles - p_target_dist_miles

# --- Step 3: Calculate initial guesses for lat3 using a spherical Earth model ---
initial_guesses_deg = []
lat1_rad = lat1_deg * DEG2RAD
lon1_rad = lon1_deg * DEG2RAD
lon3_rad = lon3_deg * DEG2RAD

# Calculate the difference in longitude, normalized to [-pi, pi] for spherical formula
delta_lon_deg = normalize_longitude(lon3_deg - lon1_deg)
delta_lon_rad = delta_lon_deg * DEG2RAD

sigma_13_rad = dist_V1_V3_miles / EARTH_RADIUS_MILES # Angular distance V1-V3

# Spherical law of cosines for sides: cos(c) = cos(a)cos(b) + sin(a)sin(b)cos(C)
# Here, applied to find lat3: cos(sigma_13) = sin(lat1)sin(lat3) + cos(lat1)cos(lat3)cos(delta_lon)
# This is of the form C_sph = A_sph*sin(lat3_rad) + B_sph*cos(lat3_rad)
A_sph = math.sin(lat1_rad)
B_sph = math.cos(lat1_rad) * math.cos(delta_lon_rad)
C_sph = math.cos(sigma_13_rad)

R_sph_sq = A_sph**2 + B_sph**2

if R_sph_sq < 1e-12: # Avoid division by zero if A_sph and B_sph are both near zero
    # This happens if lat1 is on equator (A_sph=0) AND delta_lon is +/-90 deg (B_sph=0).
    # In this specific case, C_sph = cos(sigma_13_rad) must also be 0 (i.e., sigma_13_rad = pi/2).
    # If so, any lat3 is a solution spherically. We'll use fallback guesses.
    print("Warning: Spherical guess calculation encountered a degenerate case (R_sph_sq is near zero).")
    initial_guesses_deg = [lat1_deg, 0.0, -lat1_deg] # Fallback guesses
else:
    R_sph = math.sqrt(R_sph_sq)
    # We need to solve C_sph = R_sph * cos(lat3_rad - alpha_rad_sph)
    # cos(lat3_rad - alpha_rad_sph) = C_sph / R_sph
    cos_arg = C_sph / R_sph

    if abs(cos_arg) <= 1.0 + 1e-9: # Allow for slight floating point inaccuracies
        cos_arg = max(-1.0, min(1.0, cos_arg)) # Clamp to [-1, 1]
        alpha_rad_sph = math.atan2(A_sph, B_sph) # alpha for R*cos(x-alpha) form
        acos_term_rad = math.acos(cos_arg)

        # Two potential solutions for lat3_rad from spherical model
        lat3_rad_guess1 = alpha_rad_sph + acos_term_rad
        lat3_rad_guess2 = alpha_rad_sph - acos_term_rad
        
        for lat3_rad_g in [lat3_rad_guess1, lat3_rad_guess2]:
            lat_deg_g = lat3_rad_g * RAD2DEG
            # Ensure guess is a valid latitude; solutions "over the pole" are not valid for fixed longitude
            if -90.0 <= lat_deg_g <= 90.0:
                initial_guesses_deg.append(lat_deg_g)
    else:
        print("No spherical solution for lat3 initial guess (acos argument out of range).")

if not initial_guesses_deg: # If no spherical guesses were valid or found
    initial_guesses_deg = [lat1_deg + (dist_V1_V3_miles/69.0), lat1_deg - (dist_V1_V3_miles/69.0)] # Approx 1 deg lat per 69 miles

initial_guesses_deg = sorted(list(set(initial_guesses_deg))) # Unique, sorted guesses
print(f"Initial spherical guesses for lat3: {[f'{g:.8f}' for g in initial_guesses_deg]}°")

# --- Step 4: Solve for lat3 using fsolve for each initial guess ---
final_solutions = []
for guess_lat3_deg in initial_guesses_deg:
    lat3_sol_arr, _, ier, msg = fsolve(
        objective_func_lat3,
        x0=[guess_lat3_deg],
        args=(lat1_deg, lon1_deg, lon3_deg, dist_V1_V3_miles),
        full_output=True,
        xtol=1e-10 # Tolerance for fsolve convergence
    )
    if ier == 1: # fsolve found a solution
        solved_lat3_deg = lat3_sol_arr[0]
        # Verify the solution's distance and validity
        achieved_dist_V1_V3 = objective_func_lat3([solved_lat3_deg], lat1_deg, lon1_deg, lon3_deg, dist_V1_V3_miles) + dist_V1_V3_miles
        
        is_distinct_and_valid = True
        if not (-90.0 <= solved_lat3_deg <= 90.0): is_distinct_and_valid = False
        if abs(achieved_dist_V1_V3 - dist_V1_V3_miles) > dist_V1_V3_miles * 1e-7: is_distinct_and_valid = False # Check relative tolerance
        
        for existing_sol in final_solutions: # Ensure solution is not a duplicate
            if abs(existing_sol['lat3'] - solved_lat3_deg) < 1e-7: # Threshold for distinctness
                is_distinct_and_valid = False; break
        
        if is_distinct_and_valid:
            bearing_V1_V3_deg, _, _ = inverse_geodetic(lat1_deg, lon1_deg, solved_lat3_deg, lon3_deg, unit='miles', ellipsoid=True)
            angle_V3_V1_V2 = abs(bearing_V1_V3_deg - bearing_V1_V2_deg)
            if angle_V3_V1_V2 > 180.0: angle_V3_V1_V2 = 360.0 - angle_V3_V1_V2 # Smallest angle
                
            final_solutions.append({
                "lat3": solved_lat3_deg, "lon3": lon3_deg,
                "bearing_V1_V3": bearing_V1_V3_deg, "dist_V1_V3_achieved": achieved_dist_V1_V3,
                "angle_V3_V1_V2_at_V1": angle_V3_V1_V2
            })
    # else: print(f"fsolve did not converge for guess {guess_lat3_deg:.8f}. Message: {msg}")

# --- Step 5: Print results ---
if final_solutions:
    print(f"\nFound {len(final_solutions)} solution(s) for V3(lat3, lon3):")
    for i, sol in enumerate(final_solutions):
        print(f"  Solution {i+1}:")
        print(f"    V3 coordinates: (lat={sol['lat3']:.8f}°, lon={sol['lon3']:.8f}°)")
        print(f"    Bearing V1->V3: {sol['bearing_V1_V3']:.8f}°")
        print(f"    Distance V1-V3 (achieved): {sol['dist_V1_V3_achieved']:.8f} miles (target: {dist_V1_V3_miles})")
        print(f"    Angle V3-V1-V2 (at V1): {sol['angle_V3_V1_V2_at_V1']:.8f}°")
        # The problem mentioned "90 minus the inner angle at V1" for the bearing.
        # If bearing_V1_V2 is indeed 90, then this value is 90 - angle_V3_V1_V2_at_V1.
        # This equals bearing_V1_V3 if bearing_V1_V3 <= 90.
        print(f"    Value '90 - Angle_V3_V1_V2_at_V1': {(90.0 - sol['angle_V3_V1_V2_at_V1']):.8f}°")
else:
    print("\nNo solution found for V3 that meets all criteria.")