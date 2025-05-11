import math
import numpy as np
import json

# Data
R = 3958.8  # miles Earth radius in km: 6371.0

locs = {
    'K*': (38.892751, -77.051444),
    'J*': (38.8898, -77.036565),
    'C*': (38.8898, -77.009075),
    'O': (38.8898, -77.051444)

    }

unused = {
    'KEYPHIP90':(38.89275207893436,-77.00888915208361),
    'KEYcos5PHIP90':(38.89275213758452,-77.00905106859983),
    'KEYcos5PHIP95':(38.889876771923994,-77.00921407839854),
    'KEYcos51PHIP951':(38.88981987089528,-77.00922713514876),
    'KEYPHIM67':(38.897701547539015,-77.03647762396668),
    'KEYPHIM90':(38.89275867351163,-77.03518665051968),
    'KEYsin67PHIM90':(38.89275884549353,-77.03647866519725),
    'CAPPHIP270':(38.88979457974987,-77.05153747626383),
    'CAPcos5PHIP270':(38.88979463839384,-77.05137556648924),
    'CAPcos5PHIP275':(38.89267012045754,-77.05121598221092),
    'CAPcos51PHIP2751':(38.89272703086533,-77.05120299258715),
    'CAProot3PHIP277':(38.89285296288821,-77.04095670094159),
    'CAP9100PHIP277':(38.8928378971026,-77.04079859604377)
    }

triad_names = [    
    ('K*', 'O', 'J*'),
    ('K*', 'O', 'C*'),
    ('K*', 'J*', 'C*'),
    ('J*', 'O', 'C*'),
    ]

# Functions
def haversine(lat1, lon1, lat2, lon2):
    """
    Calculate the great-circle distance between two points
    on the Earth's surface using the Haversine formula.

    Args:
        lat1: Latitude of point 1 (in degrees).
        lon1: Longitude of point 1 (in degrees).
        lat2: Latitude of point 2 (in degrees).
        lon2: Longitude of point 2 (in degrees).

    Returns:
        The great-circle distance in same units as R.
    """
    try:
        lat1_rad = math.radians(lat1)
        lon1_rad = math.radians(lon1)
        lat2_rad = math.radians(lat2)
        lon2_rad = math.radians(lon2)

        dlon = lon2_rad - lon1_rad
        dlat = lat2_rad - lat1_rad

        a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        distance = R * c
        return distance

    except Exception as e:
        print(f"Error in haversine(): {e}")
        print(f"d_lat:{dlat},\td_lon:{dlon}")
        return 0

def spherical_law_of_cosines_angles(a, b, c):
    """
    Calculates the interior angles of a spherical triangle given its side lengths.
    Uses the Spherical Law of Cosines for angles.

    Args:
        a: Length of side a (in miles or any consistent unit).
        b: Length of side b (in miles or any consistent unit).
        c: Length of side c (in miles or any consistent unit).

    Returns:
        A tuple containing the angles A, B, C (in degrees).
    """
    try:
        a_rad = a/R
        b_rad = b/R
        c_rad = c/R
        
        cos_A = (math.cos(a_rad) - math.cos(b_rad) * math.cos(c_rad)) / (math.sin(b_rad) * math.sin(c_rad))
        cos_B = (math.cos(b_rad) - math.cos(a_rad) * math.cos(c_rad)) / (math.sin(a_rad) * math.sin(c_rad))
        cos_C = (math.cos(c_rad) - math.cos(a_rad) * math.cos(b_rad)) / (math.sin(a_rad) * math.sin(b_rad))

        A = math.degrees(math.acos(cos_A))
        B = math.degrees(math.acos(cos_B))
        C = math.degrees(math.acos(cos_C))

        return A, B, C

    except Exception as e:
        print(f"Error in spherical_law_of_cosines_angles(): {e}")
        print(f"a_rad:{a_rad},\tb_rad:{b_rad},\tc_rad:{c_rad}")
        return 0, 0, 0

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

def print_results(results):
    #print("Angles (degrees):")
    for angle, value in results["angles"].items():
        #print(f"Angle {angle}: {value:.3f} degrees")
        print(value)
    #print("Side Lengths (miles):")
    for side, value in results["side_lengths"].items():
        #print(f"Side {side}: {value:.4f} miles")
        print(value)
        
def print_triangles(triangles):
    for triangle in triangles.keys():
        print(f"Triangle (A B C): {triangle}")
        print("Vertices:")
        for vert, values in triangles[triangle]["points"].items():
            print(f"  {vert}: {values["loc"]}")
        print("Angles (degrees):")
        for angle, value in triangles[triangle]["angles"].items():
            print(f"  {angle}: {value:.1f}")
        print("Side Lengths (miles):")
        for side, value in triangles[triangle]["sides"].items():
            print(f"  {side}: {value:.2f}")
     
def print_csv(triangles):
    for triangle in triangles.keys():
        A, B, C = triangle.split()
        tristring = str(f"'{A}', '{B}', '{C}'")
        for value in triangles[triangle]["angles"].values():
            tristring += f", {value:.3f}" # format to three digits!
        for value in triangles[triangle]["sides"].values():
            tristring += f", {value:.8f}" # format to eight digits!
        print(tristring)


if __name__ == "__main__":
    triangles = {}
    points = {}
    for A, B, C in triad_names:
        triad = (A, B, C)
        triad_key = f"{A} {B} {C}"  # Convert the tuple to a string key
        #print(f"triad name: {triad_key}")
        for vertname, p in zip(["A", "B", "C"], triad):
            points.update({vertname: {"loc":p, "lat":locs[p][0], "lon":locs[p][1]}})
        data = spherical_triangle_properties(*(locs[A], locs[B], locs[C]))
        triangles.update({triad_key: {"points":points, "angles":data["angles"], "sides":data["side_lengths"]}})

    #print_triangles(triangles)
    print_csv(triangles)

    # Save to JSON file
    if False: #input("Save JSON? (y/N)")=="y":
        with open("data/calc_from_measured.json", "w") as f:
            json.dump(triangles, f, indent=2)
        print("data/calc_from_measured.json")