"""
azdist_filter.py v7.0

- --half, --whole apply to both Az and Dist unless --az-only or --dist-only specified.
- 'Reason' column shows which criteria/field was matched.
- Example usages:
        azd --az-only --factor-tol 0.002 --az-tol 0.002 --az-targets 185.117 84.883 5.117 25.5 64.5 54.75 125.25 34.25 95.117 27.6923 117.692 152.3077 1 91 181 271 89 179 269 359 61 59 44 46 29 31 121 119 107 109 109.5 198 36.8699 53.1301 126.8699 143.1301 53.1301 36.8699 116.565 153.435 63.435 26.5651 --az-multiples 15 18 --factor-targets 222.5 --spherical --whole --out highpoints/az_out.csv highpoints/MixPnts.kml highpoints/az_MixLines.kml
        azd --dist-only --factor-tol 0.00025 --dist-tol 0.00025 --dist-targets 5.605 3.4641 0.33385 0.54018 0.87403 2.2882 3.7025 5.9907 0.004392 --dist-multiples 1.618 0.618 2.236 0.866 0.7071 --factor-targets 22.882 16.18 6.18 17.321 14.142 --spherical --whole --out highpoints/dist_out.csv highpoints/MixPnts.kml highpoints/dist_MixLines.kml
        azd --az-targets 46 --az-tol 0.005 --dist-targets 2.2882 7.071 --dist-tol 0.0005 --az-multiples 18 --dist-multiples 1.5 --factor-targets 14.142 --factor-tol 0.001 --spherical --half --whole --out data/azdist_filtered.csv data/SelectPnts.kml data/SelectPnts_critlines.kml
        azd --dist-only --factor-tol 0.00019 --dist-tol 0.0002 --dist-targets 2.2882 --dist-multiples 1.618 0.618 2.236 0.866 0.7071 --factor-targets 16.18 6.18 17.321 14.142 --spherical --whole --out highpoints/dist_out.csv highpoints/MixPnts.kml highpoints/dist_MMixLines.kml
        azd --dist-only --factor-tol 0.0005 --dist-tol 0.0005 --dist-multiples 3.4641 0.33385 0.54012 0.87403 3.7025 5.9907 1.618 0.618 2.236 0.866 0.7071 2.2882 5.605 0.174242 0.14943 --factor-targets 3.7025 5.9907 2.2882 3.7025 2.236 1.618 6.18 1.7321 1.4142 5.605 --spherical --half --whole --out data/d_penplus.csv data/PentagonPlusPoints.kml data/d_PenPlusLines_filt.kml
        azd --az-only --az-tol 0.01 --az-targets 25.5 64.5 54.75 125.25 34.25 1 10 172 91 82 73 74 53 55 89 179 269 359 61 59 44 46 134 136 29 31 121 119 107 109 109.5 198 126.87 143.13 53.13 36.87 222.5 42.5 132.5 129.2315 140.7685 50.7685 39.2315 126.8699 63.43495 26.56505 116.56505 153.43495 51.82729 38.1727 128.1727 141.82729 --az-multiples 15 18 --out data/az_mix_ell.csv data/MixPnts.kml data/az_MixLines_ell.kml
        
Useful RIGHT Triangle properties:
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

azd --factor-tol 0.0005 --dist-tol 0.0005 --dist-multiples 2.28825 0.87403 2.80252 1.07047 6.26662 2.393643 1.4142 1.7321 2.2361 1.618 0.618 0.47553 1.31433 1.5388 3.0 4.0 5.0 --factor-targets 1.4142 1.7321 2.2361 1.618 0.618 0.47553 1.31433 1.5388 3.0 4.0 5.0 2.28825 0.87403 2.80252 1.07047 6.26662 2.39364 --export-color-map data/d_tri_colormap.csv --out data/d_tris_ell.csv data/MixPnts.kml data/d_TriLines_ell.kml

azd --az-multiples 129.2315 140.7685 50.7685 39.2315 53.1301 36.8699 126.8699 143.1301 63.43495 26.56505 116.56505 153.43495 51.82729 38.1727 128.1727 141.82729

USER-DRIVEN COLORING: Skeleton for the Only Solution That Cannot Fail

- Exports a CSV mapping every unique reason set (as frozenset string) to a unique id.
- User then assigns their own color in Excel, Notepad, or Google Sheets.
- Script reads in mapping and applies user-assigned color codes when generating KML.
- This gives *full control* over what color every line is, and enables visual grouping, legend, re-use, and no surprises.

"""

import pandas as pd
import numpy as np
import argparse
import math
import ast
from typing import List, Callable, Dict, Tuple
from itertools import combinations
from functools import partial
import concurrent.futures
import xml.etree.ElementTree as ET
import simplekml
from geometry import inverse_geodetic
import os
import time
from tqdm import tqdm
from matplotlib import colormaps as cm
import matplotlib.colors as mcolors

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
            print(f"Warning: Placemark {pm_idx + 1} skipped, missing coordinates element or text.")
            continue
        
        coord_text_str = coordinates_element.text.strip()
        if not coord_text_str: # Skip if coordinates string is empty after stripping
            print(f"Warning: Placemark {pm_idx + 1} (Name: '{name}') skipped, empty coordinates string.")
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
            print(f"Warning: Could not parse coordinates for Placemark '{name}': {coord_text_str}")
            continue
    print(f"Loaded {len(pts)} points from {path}.")
    return pts

# === Pair Metric Calculation (inspired by dot_connecter.py) ===
def _calculate_pair_metrics(pair: Tuple[str, str], points: Dict[str, Tuple[float, float]], ellipsoid: bool) -> Dict[str, any]:
    """Worker function to calculate metrics for a single pair of points. Must be a top-level function for pickling."""
    p1_name, p2_name = pair
    lat1, lon1 = points[p1_name]
    lat2, lon2 = points[p2_name]

    # az_p1_to_p2 is the forward azimuth at p1 towards p2
    # az_p2_to_p1 is the forward azimuth at p2 towards p1
    az_p1_to_p2, az_p2_to_p1, dist = inverse_geodetic(lat1, lon1, lat2, lon2, unit='miles', ellipsoid=ellipsoid)

    az12 = az_p1_to_p2 % 360
    az21 = az_p2_to_p1 % 360
    back_az_at_p2 = (az21 + 180) % 360 # Back azimuth at P2 for line P1->P2
    back_az_at_p1 = (az12 + 180) % 360 # Back azimuth at P1 for line P2->P1

    return {
        'P1': p1_name, 'P1_lat': lat1, 'P1_lon': lon1,
        'P2': p2_name, 'P2_lat': lat2, 'P2_lon': lon2,
        'Dist': dist, 'Az12': az12, 'Az21': az21,
        'BackAz12': back_az_at_p2, 'BackAz21': back_az_at_p1
    }

def generate_all_pairs_dataframe(points: Dict[str, Tuple[float, float]], ellipsoid: bool = True) -> pd.DataFrame:
    """Calculates Az/Dist for all unique pairs, using parallel processing for large datasets."""
    point_names = list(points.keys())
    columns = ['P1', 'P1_lat', 'P1_lon',
               'P2', 'P2_lat', 'P2_lon',
               'Dist', 'Az12', 'Az21',
               'BackAz12', 'BackAz21']

    if len(point_names) < 2:
        return pd.DataFrame(columns=columns)

    all_pairs = list(combinations(point_names, 2))

    # Heuristic: For small numbers of pairs, the overhead of creating processes
    # can be slower than just running the calculations in a single thread.
    PARALLEL_THRESHOLD = 50000

    if len(all_pairs) < PARALLEL_THRESHOLD:
        print(f"Calculating metrics for {len(all_pairs)} pairs using a single thread (below threshold of {PARALLEL_THRESHOLD})...")
        # For smaller jobs, a simple list comprehension with a progress bar is efficient.
        pair_data_list = [
            _calculate_pair_metrics(pair, points, ellipsoid)
            for pair in tqdm(all_pairs, desc="Processing pairs (single-thread)")
        ]
    else:
        num_workers = os.cpu_count() or 1
        print(f"Calculating metrics for {len(all_pairs)} pairs using up to {num_workers} processes (above threshold of {PARALLEL_THRESHOLD})...")
        worker_func = partial(_calculate_pair_metrics, points=points, ellipsoid=ellipsoid)
        pair_data_list = []
        with concurrent.futures.ProcessPoolExecutor(max_workers=num_workers) as executor:
            results_iterator = executor.map(worker_func, all_pairs)
            pair_data_list = list(tqdm(results_iterator, total=len(all_pairs), desc="Processing pairs (parallel)"))

    if not pair_data_list:
        return pd.DataFrame(columns=columns)

    # Create DataFrame and ensure original column order
    return pd.DataFrame(pair_data_list)[columns]

def within_tol(val: float, targets: list, tol: float) -> bool:
    return any(abs(val - float(t)) <= tol for t in targets)

def vectorized_is_multiple(series: pd.Series, base: float, tol: float) -> pd.Series:
    """Vectorized check if values in a series are a multiple of a base value."""
    if base == 0:
        return pd.Series(False, index=series.index)
    rem = series % base
    return (rem <= tol) | (abs(base - rem) <= tol)

def vectorized_is_factor(series: pd.Series, target: float, tol: float) -> pd.Series:
    """Vectorized check if values in a series are a factor of a target value."""
    # Avoid division by zero for val in series
    safe_series = series.replace(0, np.nan)
    rem = target % safe_series
    mask = (rem <= tol) | (abs(safe_series - rem) <= tol)
    return mask.fillna(False) # Treat original zeros as not being a factor

def mask_half(series, tol=TOL):
    decimals = np.round((series - np.floor(series)) * 10)
    return (np.abs(series - np.round(series * 2) / 2) <= tol) & (decimals == 5)

def mask_whole(series, tol=TOL):
    decimals = np.round((series - np.floor(series)) * 10)
    return (np.abs(series - np.round(series)) <= tol) & (decimals == 0)

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
    half: bool = False,
    whole: bool = False,
    az_only: bool = False,
    dist_only: bool = False,
    custom_funcs: list = []
) -> pd.DataFrame:
    """
    Flexible filtering: halves/wholes apply to both Az and Dist unless only one is requested.
    Reason column records all matches for each field.
    """
    if df.empty:
        return df.copy()

    # Dictionary to hold Series for each potential reason column
    # Each Series will have the same index as df, with reason strings or NaN
    reason_columns_data = {}
    keep_mask = pd.Series(False, index=df.index)

    # Determine which fields to test based on --az-only and --dist-only flags
    test_az = not dist_only
    test_dist = not az_only

    # Azimuth close to a special value?
    if test_az and az_targets:
        for t in az_targets:
            mask12 = (df['Az12'] - t).abs() <= az_tol
            mask21 = (df['Az21'] - t).abs() <= az_tol
            reason_columns_data[f'az12_tgt_{t}'] = pd.Series(f"Az12 target:{t:.2f}", index=df.index).where(mask12)
            reason_columns_data[f'az21_tgt_{t}'] = pd.Series(f"Az21 target:{t:.2f}", index=df.index).where(mask21)
            keep_mask |= mask12 | mask21

    # Distance close to a special value?
    if test_dist and dist_targets:
        for t in dist_targets:
            mask = (df['Dist'] - t).abs() <= dist_tol
            reason_columns_data[f'dist_tgt_{t}'] = pd.Series(f"Dist target:{t:.4f}", index=df.index).where(mask)
            keep_mask |= mask

    # Azimuth is a multiple of some special value?
    if test_az and az_multiples:
        for m in az_multiples:
            mask12 = vectorized_is_multiple(df['Az12'], m, az_tol)
            mask21 = vectorized_is_multiple(df['Az21'], m, az_tol)
            reason_columns_data[f'az12_mult_{m}'] = pd.Series(f"Az12 multiple:{m:.2f}", index=df.index).where(mask12)
            reason_columns_data[f'az21_mult_{m}'] = pd.Series(f"Az21 multiple:{m:.2f}", index=df.index).where(mask21)
            keep_mask |= mask12 | mask21

    # Distance is a multiple of some special value?
    if test_dist and dist_multiples:
        for m in dist_multiples:
            mask = vectorized_is_multiple(df['Dist'], m, dist_tol)
            reason_columns_data[f'dist_mult_{m}'] = pd.Series(f"Dist multiple:{m:.4f}", index=df.index).where(mask)
            keep_mask |= mask

    # Distance is a factor of some special value? (Should only run if dist is being tested)
    if test_dist and factor_targets:
        for tgt in factor_targets:
            mask = vectorized_is_factor(df['Dist'], tgt, factor_tol)
            reason_columns_data[f'dist_factor_{tgt}'] = pd.Series(f"Dist factor of:{tgt:.4f}", index=df.index).where(mask)
            keep_mask |= mask

    # Halves, wholes filters
    if half:
        if test_az:
            mask12 = mask_half(df['Az12'], az_tol)
            mask21 = mask_half(df['Az21'], az_tol)
            reason_columns_data['az12_half'] = pd.Series("Az12 Half", index=df.index).where(mask12)
            reason_columns_data['az21_half'] = pd.Series("Az21 Half", index=df.index).where(mask21)
            keep_mask |= mask12 | mask21
        if test_dist:
            mask = mask_half(df['Dist'], dist_tol)
            reason_columns_data['dist_half'] = pd.Series("Dist Half", index=df.index).where(mask)
            keep_mask |= mask
    if whole:
        if test_az:
            mask12 = mask_whole(df['Az12'], az_tol)
            mask21 = mask_whole(df['Az21'], az_tol)
            reason_columns_data['az12_whole'] = pd.Series("Az12 Whole", index=df.index).where(mask12)
            reason_columns_data['az21_whole'] = pd.Series("Az21 Whole", index=df.index).where(mask21)
            keep_mask |= mask12 | mask21
        if test_dist:
            mask = mask_whole(df['Dist'], dist_tol)
            reason_columns_data['dist_whole'] = pd.Series("Dist Whole", index=df.index).where(mask)
            keep_mask |= mask

    # User-supplied custom functions (advanced usage)
    for i, func in enumerate(custom_funcs):
        mask = df.apply(func, axis=1)
        reason_columns_data[f'custom_{i}'] = pd.Series("Custom", index=df.index).where(mask)
        keep_mask |= mask

    # Create the reasons_df from the collected data in one go
    reasons_df = pd.DataFrame(reason_columns_data, index=df.index)

    filtered_df = df[keep_mask].copy()
    
    # Populate the 'Reason' column for the filtered DataFrame
    if not filtered_df.empty:
        reasons_for_filtered = reasons_df.loc[filtered_df.index]
        reason_series = reasons_for_filtered.apply(
            lambda row: ", ".join(sorted(list(set(r for r in row if pd.notna(r))))),
            axis=1
        )
        filtered_df["Reason"] = reason_series
    else:
        # Add Reason column even if empty to maintain schema
        filtered_df["Reason"] = pd.Series(dtype='str')
        
    return filtered_df

# === Color utilities ===
def rgba_to_kml_color(rgba):
    r, g, b, a = [int(255*x) for x in rgba]
    return f"{a:02X}{b:02X}{g:02X}{r:02X}"

def normalize_reason(reason: str) -> str:
    """Normalizes a reason string to treat Az12 and Az21 the same for grouping and coloring."""
    if reason.startswith("Az12 "):
        return reason.replace("Az12 ", "Az ", 1)
    if reason.startswith("Az21 "):
        return reason.replace("Az21 ", "Az ", 1)
    return reason

# === 1. Export mapping from unique reason-sets to user-editable color ids ===
def export_reason_map_csv(pair_records, path='reason_color_map.csv'):
    reason_sets = set(
        frozenset(normalize_reason(r.strip()) for r in rec.get('Reason', '').split(',') if r.strip())
        for rec in pair_records
    )

    if not reason_sets:
        with open(path, 'w', encoding='utf8') as f:
            f.write('reason_set,color,group\n')
        print(f"No unique reason-sets found. Empty map file created at {path}.")
        return

    sorted_reason_sets = sorted(list(reason_sets), key=lambda s: repr(sorted(list(s))))
    map_data = {
        'reason_set': [repr(sorted(list(rs))) for rs in sorted_reason_sets],
        'color': [f'color{i:03d}' for i in range(len(sorted_reason_sets))],
        'group': [f'group_{i:03d}' for i in range(len(sorted_reason_sets))]
    }
    df_to_export = pd.DataFrame(map_data)
    df_to_export.to_csv(path, index=False)
    print(f"Exported {len(df_to_export)} unique reason-sets to {path}. Assign colors and group names in this file, then re-run to generate KML.")

# === 2. Read user-edited color map and use for KML coloring ===
def load_reason_map(path='reason_color_map.csv'):
    mapping = {}
    df = pd.read_csv(path)
    if 'group' not in df.columns:
        raise ValueError(f"Color map file '{path}' is missing the required 'group' column.")
    for _, row in df.iterrows():
        key = frozenset(ast.literal_eval(row['reason_set']))
        mapping[key] = {'color': row['color'], 'group': row['group']}
    return mapping

def get_vivid_colors(colormap_name, n):
    cmap = cm[colormap_name]
    if n == 1:
        return [cmap(0.75)] # More vivid
    return [cmap(i/(n-1)) for i in range(n)]

def get_grbl_colors(n):
    if n == 1:
        return [cm['Blues'](0.6)]
    half = n // 2
    greens = [cm['Greens'](i / max(half-1, 1)) for i in range(half)] if half > 0 else []
    blues = [cm['Blues'](i / max(n-half-1, 1)) for i in range(n-half)] if (n-half) > 0 else []
    return greens + blues

# === KML Line Drawing ===
def draw_lines(
    pair_records: List[Dict[str, any]], 
    points: Dict[str, Tuple[float, float]], 
    out_path: str,
    ellipsoid_calc_used: bool, # This parameter is now passed from main
    reason_color_map_path: str
):
    kml = simplekml.Kml(name="Filtered Lines")
    calc_method_str = "Ellipsoidal (WGS84)" if ellipsoid_calc_used else "Spherical"
    kml.document.description = f"Lines generated using {calc_method_str} calculations."

    # The decision to create the map is now handled in main(). This function just loads and uses it.
    try:
        reason_map = load_reason_map(reason_color_map_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error loading color map: {e}")
        return # Stop if we can't get color/group info
    kml_folders = {} # To store folder objects: {group_name: kml_folder}

    for rec in pair_records:
        p1_name = rec['P1']
        p2_name = rec['P2']
        reason_string = rec.get('Reason', "")

        # Normalize reasons for consistent coloring and grouping
        raw_reasons_set = frozenset(r.strip() for r in reason_string.split(',') if r.strip())
        normalized_reasons_set = frozenset(normalize_reason(r) for r in raw_reasons_set)

        # Use normalized set for color and group lookup.
        style_info = reason_map.get(normalized_reasons_set, {'color': 'FFAAAAFF', 'group': 'Uncategorized'})
        color = style_info['color']
        group_name = style_info['group']
        width = 2.5 if len(raw_reasons_set) > 1 else 1.5

        # Determine folder based on the group from the map file and create if it doesn't exist
        if group_name not in kml_folders:
            kml_folders[group_name] = kml.newfolder(name=group_name)
        
        folder = kml_folders[group_name]

        line = folder.newlinestring(name=f"{p1_name} → {p2_name}")
        lat1, lon1 = points[p1_name]
        lat2, lon2 = points[p2_name]
        line.coords = [(lon1, lat1), (lon2, lat2)]
        line.style.linestyle.color = color
        line.style.linestyle.width = width
        description_html = f"""
        <![CDATA[
          <b>P1:</b> {rec['P1']}<br>
          <b>P2:</b> {rec['P2']}<br>
          <hr>
          <b>Distance:</b> {rec['Dist']:.3f} miles<br>
          <hr>
          <u>Path P1 → P2:</u><br>
          <b>Forward Azimuth at P1 (Az12):</b> {rec['Az12']:.3f}°<br>
          <b>Back Azimuth at P2 (BackAz12):</b> {rec['BackAz12']:.3f}°<br>
          <hr>
          <u>Path P2 → P1:</u><br>
          <b>Forward Azimuth at P2 (Az21):</b> {rec['Az21']:.3f}°<br>
          <b>Back Azimuth at P1 (BackAz21):</b> {rec['BackAz21']:.3f}°<br>
          <hr>
          <b>Filter Reason(s):</b> {rec['Reason']}<br>
          <hr>
          <i>Calculation Method: {calc_method_str}</i>
        ]]>
        """
        line.description = description_html

    # Generate summary of folders and line counts and add to KML description
    if kml_folders:
        summary_html = "<b>Folder Summary:</b><br/><ul>"
        # Sort folders by name for consistent output
        for folder_name in sorted(kml_folders.keys()):
            folder = kml_folders[folder_name]
            line_count = len(folder.features)
            if line_count > 0:
                summary_html += f"<li>{folder_name}: {line_count} lines</li>"
        summary_html += "</ul><hr>"
        
        # Prepend summary to the existing description
        kml.document.description = summary_html + kml.document.description

    kml.save(out_path)
    print(f"Wrote KML with {len(pair_records)} lines to {out_path}.")
    
def parse_arguments():
    """Parses command-line arguments for the script."""
    parser = argparse.ArgumentParser(description="Filter azimuth/distance pairs by modular criteria.")
    parser.add_argument("input_kml", help="Input KML file with placemarks")
    parser.add_argument("output_kml", help="Output KML file with lines for filtered pairs")
    parser.add_argument("--out", help="Optional CSV file for saving filtered pair data (Az, Dist, Reason)", default=None)
    parser.add_argument("--export-color-map", type=str, metavar='FILE', help="Export the reason-to-color/group map to a CSV file and exit.")
    parser.add_argument("--color-map", type=str, metavar='FILE', default='reason_color_map.csv', help="Path to the CSV map for KML generation. Must contain 'reason_set', 'color', and 'group' columns. Colors can be KML names or AABBGGRR hex codes. Default: 'reason_color_map.csv'")
    parser.add_argument("--az-only", action='store_true', help="Restrict all filters to Azimuth fields only.")
    parser.add_argument("--dist-only", action='store_true', help="Restrict all filters to Distance fields only.")
    parser.add_argument("--half", action='store_true', help="Filter for distances or azimuths ending in .5 (exclusive)")
    parser.add_argument("--whole", action='store_true', help="Filter for distances or azimuths ending in .0 (exclusive)")
    parser.add_argument("--az-tol", type=float, default=TOL, help="Tolerance for azimuth match [default: 0.001]")
    parser.add_argument("--az-targets", nargs='*', type=float, default=None, help="Azimuths of interest (deg)")
    parser.add_argument("--az-multiples", nargs='*', type=float, default=None, help="Azimuth multiples to match")
    parser.add_argument("--dist-tol", type=float, default=TOL, help="Tolerance for distance match [default: 0.001]")
    parser.add_argument("--dist-targets", nargs='*', type=float, default=None, help="Distances of interest (miles)")
    parser.add_argument("--dist-multiples", nargs='*', type=float, default=None, help="Distance multiples to match")
    parser.add_argument("--factor-tol", type=float, default=TOL, help="Tolerance for --factor-targets checks [default: 0.001]")
    parser.add_argument("--factor-targets", nargs='*', type=float, default=None, help="Distance is a factor of each TARGET (within tolerance)")
    parser.add_argument('--spherical', action='store_true', help='Use spherical (not ellipsoidal) calculations for Az/Dist')
    return parser.parse_args()

def to_float_list(seq):
    """Safely converts a sequence of strings to a list of floats."""
    if seq is None:
        return []
    out = []
    for x in seq:
        try:
            if x is not None and str(x).strip() != '':
                out.append(float(x))
        except ValueError:
            print(f"Warning: Could not convert '{x}' to float. Skipping this target/multiple.")
            continue
    return out

def load_and_prepare_data(args):
    """Loads KML points and generates the initial DataFrame of all point pairs."""
    points_data = load_points_from_kml(args.input_kml)
    all_pairs_df = generate_all_pairs_dataframe(points_data, ellipsoid=not args.spherical)
    return points_data, all_pairs_df

def apply_filters(args, all_pairs_df):
    """Applies all specified filters to the DataFrame."""
    parsed_az_targets = to_float_list(args.az_targets)
    parsed_dist_targets = to_float_list(args.dist_targets)
    parsed_az_multiples = to_float_list(args.az_multiples)
    parsed_dist_multiples = to_float_list(args.dist_multiples)
    parsed_factor_targets = to_float_list(args.factor_targets)

    filters_active = (
        bool(parsed_az_targets) or bool(parsed_dist_targets) or
        bool(parsed_az_multiples) or bool(parsed_dist_multiples) or
        bool(parsed_factor_targets) or args.half or args.whole
    )

    if not filters_active:
        processed_df = all_pairs_df.copy()
        if not processed_df.empty:
            processed_df['Reason'] = "All Pairs (No Filters)"
        elif 'Reason' not in processed_df.columns:
            processed_df['Reason'] = pd.Series(dtype='str')
        print(f"No filters specified. Processing all {len(processed_df)} pairs.")
    else:
        processed_df = filter_pairs(
            all_pairs_df,
            az_targets=parsed_az_targets, az_tol=args.az_tol,
            dist_targets=parsed_dist_targets, dist_tol=args.dist_tol,
            az_multiples=parsed_az_multiples, dist_multiples=parsed_dist_multiples,
            factor_targets=parsed_factor_targets, factor_tol=args.factor_tol,
            half=args.half, whole=args.whole,
            az_only=args.az_only, dist_only=args.dist_only
        )
        print(f"Selected {len(processed_df)} out of {len(all_pairs_df)} total calculated pairs.")
    return processed_df, filters_active

def handle_output(args, processed_df, points_data, ellipsoid_calc_used, filters_active):
    """Manages all file outputs (CSV, KML, color map)."""
    if args.out:
        processed_df.to_csv(args.out, index=False)
        print(f"Pair data written to {args.out}")

    if not processed_df.empty:
        pair_records_for_kml = processed_df.to_dict(orient='records')

        if args.export_color_map:
            print(f"Exporting color map to '{args.export_color_map}'...")
            export_reason_map_csv(pair_records_for_kml, args.export_color_map)
            return

        color_map_path = args.color_map
        if not os.path.exists(color_map_path):
            print(f"\nError: Color map '{color_map_path}' not found.")
            print(f"Please run the script with --export-color-map <filename.csv> first to generate it.")
            return

        pair_records_for_kml.sort(key=lambda x: (x.get('P1', ''), x.get('P2', '')))
        draw_lines(pair_records_for_kml, points_data, args.output_kml, ellipsoid_calc_used, reason_color_map_path=color_map_path)
    else:
        if filters_active:
            print(f"No lines to draw as no pairs matched the filter criteria. KML file '{args.output_kml}' will not be created with content.")
        else:
            print(f"No lines to draw as no pairs could be generated from the input. KML file '{args.output_kml}' will not be created with content.")

def main():
    """Main function to orchestrate the script's execution."""
    script_start_time = time.monotonic()
    args = parse_arguments()

    # --- Load and Prepare ---
    t0 = time.monotonic()
    points_data, all_pairs_df = load_and_prepare_data(args)
    t1 = time.monotonic()
    print(f"\n> Data loading and pair calculation took {t1 - t0:.2f} seconds.")

    # --- Filter ---
    t0 = time.monotonic()
    processed_df, filters_active = apply_filters(args, all_pairs_df)
    t1 = time.monotonic()
    print(f"> Filtering took {t1 - t0:.2f} seconds.")

    # --- Output ---
    t0 = time.monotonic()
    handle_output(args, processed_df, points_data, not args.spherical, filters_active)
    t1 = time.monotonic()
    print(f"> File output took {t1 - t0:.2f} seconds.")

    script_end_time = time.monotonic()
    print(f"\nTotal script execution time: {script_end_time - script_start_time:.2f} seconds.")

if __name__ == "__main__":
    main()
