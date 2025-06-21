"""
azdist_triangles.py   v1.1

Example usage and result:
>
python azdist_triangles.py --out data/tri_out_ell.csv data/MixPnts.kml data/TriangleLines_ell.kml

Loaded 199 points from data/MixPnts.kml.
Analyzing 1293699 unique triangles using Ellipsoidal model...
Processing in parallel with up to 16 workers (above threshold of 20000)...
Processing:   0%|              | 0/1293699 [00:00<?, ?it/s]Pr

"""
import argparse
import csv
import math
from itertools import combinations
import simplekml
from pyproj import Geod
from tqdm import tqdm
import xml.etree.ElementTree as ET
import concurrent.futures
from functools import partial
import os

# ===============================================================================
# Constants and Geodetic Functions
# ===============================================================================
_GEOID = Geod(ellps='WGS84')
PHI = (1 + math.sqrt(5)) / 2
DEG2RAD = math.pi / 180.0
RAD2DEG = 180.0 / math.pi
EARTH_RADIUS_MILES = 3958.756

def inverse_geodetic(p1_coords, p2_coords, ellipsoid: bool):
    if ellipsoid:
        az1, az2, dist_m = _GEOID.inv(p1_coords[1], p1_coords[0], p2_coords[1], p2_coords[0])
        dist_miles = dist_m / 1609.344
        return az1 % 360, az2 % 360, dist_miles
    
    phi1r, phi2r = p1_coords[0] * DEG2RAD, p2_coords[0] * DEG2RAD
    dlam = (p2_coords[1] - p1_coords[1]) * DEG2RAD
    cos_sigma = math.sin(phi1r) * math.sin(phi2r) + math.cos(phi1r) * math.cos(phi2r) * math.cos(dlam)
    sigma = math.acos(max(-1, min(1, cos_sigma)))
    dist = EARTH_RADIUS_MILES * sigma
    y1 = math.sin(dlam) * math.cos(phi2r)
    x1 = math.cos(phi1r) * math.sin(phi2r) - math.sin(phi1r) * math.cos(phi2r) * math.cos(dlam)
    az1 = (math.atan2(y1, x1) * RAD2DEG + 360) % 360
    y2 = math.sin(-dlam) * math.cos(phi1r)
    x2 = math.cos(phi2r) * math.sin(phi1r) - math.sin(phi2r) * math.cos(phi1r) * math.cos(dlam)
    az2 = (math.atan2(y2, x2) * RAD2DEG + 360) % 360
    return az1, az2, dist

def calculate_triangle_properties(p1_coords, p2_coords, p3_coords, ellipsoid: bool):
    _, _, side_ab = inverse_geodetic(p1_coords, p2_coords, ellipsoid)
    _, _, side_bc = inverse_geodetic(p2_coords, p3_coords, ellipsoid)
    _, _, side_ca = inverse_geodetic(p3_coords, p1_coords, ellipsoid)
    
    def clamp(x): return max(-1.0, min(1.0, x))
    try:
        angle_a = math.degrees(math.acos(clamp((side_bc**2 + side_ca**2 - side_ab**2) / (2 * side_bc * side_ca))))
        angle_b = math.degrees(math.acos(clamp((side_ca**2 + side_ab**2 - side_bc**2) / (2 * side_ca * side_ab))))
        angle_c = math.degrees(math.acos(clamp((side_ab**2 + side_bc**2 - side_ca**2) / (2 * side_ab * side_bc))))
    except (ValueError, ZeroDivisionError):
        return None
    return {"sides": [side_ab, side_bc, side_ca], "angles": [angle_a, angle_b, angle_c]}

# ===============================================================================
# KML Parsing
# ===============================================================================
def load_points_from_kml(path: str) -> dict[str, tuple[float, float]]:
    ns = {'kml': 'http://www.opengis.net/kml/2.2'}
    tree = ET.parse(path)
    root = tree.getroot()
    pts = {}
    for pm in root.findall('.//kml:Placemark', ns):
        name = pm.findtext('kml:name', default='', namespaces=ns).strip()
        coords_element = pm.find('.//kml:Point/kml:coordinates', ns)
        if name and coords_element is not None and coords_element.text:
            lon_str, lat_str, *_ = coords_element.text.strip().split(',')
            pts[name] = (float(lat_str), float(lon_str))
    print(f"Loaded {len(pts)} points from {path}.")
    return pts

# ===============================================================================
# Triangle Definitions and Matching Logic
# ===============================================================================
TRIANGLE_DEFINITIONS = {
    "3-4-5":       {"group": "3-4-5 Right Triangles", "angles": [36.87, 53.13, 90.0], "side_ratios": [3.0, 4.0, 5.0]},
    "30-60-90":    {"group": "30-60-90 Right Triangles", "angles": [30.0, 60.0, 90.0], "side_ratios": [1.0, math.sqrt(3), 2.0]},
    "Equilateral": {"group": "Equilateral Triangles (60-60-60)", "angles": [60.0, 60.0, 60.0], "side_ratios": [1.0, 1.0, 1.0]},
    "45-45-90":    {"group": "45-45-90 Right Triangles", "angles": [45.0, 45.0, 90.0], "side_ratios": [1.0, 1.0, math.sqrt(2)]},
    "18-72-90":    {"group": "18-72-90 Right Triangles", "angles": [18.0, 72.0, 90.0], "side_ratios": [math.sin(math.radians(18)), math.sin(math.radians(72)), 1.0]},
    "36-54-90":    {"group": "36-54-90 Right Triangles", "angles": [36.0, 54.0, 90.0], "side_ratios": [math.sin(math.radians(36)), math.sin(math.radians(54)), 1.0]},
    "Golden T": {"group": "Golden Triangles (72-72-36)", "angles": [36.0, 72.0, 72.0], "side_ratios": [1.0, PHI, PHI]},
    "Golden G": {"group": "Golden Gnomons (36-36-108)", "angles": [36.0, 36.0, 108.0], "side_ratios": [1.0, 1.0, 1/PHI]},
    "Kepler": {"group": "Kepler Right Triangles (1:sqrt(phi):phi)", "angles": [31.72, 58.28, 90.0], "side_ratios": [1, math.sqrt(PHI), PHI]},
    "1-2-r5":      {"group": "1-2-sqrt(5) Right Triangles", "angles": [26.57, 63.43, 90.0], "side_ratios": [1.0, 2.0, math.sqrt(5)]},
}
KML_STYLES = {
    "3-4-5": "FF0077FF", "30-60-90": "FF00FF00", "Equilateral": "FF33FF33","45-45-90": "FFFF0000",
    "18-72-90": "FFFF00CC", "36-54-90": "FFFF10C1", "Golden T": "FF00D7FF", "Golden G": "FFFF8C00",
    "Kepler": "FFEE82EE", "1-2-r5": "FFFF00FF", "Default": "FFFFFFFF",
}

def find_triangle_match(properties, angle_tol=1.5, ratio_tol=0.015):
    if not properties: return None, None, None
    
    sorted_angles = sorted(properties['angles'])
    sorted_sides = sorted(properties['sides'])
    
    for key, definition in TRIANGLE_DEFINITIONS.items():
        match_type = None
        if all(abs(a - b) < angle_tol for a, b in zip(sorted_angles, sorted(definition['angles']))):
            match_type = "angles"
        else:
            if sorted_sides[0] > 1e-9:
                measured_ratios = [s / sorted_sides[0] for s in sorted_sides]
                def_ratios = sorted(definition['side_ratios'])
                def_ratios_norm = [r / def_ratios[0] for r in def_ratios]
                if all(abs(a - b) < ratio_tol for a, b in zip(measured_ratios, def_ratios_norm)):
                    match_type = "side_ratios"
        
        if match_type:
            def_side_sum = sum(definition['side_ratios'])
            measured_side_sum = sum(sorted_sides)
            factor = measured_side_sum / def_side_sum if def_side_sum > 0 else 0
            return key, match_type, factor
                
    return None, None, None

# ===============================================================================
# Parallel Processing Worker Function
# ===============================================================================
def process_single_triad(triad_of_points, ellipsoid: bool):
    """
    Worker function for parallel processing. Takes one triangle, calculates its
    properties, and checks for a match. Returns a match dictionary or None.
    This must be a top-level function for multiprocessing.
    """
    (p1_name, p1_coords), (p2_name, p2_coords), (p3_name, p3_coords) = triad_of_points
    
    properties = calculate_triangle_properties(p1_coords, p2_coords, p3_coords, ellipsoid=ellipsoid)
    match_key, match_reason, match_factor = find_triangle_match(properties)
    
    if match_key:
        return {
            "points": (p1_name, p2_name, p3_name), "coords": (p1_coords, p2_coords, p3_coords),
            "key": match_key, "reason": match_reason, "factor": match_factor, "properties": properties
        }
    return None

# ===============================================================================
# Main Execution and Output
# ===============================================================================
def main():
    parser = argparse.ArgumentParser(description="Find and classify special triangles from a KML file.")
    parser.add_argument("input_kml", help="Input KML file with placemarks.")
    parser.add_argument("output_kml", help="Output KML file for matched triangles.")
    parser.add_argument("--out-csv", help="Optional CSV file for saving matched triangle data.", default=None)
    parser.add_argument('--spherical', action='store_true', help='Use spherical calculations. Default is ellipsoidal (WGS84).')
    args = parser.parse_args()

    use_ellipsoid = not args.spherical
    points = load_points_from_kml(args.input_kml)
    if len(points) < 3:
        print("Error: Need at least 3 points in the KML file."); return

    all_triads = list(combinations(points.items(), 3))
    total_triads = len(all_triads)
    
    print(f"Analyzing {total_triads} unique triangles using {'Ellipsoidal' if use_ellipsoid else 'Spherical'} model...")

    matched_triangles = []
    # Heuristic copied from azdist_filter.py: only use parallel processing for larger jobs.
    PARALLEL_THRESHOLD = 20000 

    if total_triads < PARALLEL_THRESHOLD:
        print(f"Processing in a single thread (below threshold of {PARALLEL_THRESHOLD})...")
        for triad in tqdm(all_triads, desc="Processing"):
            result = process_single_triad(triad, ellipsoid=use_ellipsoid)
            if result:
                matched_triangles.append(result)
    else:
        num_workers = os.cpu_count() or 1
        print(f"Processing in parallel with up to {num_workers} workers (above threshold of {PARALLEL_THRESHOLD})...")
        # Use functools.partial to preset the 'ellipsoid' argument for the worker function
        worker_func = partial(process_single_triad, ellipsoid=use_ellipsoid)
        with concurrent.futures.ProcessPoolExecutor(max_workers=num_workers) as executor:
            # The 'map' function distributes the work and we collect the results
            results_iterator = executor.map(worker_func, all_triads)
            # Filter out the None results from triads that didn't match
            matched_triangles = [r for r in tqdm(results_iterator, total=total_triads, desc="Processing") if r is not None]

    print(f"Found {len(matched_triangles)} triangles matching defined criteria.")
    if not matched_triangles: return

    # Write KML and CSV Output
    kml = simplekml.Kml(name="Special Triangles")
    kml_folders = {}
    csv_rows = []

    for match in sorted(matched_triangles, key=lambda m: m['key']):
        key, definition = match['key'], TRIANGLE_DEFINITIONS[match['key']]
        group_name = definition['group']
        if group_name not in kml_folders: kml_folders[group_name] = kml.newfolder(name=group_name)
        
        folder = kml_folders[group_name]
        p_names, p_coords, props = match['points'], match['coords'], match['properties']
        
        poly = folder.newpolygon(name=f"{p_names[0]}-{p_names[1]}-{p_names[2]}")
        poly.outerboundaryis = [ (c[1], c[0]) for c in p_coords ] + [ (p_coords[0][1], p_coords[0][0]) ]
        
        color = KML_STYLES.get(key, KML_STYLES["Default"])
        poly.style.polystyle.color = simplekml.Color.changealphaint(100, color)
        poly.style.linestyle.color, poly.style.linestyle.width = color, 2
        
        desc = f"""
        <b>Match Type:</b> {key} (by {match['reason']})<br>
        <b>Proportionality Factor:</b> {match['factor']:.4f}<br><hr>
        <b>Vertices:</b> {', '.join(p_names)}<br>
        <b>Angles:</b> {', '.join(f'{a:.2f}°' for a in props['angles'])}<br>
        <b>Side Lengths (miles):</b> {', '.join(f'{s:.4f}' for s in props['sides'])}
        """
        poly.description = f"<![CDATA[{desc}]]>"

        csv_rows.append([
            *p_names, key, match['reason'], group_name, match['factor'],
            *props['angles'], *props['sides']
        ])

    kml.save(args.output_kml); print(f"Wrote {len(matched_triangles)} triangles to {args.output_kml}")
    if args.out_csv:
        with open(args.out_csv, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['p1', 'p2', 'p3', 'match_type', 'match_reason', 'group', 'proportionality_factor', 'angle1', 'angle2', 'angle3', 'side1', 'side2', 'side3'])
            writer.writerows(csv_rows)
        print(f"Wrote details to {args.out_csv}")

if __name__ == "__main__":
    main()