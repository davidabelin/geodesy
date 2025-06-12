"""
azdist_filter.py v4.0

- --tenth, --half, --whole apply to both Az and Dist unless --az-only or --dist-only specified.
- 'Reason' column shows which criteria/field was matched.
- Example usage:
        azd --az-targets 46 --az-tol 0.005 --dist-targets 2.882 7.071 --dist-tol 0.0005 --az-multiples 18 --dist-multiples 1.5 --factor-targets 14.142 --factor-tol 0.001 --tenth --spherical --half --whole --out data/azdist_filtered.csv data/SelectPnts.kml data/SelectPnts_critlines.kml
        azd --dist-only --dist-targets 2.882 7.071 --dist-tol 0.0001 --dist-multiples 1.5 1.618 1.7321 1.4142 --factor-targets 1.5 1.618 1.7321 1.4142 --factor-tol 0.0001 --spherical --whole --out highpoints/azdist_out.csv highpoints/BigMixPnts.kml highpoints/BigMixLines.kml
        azd --az-targets 13 66 112.5 --az-tol 0.005 --factor-targets 36 30 54 --factor-tol 0.0001 --spherical --half --whole --out highpoints/azdist_out.csv highpoints/BigMixPnts.kml highpoints/BigMixLines.kml

"""

import pandas as pd
import numpy as np
import argparse
import math
from typing import List, Callable, Dict, Tuple
import xml.etree.ElementTree as ET
from itertools import combinations
import simplekml # For KML output
import geometry # For inverse_geodetic

# === Constants ===
PHI = (1 + 5 ** 0.5) / 2
PI = math.pi
SQRT2 = math.sqrt(2)
SQRT3 = math.sqrt(3) 
TOL = 0.0001 # Default tolerance, used in various places

# === KML Parsing (from dot_connecter.py) ===
def load_points_from_kml(path: str) -> Dict[str, Tuple[float, float]]:
    """
    Parse KML to dict name -> (lat, lon).
    Handles Placemarks without a <name> tag by generating a unique name.
    """
    ns = {'kml': 'http://www.opengis.net/kml/2.2'}
    tree = ET.parse(path)
    root = tree.getroot()
    pts = {}
    unnamed_point_idx = 0
    for pm_idx, pm in enumerate(root.findall('.//kml:Placemark', ns)):
        name = pm.findtext('kml:name', default='', namespaces=ns).strip()
        
        # Find coordinates element safely
        coordinates_element = pm.find('.//kml:Point/kml:coordinates', ns)
        if coordinates_element is None or coordinates_element.text is None:
            # print(f"Warning: Placemark {pm_idx + 1} skipped, missing coordinates element or text.")
            continue
        
        coord_text_str = coordinates_element.text.strip()
        if not coord_text_str: # Skip if coordinates string is empty after stripping
            # print(f"Warning: Placemark {pm_idx + 1} (Name: '{name}') skipped, empty coordinates string.")
            continue

        if not name: # If name is missing, generate one
            unnamed_point_idx += 1
            name = f"UP_{unnamed_point_idx}"
        
        # Ensure name is unique (handles auto-generated or KML duplicates)
        original_name = name
        suffix_counter = 1
        while name in pts: 
            name = f"{original_name}_{suffix_counter}"
            suffix_counter += 1
            
        try:
            lon_str, lat_str, *_ = coord_text_str.split(',') # KML order is lon,lat,alt
            pts[name] = (float(lat_str), float(lon_str)) # Stores as lat,lon
        except ValueError:
            # print(f"Warning: Could not parse coordinates for Placemark '{name}': {coord_text_str}")
            continue
    print(f"Loaded {len(pts)} points from {path}.")
    return pts

# === Pair Metric Calculation (inspired by dot_connecter.py) ===
def generate_all_pairs_dataframe(points: Dict[str, Tuple[float, float]], ellipsoid: bool = True) -> pd.DataFrame:
    """Calculates Az/Dist for all unique pairs and returns a DataFrame."""
    pair_data_list = []
    point_names = list(points.keys())
    # P1, P1_lat, P1_lon,
    # P2, P2_lat, P2_lon,
    # Dist, Az12 (fwd P1->P2), Az21 (fwd P2->P1), 
    # BackAz12 (at P2 from P1), BackAz21 (at P1 from P2)
    columns = ['P1', 'P1_lat', 'P1_lon',
               'P2', 'P2_lat', 'P2_lon',
               'Dist', 'Az12', 'Az21',
               'BackAz12', 'BackAz21']

    if len(point_names) >= 2: # Only proceed if pairs can be formed
        for p1_name, p2_name in combinations(point_names, 2):
            lat1, lon1 = points[p1_name]
            lat2, lon2 = points[p2_name]

            # az_p1_to_p2 is the forward azimuth at p1 towards p2
            # az_p2_to_p1 is the forward azimuth at p2 towards p1
            az_p1_to_p2, az_p2_to_p1, dist = geometry.inverse_geodetic(lat1, lon1, lat2, lon2, unit='miles', ellipsoid=ellipsoid)

            az12 = az_p1_to_p2 % 360
            az21 = az_p2_to_p1 % 360
            back_az_at_p2 = (az21 + 180) % 360 # Back azimuth at P2 for line P1->P2
            back_az_at_p1 = (az12 + 180) % 360 # Back azimuth at P1 for line P2->P1

            pair_data_list.append({
                'P1': p1_name, 'P1_lat': lat1, 'P1_lon': lon1,
                'P2': p2_name, 'P2_lat': lat2, 'P2_lon': lon2,
                'Dist': dist, 'Az12': az12, 'Az21': az21,
                'BackAz12': back_az_at_p2, 'BackAz21': back_az_at_p1
            })

    if not pair_data_list: # If no pairs were generated
        return pd.DataFrame(columns=columns) # Return empty DataFrame with defined columns
    return pd.DataFrame(pair_data_list, columns=columns)

def within_tol(val: float, targets: list, tol: float) -> bool:
    return any(abs(val - float(t)) <= tol for t in targets)

def is_multiple(val: float, base: float, tol: float) -> bool:
    base = float(base)
    if base == 0: return False
    rem = val % base
    return min(rem, abs(base - rem)) <= tol

def is_factor(val: float, target: float, tol: float) -> bool:
    target = float(target)
    if val == 0:
        return False
    rem = target % val
    return min(rem, abs(val - rem)) <= tol

def mask_tenth(series, tol=TOL):
    decimals = np.round((series - np.floor(series)) * 10)
    return (np.abs(series - np.round(series * 10) / 10) <= tol) & (decimals == 1)

def mask_half(series, tol=TOL):
    decimals = np.round((series - np.floor(series)) * 10)
    return (np.abs(series - np.round(series * 2) / 2) <= tol) & (decimals == 5)

def mask_whole(series, tol=TOL):
    decimals = np.round((series - np.floor(series)) * 10)
    return (np.abs(series - np.round(series)) <= tol) & (decimals == 0)

# === KML Drawing (already present) ===
def draw_lines(
    pair_records: List[Dict[str, any]], 
    points: Dict[str, Tuple[float, float]], 
    out_path: str,
    ellipsoid_calc_used: bool
):
    kml = simplekml.Kml(name="Filtered Lines") # Added name to KML root
    
    calc_method_str = "Ellipsoidal (WGS84)" if ellipsoid_calc_used else "Spherical"
    kml.document.description = f"Lines generated using {calc_method_str} calculations."

    palette = ['FF0000FF','FF00FF00','FFFF0000','FFFFFF00','FF00FFFF','FFFF00FF']
    style_map = {}

    for record in pair_records:
        p1_name = record['P1']
        p2_name = record['P2']

        grp = p1_name # Group style by the reference point
        if grp not in style_map:
            style_map[grp] = palette[len(style_map) % len(palette)]

        line = kml.newlinestring(name=f"{p1_name} → {p2_name}")
        lat1, lon1 = points[p1_name]
        lat2, lon2 = points[p2_name]
        line.coords = [(lon1, lat1), (lon2, lat2)]
        line.style.linestyle.color = style_map[grp]
        line.style.linestyle.width = 1.5
        
        description_html = f"""
        <![CDATA[
          <b>P1:</b> {record['P1']}<br>
          <b>P2:</b> {record['P2']}<br>
          <hr>
          <b>Distance:</b> {record['Dist']:.3f} miles<br>
          <hr>
          <u>Path P1 → P2:</u><br>
          <b>Forward Azimuth at P1 (Az12):</b> {record['Az12']:.3f}°<br>
          <b>Back Azimuth at P2 (BackAz12):</b> {record['BackAz12']:.3f}°<br>
          <hr>
          <u>Path P2 → P1:</u><br>
          <b>Forward Azimuth at P2 (Az21):</b> {record['Az21']:.3f}°<br>
          <b>Back Azimuth at P1 (BackAz21):</b> {record['BackAz21']:.3f}°<br>
          <hr>
          <b>Filter Reason(s):</b> {record['Reason']}<br>
          <hr>
          <i>Calculation Method: {calc_method_str}</i>
        ]]>
        """
        line.description = description_html

    kml.save(out_path)
    print(f"Wrote KML with {len(pair_records)} lines to {out_path}.")


def filter_pairs(
    df: pd.DataFrame,
    az_targets: list = None,
    az_tol: float = TOL,
    dist_targets: list = None,
    dist_tol: float = TOL,
    az_multiples: list = None,
    dist_multiples: list = None,
    factor_targets: list = None,
    factor_tol: float = TOL,
    tenth: bool = False,
    half: bool = False,
    whole: bool = False,
    az_only: bool = False,
    dist_only: bool = False,
    custom_funcs: list = []
) -> pd.DataFrame:
    """
    Flexible filtering: tenths/halves/wholes apply to both Az and Dist unless only one is requested.
    Reason column records all matches for each field.
    """
    reasons_dict = {idx: [] for idx in df.index}
    keep = pd.Series(False, index=df.index)

    # Azimuth close to a special value?
    if az_targets and len(az_targets) > 0:
        for idx, row in df.iterrows():
            x12, x21 = row['Az12'], row['Az21']
            for t in az_targets:
                if abs(x12 - t) <= az_tol:
                    keep.loc[idx] = True
                    reasons_dict[idx].append(f"Az12 target:{t:.3f}")
                if abs(x21 - t) <= az_tol:
                    keep.loc[idx] = True
                    reasons_dict[idx].append(f"Az21 target:{t:.3f}")

    # Distance close to a special value?
    if dist_targets and len(dist_targets) > 0:
        for idx, x in df['Dist'].items(): # Iterate with index
            for t in dist_targets:
                if abs(x - t) <= dist_tol:
                    keep.loc[idx] = True
                    reasons_dict[idx].append(f"Dist target:{t:.4f}")

    # Azimuth is a multiple of some special value?
    if az_multiples and len(az_multiples) > 0:
        for idx, row in df.iterrows():
            x12, x21 = row['Az12'], row['Az21']
            for m in az_multiples:
                if is_multiple(x12, m, az_tol):
                    keep.loc[idx] = True
                    reasons_dict[idx].append(f"Az12 multiple:{m:.3f}")
                if is_multiple(x21, m, az_tol):
                    keep.loc[idx] = True
                    reasons_dict[idx].append(f"Az21 multiple:{m:.3f}")

    # Distance is a multiple of some special value?
    if dist_multiples and len(dist_multiples) > 0:
        for m in dist_multiples:
            for idx, x in df['Dist'].items():
                if is_multiple(x, m, dist_tol):
                    keep.loc[idx] = True
                    reasons_dict[idx].append(f"Dist multiple:{m:.4f}")

    # Distance is a factor of some special value?
    if factor_targets and len(factor_targets) > 0:
        for tgt in factor_targets:
            for idx, x in df['Dist'].items():
                if is_factor(x, tgt, factor_tol):
                    keep.loc[idx] = True
                    reasons_dict[idx].append(f"Dist factor of:{tgt:.4f}")

    # Tenths, halves, wholes filters (now test both Az and Dist by default)
    test_az = not dist_only
    test_dist = not az_only

    if tenth:
        if test_az:
            mask12 = mask_tenth(df['Az12'], az_tol)
            mask21 = mask_tenth(df['Az21'], az_tol)
            for idx in df.index[mask12]:
                keep.loc[idx] = True
                reasons_dict[idx].append("Az12 Tenth")
            for idx in df.index[mask21]:
                keep.loc[idx] = True
                reasons_dict[idx].append("Az21 Tenth")
        if test_dist:
            mask = mask_tenth(df['Dist'], dist_tol)
            for i, m in enumerate(mask):
                if m:
                    keep.iat[i] = True
                    reasons_dict[i].append("Dist Tenth")
    if half:
        if test_az:
            mask12 = mask_half(df['Az12'], az_tol)
            mask21 = mask_half(df['Az21'], az_tol)
            for idx in df.index[mask12]:
                keep.loc[idx] = True
                reasons_dict[idx].append("Az12 Half")
            for idx in df.index[mask21]:
                keep.loc[idx] = True
                reasons_dict[idx].append("Az21 Half")
        if test_dist:
            mask = mask_half(df['Dist'], dist_tol)
            for i, m in enumerate(mask):
                if m:
                    keep.iat[i] = True
                    reasons_dict[i].append("Dist Half")
    if whole:
        if test_az:
            mask12 = mask_whole(df['Az12'], az_tol)
            mask21 = mask_whole(df['Az21'], az_tol)
            for idx in df.index[mask12]:
                keep.loc[idx] = True
                reasons_dict[idx].append("Az12 Whole")
            for idx in df.index[mask21]:
                keep.loc[idx] = True
                reasons_dict[idx].append("Az21 Whole")
        if test_dist:
            mask = mask_whole(df['Dist'], dist_tol)
            for i, m in enumerate(mask):
                if m:
                    keep.iat[i] = True
                    reasons_dict[i].append("Dist Whole")

    # User-supplied custom functions (advanced usage)
    for func in custom_funcs:
        mask = df.apply(func, axis=1)
        for idx in df.index[mask]:
            keep.loc[idx] = True
            reasons_dict[idx].append("Custom")

    filtered_df = df[keep].copy()
    
    # Populate the 'Reason' column for the filtered DataFrame
    # Using sorted(list(set(...))) to ensure unique and ordered reasons
    if not filtered_df.empty:
        filtered_df["Reason"] = [", ".join(sorted(list(set(reasons_dict[idx])))) for idx in filtered_df.index]
    else:
        # Add Reason column even if empty to maintain schema
        filtered_df["Reason"] = pd.Series(dtype='str')
        
    return filtered_df

def main():
    parser = argparse.ArgumentParser(description="Filter azimuth/distance pairs by modular criteria.")
    parser.add_argument("input_kml", help="Input KML file with placemarks")
    parser.add_argument("output_kml", help="Output KML file with lines for filtered pairs")
    parser.add_argument("--out", help="Optional CSV file for saving filtered pair data (Az, Dist, Reason)", default=None)
    parser.add_argument("--az-targets", nargs='*', type=float, default=None, help="Azimuths of interest (deg)")
    parser.add_argument("--az-tol", type=float, default=TOL, help="Tolerance for azimuth match [default: 0.001]")
    parser.add_argument("--dist-targets", nargs='*', type=float, default=None, help="Distances of interest (miles)")
    parser.add_argument("--dist-tol", type=float, default=TOL, help="Tolerance for distance match [default: 0.001]")
    parser.add_argument("--az-multiples", nargs='*', type=float, default=None, help="Azimuth multiples to match")
    parser.add_argument("--dist-multiples", nargs='*', type=float, default=None, help="Distance multiples to match")
    parser.add_argument("--factor-targets", nargs='*', type=float, default=None, help="Distance is a factor of each TARGET (within tolerance)")
    parser.add_argument("--factor-tol", type=float, default=TOL, help="Tolerance for --factor-targets checks [default: 0.001]")
    parser.add_argument("--tenth", action='store_true', help="Filter for distances or azimuths ending in .1 (exclusive)")
    parser.add_argument("--half", action='store_true', help="Filter for distances or azimuths ending in .5 (exclusive)")
    parser.add_argument("--whole", action='store_true', help="Filter for distances or azimuths ending in .0 (exclusive)")
    parser.add_argument("--az-only", action='store_true', help="Restrict --tenth/--half/--whole to Az only")
    parser.add_argument("--dist-only", action='store_true', help="Restrict --tenth/--half/--whole to Dist only")
    parser.add_argument(
        '--spherical', action='store_true',
        help='Use spherical (not ellipsoidal) calculations for Az/Dist'
    )
    args = parser.parse_args()

    def to_float_list(seq): # Helper function, can be kept local to main or moved
        if seq is None:
            return []
        out = []
        for x in seq:
            try:
                if x is not None and str(x).strip() != '':
                    out.append(float(x))
            except ValueError: # Catch if conversion to float fails
                print(f"Warning: Could not convert '{x}' to float. Skipping this target/multiple.")
                continue
        return out

    points_data = load_points_from_kml(args.input_kml)
    df = generate_all_pairs_dataframe(points_data, ellipsoid=not args.spherical)
    filtered = filter_pairs(
        df,
        az_targets=to_float_list(args.az_targets),
        az_tol=args.az_tol,
        dist_targets=to_float_list(args.dist_targets),
        dist_tol=args.dist_tol,
        az_multiples=to_float_list(args.az_multiples),
        dist_multiples=to_float_list(args.dist_multiples),
        factor_targets=to_float_list(args.factor_targets),
        factor_tol=args.factor_tol,
        tenth=args.tenth,
        half=args.half,
        whole=args.whole,
        az_only=args.az_only,
        dist_only=args.dist_only
    )
    print(f"Selected {len(filtered)} out of {len(df)} total calculated pairs.")

    if args.out:
        filtered.to_csv(args.out, index=False)
        print(f"Filtered pair data written to {args.out}")
    else:
        # Print to console if no CSV output, but maybe just a summary if KML is the main output
        if not filtered.empty:
            print("Filtered pairs (first 5 rows):")
            print(filtered.head())
        else:
            print("No pairs matched the filter criteria.")

    # Generate KML output with lines
    if not filtered.empty:
        # Convert filtered DataFrame rows to a list of dictionaries
        pair_records_for_kml = filtered.to_dict(orient='records')
        draw_lines(pair_records_for_kml, points_data, args.output_kml, ellipsoid_calc_used=not args.spherical)
    else:
        print(f"No lines to draw. KML file '{args.output_kml}' will not be created with content (or may be empty if simplekml creates it anyway).")

if __name__ == "__main__":
    main()
