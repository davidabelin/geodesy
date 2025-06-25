"""
azdist_triangles.py   v2.0  Gemi-assisted

Example usage and result:

>python azdist_triangles.py --angle-tol 0.01 --ratio-tol 0.001 --out-csv data/tri_out_ell.csv data/MixPnts.kml data/TriangleLines_ell.kml
Loaded 199 points from data/MixPnts.kml.
Analyzing 1293699 unique triangles using Ellipsoidal model...
Processing in parallel with up to 16 workers (above threshold of 20000)...
Processing: 100%|..............| 1293699/1293699 [00:07<00:00, 162239.60it/s]
Found 16 triangles matching defined criteria.
Wrote 16 triangles to data/TriangleLines_ell.kml
Wrote details to data/tri_out_ell.csv:
p1,p2,p3,match_type,match_reason,group,proportionality_factor,angle1,angle2,angle3,side1,side2,side3
PenF,H66,H20,1-2-r5,side_ratios,1-2-sqrt(5) Triangles,1.7549706251830806,63.37560135360622,26.553978386299406,90.0704202600944,3.509076620406827,1.7547698691974,3.925299002369687
Chk61,POOthr2a,H70a,1-2-r5,side_ratios,1-2-sqrt(5) Triangles,1.447920671866929,63.382939793122574,26.560650067769167,90.05641013910828,2.895113730537005,1.4479922647818149,3.238305068603588
P,RC,SF06,3-4-5,side_ratios,3-4-5 Triangles,1.9967354744506343,53.208807282798425,36.88485334075021,89.90633937645138,7.991730068534685,5.989718172688324,9.979377452184602
PenC,Chk06,SF03,3-4-5,angles,3-4-5 Triangles,2.107892174058158,36.861026843188746,90.00325383616583,53.13571932064543,6.322542402856264,10.539746531733115,8.432417154108519
HighWa,Chk47,H33,3-4-5,side_ratios,3-4-5 Triangles,2.0481048286473866,89.97600239665351,36.86173392089572,53.1622636824508,10.239573585637306,6.142577448466066,8.195106909665267
Hk,Chk13,H28,36-54-90,side_ratios,36-54-90 Triangles,7.160916645741002,54.00012078420166,89.9786250508864,36.02125416491197,5.792584103523699,7.160016252741946,4.210700748444521
PenG,Chk28,Chk39,36-54-90,side_ratios,36-54-90 Triangles,9.746742154288015,35.97631369332924,90.04884093583046,53.974845370840306,5.727147967066431,9.749150673042244,7.884714852976899
Nb,Gs1,OtrC,36-54-90,side_ratios,36-54-90 Triangles,7.310651753375607,54.04594930187523,89.94142067262334,36.01263002550142,5.916283925027431,7.308669144179235,4.297233477887108
H44,H99,H42,36-54-90,side_ratios,36-54-90 Triangles,7.543286470935592,53.99575630212801,36.01484959617866,89.98939410169335,6.101895662277737,4.435106682177136,7.542763616339513
HighWa,Chk10,Chk11,45-45-90,side_ratios,45-45-90 Triangles,7.061143709732155,44.96256219646292,90.07614592405845,44.96129187947864,7.059279709862797,9.98984991911173,7.059122990658465
Lz,Chk11,H32,45-45-90,side_ratios,45-45-90 Triangles,3.1868506319749073,89.97733257966559,44.99123729125605,45.03143012907838,4.506365218521976,3.1859942821134686,3.188229148310552
Gs6,Chk53,H13,45-45-90,side_ratios,45-45-90 Triangles,5.220394166503175,90.06818552980714,44.97206006140158,44.9597544087913,7.385324196820699,5.219669315855199,5.21854705153263
Chk46,H22,H29,Equilateral,side_ratios,Equilateral Triangles (60-60-60),3.6552483110279077,59.99139491322797,60.038481896296624,59.97012319047539,3.654931775354589,3.6566653332542782,3.654147824474855
CFa,Chk26,SF03,Golden-T,side_ratios,Golden-Triangles (72-72-36),5.165728860772492,35.994689059956,72.08209579362611,71.92321514641789,5.1651697332291056,8.36236939372915,8.35483948060657
CptA,H70b,H47,Kepler,side_ratios,Kepler Triangles (1:sqrt(phi):phi),2.0325941054851597,38.149824008575926,51.79439685631286,90.05577913511124,2.0321271775742047,2.585052068609835,3.2897208489723937
SCb,OtrB,H44,Kepler,side_ratios,Kepler Triangles (1:sqrt(phi):phi),4.636507409942469,38.17437337175231,89.9655523300272,51.86007429822052,4.635953536045574,7.500851649407798,5.89945733343116

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
    y1 = math.sin(dlam) * math.cos(phi2r)
    x1 = math.cos(phi1r) * math.sin(phi2r) - math.sin(phi1r) * math.cos(phi2r) * math.cos(dlam)
    az1 = (math.atan2(y1, x1) * RAD2DEG + 360) % 360
    y2 = math.sin(-dlam) * math.cos(phi1r)
    x2 = math.cos(phi2r) * math.sin(phi1r) - math.sin(phi2r) * math.cos(phi1r) * math.cos(dlam)
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
# KML Parsing, Triangle Definitions, and Matching
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
    "3-4-5":       {"group": "3-4-5 Triangles", "angles": [36.87, 53.13, 90.0], "side_ratios": [3.0, 4.0, 5.0]},
    "30-60-90":    {"group": "30-60-90 Triangles", "angles": [30.0, 60.0, 90.0], "side_ratios": [1.0, math.sqrt(3), 2.0]},
    "Equilateral": {"group": "Equilateral Triangles (60-60-60)", "angles": [60.0, 60.0, 60.0], "side_ratios": [1.0, 1.0, 1.0]},
    "45-45-90":    {"group": "45-45-90 Triangles", "angles": [45.0, 45.0, 90.0], "side_ratios": [1.0, 1.0, math.sqrt(2)]},
    "18-72-90":    {"group": "18-72-90 Triangles", "angles": [18.0, 72.0, 90.0], "side_ratios": [math.sin(math.radians(18)), math.sin(math.radians(72)), 1.0]},
    "36-54-90":    {"group": "36-54-90 Triangles", "angles": [36.0, 54.0, 90.0], "side_ratios": [math.sin(math.radians(36)), math.sin(math.radians(54)), 1.0]},
    "Golden-T":    {"group": "Golden Triangles (72-72-36)", "angles": [36.0, 72.0, 72.0], "side_ratios": [1.0, PHI, PHI]},
    "Golden-G":    {"group": "Golden Gnomons (36-36-108)", "angles": [36.0, 36.0, 108.0], "side_ratios": [1.0, 1.0, 1/PHI]},
    "Kepler":      {"group": "Kepler Triangles (1:sqrt(phi):phi)", "angles": [31.72, 58.28, 90.0], "side_ratios": [1, math.sqrt(PHI), PHI]},
    "1-2-r5":      {"group": "1-2-sqrt(5) Triangles", "angles": [26.57, 63.43, 90.0], "side_ratios": [1.0, 2.0, math.sqrt(5)]},
}
KML_STYLES = {
    "3-4-5": "FF0077FF", "30-60-90": "FF00FF00", "Equilateral": "FF33FF33", "45-45-90": "FFFF0000",
    "18-72-90": "FFFF00CC", "36-54-90": "FFFF10C1", "Golden-T": "FF00D7FF", "Golden-G": "FFFF8C00",
    "Kepler": "FFEE82EE", "1-2-r5": "FFFF00FF", "Default": "FFFFFFFF",
}

def find_triangle_match(properties, angle_tol, ratio_tol): # Tolerances are now arguments
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

# ===============================================================================
# Parallel Processing Worker
# ===============================================================================
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
    # Tolerance arguments
    parser.add_argument('--angle-tol', type=float, default=1.5, help='Tolerance for angle matching in degrees. Default: 1.5')
    parser.add_argument('--ratio-tol', type=float, default=0.015, help='Tolerance for side ratio matching (unitless). Default: 0.015')
    args = parser.parse_args()

    use_ellipsoid = not args.spherical
    points = load_points_from_kml(args.input_kml)
    if len(points) < 3: print("Error: Need at least 3 points in the KML file."); return

    all_triads = list(combinations(points.items(), 3))
    total_triads = len(all_triads)
    
    print(f"Analyzing {total_triads} unique triangles using {'Ellipsoidal' if use_ellipsoid else 'Spherical'} model...")
    matched_triangles = []
    PARALLEL_THRESHOLD = 20000 

    if total_triads < PARALLEL_THRESHOLD:
        print(f"Processing in a single thread (below threshold of {PARALLEL_THRESHOLD})...")
        geod_obj = Geod(ellps='WGS84') if use_ellipsoid else None
        # Pass tolerances to the single-thread worker
        for triad in tqdm(all_triads, desc="Processing"):
            result = process_single_triad_st(triad, geod_obj, args.angle_tol, args.ratio_tol)
            if result: matched_triangles.append(result)
    else:
        num_workers = os.cpu_count() or 1
        print(f"Processing in parallel with up to {num_workers} workers (above threshold of {PARALLEL_THRESHOLD})...")
        # Use functools.partial to preset the tolerance arguments for the parallel worker
        worker_func = partial(process_single_triad, angle_tol=args.angle_tol, ratio_tol=args.ratio_tol)
        with concurrent.futures.ProcessPoolExecutor(max_workers=num_workers, initializer=init_worker, initargs=(use_ellipsoid,)) as executor:
            results_iterator = executor.map(worker_func, all_triads, chunksize=1000)
            matched_triangles = [r for r in tqdm(results_iterator, total=total_triads, desc="Processing") if r is not None]

    print(f"Found {len(matched_triangles)} triangles matching defined criteria.")
    if not matched_triangles: return

    kml, csv_rows = simplekml.Kml(name="Special Triangles"), []
    kml_folders = {}
    for match in sorted(matched_triangles, key=lambda m: m['key']):
        key, definition = match['key'], TRIANGLE_DEFINITIONS[match['key']]
        group_name = definition['group']
        folder = kml_folders.setdefault(group_name, kml.newfolder(name=group_name))
        p_names, p_coords, props = match['points'], match['coords'], match['properties']
        poly = folder.newpolygon(name=f"{p_names[0]}-{p_names[1]}-{p_names[2]}")
        poly.outerboundaryis = [ (c[1], c[0]) for c in p_coords ] + [ (p_coords[0][1], p_coords[0][0]) ]
        color = KML_STYLES.get(key, KML_STYLES["Default"])
        poly.style.polystyle.color, poly.style.linestyle.color, poly.style.linestyle.width = simplekml.Color.changealphaint(100, color), color, 2
        poly.description = f"<![CDATA[<b>Match Type:</b> {key} (by {match['reason']})<br><b>Proportionality Factor:</b> {match['factor']:.4f}<br><hr><b>Vertices:</b> {', '.join(p_names)}<br><b>Angles:</b> {', '.join(f'{a:.2f}°' for a in props['angles'])}<br><b>Side Lengths (miles):</b> {', '.join(f'{s:.4f}' for s in props['sides'])}]]>"
        csv_rows.append([*p_names, key, match['reason'], group_name, match['factor'], *props['angles'], *props['sides']])
    kml.save(args.output_kml); print(f"Wrote {len(matched_triangles)} triangles to {args.output_kml}")
    if args.out_csv:
        with open(args.out_csv, 'w', newline='') as f:
            writer = csv.writer(f); writer.writerow(['p1', 'p2', 'p3', 'match_type', 'match_reason', 'group', 'proportionality_factor', 'angle1', 'angle2', 'angle3', 'side1', 'side2', 'side3']); writer.writerows(csv_rows)
        print(f"Wrote details to {args.out_csv}")

# Worker for single-threaded mode needs tolerances passed in
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