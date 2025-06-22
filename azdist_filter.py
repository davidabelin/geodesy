"""
azdist_filter.py v7.6

- --half, --whole apply to both Az and Dist unless --az-only or --dist-only specified.
- 'Reason' column shows which criteria/field was matched.
- Example usages:
        azd --az-only --factor-tol 0.002 --az-tol 0.002 --az-targets 185.117 84.883 5.117 25.5 64.5 54.75 125.25 34.25 95.117 27.6923 117.692 152.3077 1 91 181 271 89 179 269 359 61 59 44 46 29 31 121 119 107 109 109.5 198 36.8699 53.1301 126.8699 143.1301 53.1301 36.8699 116.565 153.435 63.435 26.5651 --az-multiples 15 18 --factor-targets 222.5 --spherical --whole --out data/az_out.csv data/MixPnts.kml data/az_MixLines.kml
        azd --az-targets 46 --az-tol 0.005 --dist-targets 2.2882 7.071 --dist-tol 0.0005 --az-multiples 18 --dist-multiples 1.5 --factor-targets 14.142 --factor-tol 0.001 --spherical --half --whole --out data/azdist_filtered.csv data/SelectPnts.kml data/SelectPnts_critlines.kml
        azd --dist-only --factor-tol 0.00019 --dist-tol 0.0002 --dist-targets 2.2882 --dist-multiples 1.618 0.618 2.236 0.866 0.7071 --factor-targets 16.18 6.18 17.321 14.142 --spherical --whole --out highpoints/dist_out.csv highpoints/MixPnts.kml highpoints/dist_MMixLines.kml
        azd --factor-tol 0.0005 --dist-tol 0.0005 --dist-multiples 2.28825 0.87403 2.80252 1.07047 6.26662 2.393643 1.4142 1.7321 2.2361 1.618 0.618 0.47553 1.31433 1.5388 3.0 4.0 5.0 --factor-targets 1.4142 1.7321 2.2361 1.618 0.618 0.47553 1.31433 1.5388 3.0 4.0 5.0 2.28825 0.87403 2.80252 1.07047 6.26662 2.39364 --export-color-map data/d_tri_colormap.csv --out data/d_tris_ell.csv data/MixPnts.kml data/d_TriLines_ell.kml
   
Useful RIGHT Triangle properties (for Grouping):
r2 - r3 - r5:   AZs 129.2315 140.7685 50.7685 39.2315
                Ds 1.4142 1.7321 2.2361
3 - 4 - 5:	    AZs 53.1301 36.8699 126.8699 143.1301
                Ds 3 4 5
1 - 2 - r5:	    AZs 63.43495 26.56505 116.56505 153.43495
                Ds 1 2 2.2361
30 - 60 - 90    AZs 30 60 90
                Ds 1 1.7321 2
1 - phi - phi^2:    AZs 51.82729 38.17271 128.17271 141.82729
                    Ds 1 1.618 2.618
18 - 72 - 90:	Ds 0.47553 1.618 1.5388
36 - 54 - 90	Ds 1.31433 2 2.2361

azd --az-multiples 129.2315 140.7685 50.7685 39.2315 53.1301 36.8699 126.8699 143.1301 63.43495 26.56505 116.56505 153.43495 51.82729 38.1727 128.1727 141.82729

USER-DRIVEN COLORING: Skeleton for the Only Solution That Cannot Fail

- Exports a CSV mapping every unique reason set (as frozenset string) to a unique id.
- User then assigns their own color in Excel, Notepad, or Google Sheets.
- Script reads in mapping and applies user-assigned color codes when generating KML.
- This gives *full control* over what color every line is, and enables visual grouping, legend, re-use, and no surprises.

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
import os
from functools import partial

# ===============================================================================
# Constants and Geodetic Functions
# ===============================================================================
PHI = (1 + math.sqrt(5)) / 2
DEG2RAD = math.pi / 180.0
RAD2DEG = 180.0 / math.pi
EARTH_RADIUS_MILES = 3958.756
worker_geod_obj = None

def init_worker(use_ellipsoid: bool):
    global worker_geod_obj
    if use_ellipsoid:
        worker_geod_obj = Geod(ellps='WGS84')

def inverse_geodetic(p1_coords, p2_coords, geod_obj):
    if geod_obj:
        az1, az2, dist_m = geod_obj.inv(p1_coords[1], p1_coords[0], p2_coords[1], p2_coords[0])
        return az1 % 360, az2 % 360, dist_m / 1609.344
    phi1r, phi2r = p1_coords[0] * DEG2RAD, p2_coords[0] * DEG2RAD
    dlam = (p2_coords[1] - p1_coords[1]) * DEG2RAD
    sigma = math.acos(max(-1, min(1, math.sin(phi1r) * math.sin(phi2r) + math.cos(phi1r) * math.cos(phi2r) * math.cos(dlam))))
    dist = EARTH_RADIUS_MILES * sigma
    y1, x1 = math.sin(dlam) * math.cos(phi2r), math.cos(phi1r) * math.sin(phi2r) - math.sin(phi1r) * math.cos(phi2r) * math.cos(dlam)
    az1 = (math.atan2(y1, x1) * RAD2DEG + 360) % 360
    y2, x2 = math.sin(-dlam) * math.cos(phi1r), math.cos(phi2r) * math.sin(phi1r) - math.sin(phi2r) * math.cos(phi1r) * math.cos(dlam)
    az2 = (math.atan2(y2, x2) * RAD2DEG + 360) % 360
    return az1, az2, dist

def calculate_triangle_properties(p1_coords, p2_coords, p3_coords, geod_obj):
    _, _, side_ab = inverse_geodetic(p1_coords, p2_coords, geod_obj)
    _, _, side_bc = inverse_geodetic(p2_coords, p3_coords, geod_obj)
    _, _, side_ca = inverse_geodetic(p3_coords, p1_coords, geod_obj)
    def clamp(x): return max(-1.0, min(1.0, x))
    try:
        angle_a = math.degrees(math.acos(clamp((side_bc**2 + side_ca**2 - side_ab**2) / (2 * side_bc * side_ca))))
        angle_b = math.degrees(math.acos(clamp((side_ca**2 + side_ab**2 - side_bc**2) / (2 * side_ca * side_ab))))
        angle_c = math.degrees(math.acos(clamp((side_ab**2 + side_bc**2 - side_ca**2) / (2 * side_ab * side_bc))))
    except (ValueError, ZeroDivisionError): return None
    return {"sides": [side_ab, side_bc, side_ca], "angles": [angle_a, angle_b, angle_c]}

# ===============================================================================
# KML Parsing, Triangle Definitions, and Styling
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

TRIANGLE_DEFINITIONS = {
    "3-4-5":       {"group": "3-4-5 Right Triangles", "angles": [36.87, 53.13, 90.0], "side_ratios": [3.0, 4.0, 5.0]},
    "30-60-90":    {"group": "30-60-90 Right Triangles", "angles": [30.0, 60.0, 90.0], "side_ratios": [1.0, math.sqrt(3), 2.0]},
    "Equilateral": {"group": "Equilateral Triangles", "angles": [60.0, 60.0, 60.0], "side_ratios": [1.0, 1.0, 1.0]},
    "45-45-90":    {"group": "45-45-90 Right Triangles", "angles": [45.0, 45.0, 90.0], "side_ratios": [1.0, 1.0, math.sqrt(2)]},
    "18-72-90":    {"group": "18-72-90 Right Triangles", "angles": [18.0, 72.0, 90.0], "side_ratios": [math.sin(math.radians(18)), math.sin(math.radians(72)), 1.0]},
    "36-54-90":    {"group": "36-54-90 Right Triangles", "angles": [36.0, 54.0, 90.0], "side_ratios": [math.sin(math.radians(36)), math.sin(math.radians(54)), 1.0]},
    "Golden T":    {"group": "Golden Triangles (72-72-36)", "angles": [36.0, 72.0, 72.0], "side_ratios": [1.0, PHI, PHI]},
    "Golden G":    {"group": "Golden Gnomons (36-36-108)", "angles": [36.0, 36.0, 108.0], "side_ratios": [1.0, 1.0, 1/PHI]},
    "Kepler":      {"group": "Kepler Right Triangles", "angles": [31.72, 58.28, 90.0], "side_ratios": [1, math.sqrt(PHI), PHI]},
    "1-2-r5":      {"group": "1-2-sqrt(5) Right Triangles", "angles": [26.57, 63.43, 90.0], "side_ratios": [1.0, 2.0, math.sqrt(5)]},
}

# --- New KML Styling Logic ---
# Base colors for matches found by the more precise 'side_ratios'
KML_STYLES_SIDES = {
    "3-4-5": "FF0077FF", "30-60-90": "FF00FF00", "Equilateral": "FFFF00F0", # Bright Pink
    "45-45-90": "FFFF0000", "18-72-90": "FFFF00CC", "36-54-90": "FFFF10C1",
    "Golden T": "FF00D7FF", "Golden G": "FFFF8C00", "Kepler": "FFEE82EE",
    "1-2-r5": "FFFF00FF", "Default": "FFFFFFFF",
}
# Darker/muted versions for matches found by 'angles'
KML_STYLES_ANGLES = {
    "3-4-5": "FF004E9A", "30-60-90": "FF009900", "Equilateral": "FFCC00B4", # Darker Pink
    "45-45-90": "FFB40000", "18-72-90": "FFB40090", "36-54-90": "FFB40C88",
    "Golden T": "FF00A6D1", "Golden G": "FFB46200", "Kepler": "FFA95DB4",
    "1-2-r5": "FFB400B4", "Default": "FFCCCCCC",
}

def find_triangle_match(properties, angle_tol, ratio_tol):
    if not properties: return None, None, None
    sorted_angles, sorted_sides = sorted(properties['angles']), sorted(properties['sides'])
    for key, definition in TRIANGLE_DEFINITIONS.items():
        match_type = None
        if all(abs(a - b) < angle_tol for a, b in zip(sorted_angles, sorted(definition['angles']))):
            match_type = "angles"
        elif sorted_sides[0] > 1e-9:
            measured_ratios = [s / sorted_sides[0] for s in sorted_sides]
            def_ratios = sorted(definition['side_ratios'])
            if def_ratios[0] > 1e-9:
                def_ratios_norm = [r / def_ratios[0] for r in def_ratios]
                if all(abs(a - b) < ratio_tol for a, b in zip(measured_ratios, def_ratios_norm)):
                    match_type = "side_ratios"
        if match_type:
            factor = sum(sorted_sides) / sum(definition['side_ratios']) if sum(definition['side_ratios']) > 0 else 0
            return key, match_type, factor
    return None, None, None

def process_single_triad(triad_of_points, angle_tol, ratio_tol):
    (p1_name, p1_coords), (p2_name, p2_coords), (p3_name, p3_coords) = triad_of_points
    properties = calculate_triangle_properties(p1_coords, p2_coords, p3_coords, geod_obj=worker_geod_obj)
    match_key, match_reason, match_factor = find_triangle_match(properties, angle_tol, ratio_tol)
    if match_key:
        return {"points": (p1_name, p2_name, p3_name), "coords": (p1_coords, p2_coords, p3_coords),
                "key": match_key, "reason": match_reason, "factor": match_factor, "properties": properties}
    return None

# ===============================================================================
# Main Execution
# ===============================================================================
def main():
    parser = argparse.ArgumentParser(description="Find and classify special triangles from a KML file.")
    parser.add_argument("input_kml", help="Input KML file with placemarks.")
    parser.add_argument("output_kml", help="Output KML file for matched triangles.")
    parser.add_argument("--out-csv", help="Optional CSV file for saving matched triangle data.", default=None)
    parser.add_argument('--spherical', action='store_true', help='Use spherical calculations. Default is ellipsoidal (WGS84).')
    parser.add_argument('--angle-tol', type=float, default=1.5, help='Tolerance for angle matching in degrees. Default: 1.5')
    parser.add_argument('--ratio-tol', type=float, default=0.015, help='Tolerance for side ratio matching (unitless). Default: 0.015')
    args = parser.parse_args()

    use_ellipsoid = not args.spherical
    points = load_points_from_kml(args.input_kml)
    if len(points) < 3: print("Error: Need at least 3 points in the KML file."); return

    all_triads = list(combinations(points.items(), 3))
    total_triads = len(all_triads)
    
    print(f"Analyzing {total_triads} unique triangles using {'Ellipsoidal' if use_ellipsoid else 'Spherical'} model...")
    matched_triangles, PARALLEL_THRESHOLD = [], 20000 

    if total_triads < PARALLEL_THRESHOLD:
        print(f"Processing in a single thread (below threshold of {PARALLEL_THRESHOLD})...")
        geod_obj = Geod(ellps='WGS84') if use_ellipsoid else None
        for triad in tqdm(all_triads, desc="Processing"):
            result = process_single_triad_st(triad, geod_obj, args.angle_tol, args.ratio_tol)
            if result: matched_triangles.append(result)
    else:
        num_workers = os.cpu_count() or 1
        print(f"Processing in parallel with up to {num_workers} workers (above threshold of {PARALLEL_THRESHOLD})...")
        worker_func = partial(process_single_triad, angle_tol=args.angle_tol, ratio_tol=args.ratio_tol)
        with concurrent.futures.ProcessPoolExecutor(max_workers=num_workers, initializer=init_worker, initargs=(use_ellipsoid,)) as executor:
            results_iterator = executor.map(worker_func, all_triads, chunksize=1000)
            matched_triangles = [r for r in tqdm(results_iterator, total=total_triads, desc="Processing") if r is not None]

    print(f"Found {len(matched_triangles)} triangles matching defined criteria.")
    if not matched_triangles: return

    kml, csv_rows, kml_folders = simplekml.Kml(name="Special Triangles"), [], {}
    for match in sorted(matched_triangles, key=lambda m: m['key']):
        key, definition = match['key'], TRIANGLE_DEFINITIONS[match['key']]
        group_name = definition['group']
        folder = kml_folders.setdefault(group_name, kml.newfolder(name=group_name))
        
        p_names, p_coords, props, reason = match['points'], match['coords'], match['properties'], match['reason']
        poly = folder.newpolygon(name=f"{p_names[0]}-{p_names[1]}-{p_names[2]}")
        poly.outerboundaryis = [ (c[1], c[0]) for c in p_coords ] + [ (p_coords[0][1], p_coords[0][0]) ]
        
        # --- Select color based on match reason ---
        if reason == 'side_ratios':
            color = KML_STYLES_SIDES.get(key, KML_STYLES_SIDES["Default"])
        else: # 'angles'
            color = KML_STYLES_ANGLES.get(key, KML_STYLES_ANGLES["Default"])
        
        poly.style.polystyle.color, poly.style.linestyle.color, poly.style.linestyle.width = simplekml.Color.changealphaint(100, color), color, 2
        poly.description = f"<![CDATA[<b>Match Type:</b> {key} (by {reason})<br><b>Proportionality Factor:</b> {match['factor']:.4f}<br><hr><b>Vertices:</b> {', '.join(p_names)}<br><b>Angles:</b> {', '.join(f'{a:.2f}°' for a in props['angles'])}<br><b>Side Lengths (miles):</b> {', '.join(f'{s:.4f}' for s in props['sides'])}]]>"
        csv_rows.append([*p_names, key, reason, group_name, match['factor'], *props['angles'], *props['sides']])
        
    kml.save(args.output_kml); print(f"Wrote {len(matched_triangles)} triangles to {args.output_kml}")
    if args.out_csv:
        with open(args.out_csv, 'w', newline='') as f:
            writer = csv.writer(f); writer.writerow(['p1', 'p2', 'p3', 'match_type', 'match_reason', 'group', 'proportionality_factor', 'angle1', 'angle2', 'angle3', 'side1', 'side2', 'side3']); writer.writerows(csv_rows)
        print(f"Wrote details to {args.out_csv}")

def process_single_triad_st(triad_of_points, geod_obj, angle_tol, ratio_tol):
    (p1_name, p1_coords), (p2_name, p2_coords), (p3_name, p3_coords) = triad_of_points
    properties = calculate_triangle_properties(p1_coords, p2_coords, p3_coords, geod_obj=geod_obj)
    match_key, match_reason, match_factor = find_triangle_match(properties, angle_tol, ratio_tol)
    if match_key:
        return {"points": (p1_name, p2_name, p3_name), "coords": (p1_coords, p2_coords, p3_coords),
                "key": match_key, "reason": match_reason, "factor": match_factor, "properties": properties}
    return None

if __name__ == "__main__":
    main()