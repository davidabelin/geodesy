# point_jiggler.py (v1.1)
# Gemi-Assisted Point Position Optimizer

"""
This script uses optimization algorithms to "jiggle" the coordinates of points
in a KML file to best satisfy a set of geometric and/or altitude-based constraints.

Core Concepts:
1.  Objective Function: A master "score" is calculated that represents how
    well the current point configuration meets all desired goals. The optimizer's
    job is to find the coordinates that minimize this score.

2.  Point Groups (from KML Folders): Points are grouped by the KML <Folder>
    they reside in. Points in a folder named "HillPnts" will be flagged for
    altitude optimization. Points in other folders will not.

3.  Geometric Error: A measure of how well the distances and azimuths between
    all pairs of points match a list of targets (e.g., specific distances,
    multiples of a value, side ratios of special triangles).

4.  Altitude Error: For points in the "HillPnts" group, this measures how
    close they are to the highest possible elevation in their local vicinity.
    This error is normalized and weighted.

5.  Jiggle Rating: For now, this is assumed to be a property we can add later.
    The primary control for mobility is whether a point is an anchor or not.

6.  Weights: The user provides weights to balance the importance of the
    geometric goals vs. the altitude-seeking goals.

Basic CLI Usage:
------------------------------------------------
python point_jiggler.py your_points.kml your_targets.csv your_output.kml \
    --dem /path/to/your/dem.tif \
    --geom-weight 1.0 \
    --alt-weight 0.5
------------------------------------------------
"""

import argparse
import numpy as np
import xml.etree.ElementTree as ET
import csv
from scipy.optimize import minimize
from pyproj import Geod
import rasterio
from tqdm import tqdm

# --- Global Geodetic Object ---
# Using WGS84 ellipsoid for accurate calculations
GEOD = Geod(ellps='WGS84')

# ==============================================================================
# 1. DATA LOADING AND PARSING
# ==============================================================================

def parse_kml_folders(kml_path):
    """
    Parses a KML file, reading points and organizing them by their parent folder.
    Points in a folder named "HillPnts" are flagged for altitude optimization.

    Args:
        kml_path (str): Path to the input KML file.

    Returns:
        dict: A dictionary where keys are point names and values are another
              dictionary containing 'coords', 'group', and 'optimize_altitude'.
    """
    points = {}
    ns = {'kml': 'http://www.opengis.net/kml/2.2'}
    try:
        tree = ET.parse(kml_path)
        root = tree.getroot()
        
        # Find all folders in the document
        for folder in root.findall('.//kml:Folder', ns):
            folder_name_node = folder.find('kml:name', ns)
            folder_name = folder_name_node.text.strip() if folder_name_node is not None else "Default"
            
            # Determine if points in this folder should be altitude-optimized
            is_hill_group = (folder_name.lower() == 'hillpnts')
            
            for placemark in folder.findall('kml:Placemark', ns):
                name_node = placemark.find('kml:name', ns)
                if name_node is None:
                    continue
                name = name_node.text.strip()

                coords_node = placemark.find('.//kml:Point/kml:coordinates', ns)
                if coords_node is None or not coords_node.text:
                    continue

                lon_str, lat_str, *_ = coords_node.text.strip().split(',')
                points[name] = {
                    'coords': np.array([float(lat_str), float(lon_str)]),
                    'group': folder_name,
                    'optimize_altitude': is_hill_group,
                    'jiggle_rating': 1 if is_hill_group else 4 # Example: Hills are more mobile
                }
        
        # Handle points not in any folder (in the root document)
        for placemark in root.find('kml:Document', ns).findall('kml:Placemark', ns):
             name_node = placemark.find('kml:name', ns)
             if name_node is None or name_node.text.strip() in points:
                 continue
             name = name_node.text.strip()
             coords_node = placemark.find('.//kml:Point/kml:coordinates', ns)
             if coords_node is not None and coords_node.text:
                 lon_str, lat_str, *_ = coords_node.text.strip().split(',')
                 points[name] = {
                    'coords': np.array([float(lat_str), float(lon_str)]),
                    'group': 'root',
                    'optimize_altitude': False,
                    'jiggle_rating': 4 
                }

        print(f"Loaded {len(points)} points from {kml_path} into groups.")
        return points
    except FileNotFoundError:
        print(f"Error: KML file not found at {kml_path}")
        exit(1)
    except Exception as e:
        print(f"An error occurred parsing the KML file: {e}")
        exit(1)


def load_targets(targets_path):
    """
    Loads geometric targets from a simple CSV file.
    NOTE: This is a simplified placeholder for the initial draft.
    A full implementation would read multiples, factors, etc.
    """
    targets = {'distances': [], 'azimuths': []}
    try:
        with open(targets_path, 'r') as f:
            reader = csv.reader(f)
            next(reader) # Skip header
            for row in reader:
                target_type, value = row[0].strip(), float(row[1])
                if 'dist' in target_type:
                    targets['distances'].append(value)
                elif 'az' in target_type:
                    targets['azimuths'].append(value)
    except FileNotFoundError:
        print(f"Warning: Targets file not found at {targets_path}. Proceeding without geometric targets.")
    except Exception as e:
        print(f"Warning: Could not parse targets file. Error: {e}")
    return targets

# ==============================================================================
# 2. OBJECTIVE FUNCTION (THE "SCORE")
# ==============================================================================

# Global variable to hold a progress bar instance
pbar = None

def calculate_total_error(flat_coords, all_points_data, jiggle_point_names,
                          targets, dem_dataset, weights):
    """
    The main objective function for the optimizer.
    Calculates a single 'score' representing the total error of the system.
    """
    global pbar
    
    # 1. Reconstruct the full coordinate set for this iteration
    current_coords = {name: data['coords'] for name, data in all_points_data.items()}
    jiggled_coords_2d = flat_coords.reshape(len(jiggle_point_names), 2)
    for i, name in enumerate(jiggle_point_names):
        current_coords[name] = jiggled_coords_2d[i]

    all_point_names = list(all_points_data.keys())
    
    # 2. Calculate Geometric Error
    # --- This is a simplified placeholder ---
    geom_error = 0
    num_pairs = 0
    
    for i in range(len(all_point_names)):
        for j in range(i + 1, len(all_point_names)):
            p1_name = all_point_names[i]
            p2_name = all_point_names[j]
            p1 = current_coords[p1_name]
            p2 = current_coords[p2_name]
            
            _, _, dist = GEOD.inv(p1[1], p1[0], p2[1], p2[0])
            dist_miles = dist / 1609.344

            if targets['distances']:
                min_dist_error = min([abs(dist_miles - t) for t in targets['distances']])
                geom_error += min_dist_error
            num_pairs += 1

    normalized_geom_error = geom_error / num_pairs if num_pairs > 0 else 0

    # 3. Calculate Altitude Error
    alt_error = 0
    num_alt_points = 0
    if dem_dataset and weights['alt'] > 0:
        dem_band = dem_dataset.read(1, masked=True)
        min_alt, max_alt = np.nanmin(dem_band), np.nanmax(dem_band)
        alt_range = max_alt - min_alt if max_alt > min_alt else 1
        
        for name, data in all_points_data.items():
            if data['optimize_altitude']:
                coords = (current_coords[name][1], current_coords[name][0]) # (lon, lat)
                # Sample returns a generator
                alt_val = next(dem_dataset.sample([coords]))[0]
                if alt_val != dem_dataset.nodata:
                    # Normalize and make negative
                    normalized_alt = (alt_val - min_alt) / alt_range
                    alt_error += -1 * normalized_alt # Minimize this to maximize altitude
                num_alt_points += 1
    
    normalized_alt_error = alt_error / num_alt_points if num_alt_points > 0 else 0

    # 4. Combine errors with weights
    total_score = (weights['geom'] * normalized_geom_error) + \
                  (weights['alt'] * normalized_alt_error)
    
    if pbar:
        pbar.update(1)
        pbar.set_description(f"Score: {total_score:.6f}")

    return total_score


# ==============================================================================
# 3. MAIN EXECUTION
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Optimize point coordinates based on geometric and altitude constraints.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("input_kml", help="Path to the input KML file with point data organized in Folders.")
    parser.add_argument("targets_csv", help="Path to a CSV file defining geometric targets.")
    parser.add_argument("output_kml", help="Path to save the new KML file with optimized coordinates.")
    parser.add_argument("--dem", help="Path to a DEM file (e.g., GeoTIFF) for altitude optimization.", default=None)
    parser.add_argument("--geom-weight", type=float, default=1.0, help="Weight for the geometric error component.")
    parser.add_argument("--alt-weight", type=float, default=1.0, help="Weight for the altitude error component.")
    parser.add_argument("--max-iterations", type=int, default=100, help="Maximum number of iterations for the optimizer.")
    parser.add_argument("--anchor-groups", nargs='*', default=[], help="List of KML Folder names whose points should NOT be jiggled.")

    args = parser.parse_args()

    # --- Load Data ---
    all_points_data = parse_kml_folders(args.input_kml)
    targets = load_targets(args.targets_csv)
    dem_dataset = None
    if args.dem:
        try:
            dem_dataset = rasterio.open(args.dem)
            print(f"Loaded DEM: {args.dem}")
        except Exception as e:
            print(f"Warning: Could not load DEM file. Altitude optimization will be disabled. Error: {e}")

    # --- Prepare for Optimization ---
    jiggle_points = {
        name: data for name, data in all_points_data.items()
        if data['group'] not in args.anchor_groups
    }
    
    if not jiggle_points:
        print("No points eligible for jiggling. Check your --anchor-groups argument. Exiting.")
        return

    jiggle_point_names = list(jiggle_points.keys())
    initial_coords_flat = np.array([data['coords'] for data in jiggle_points.values()]).flatten()

    bounds = []
    for name in jiggle_point_names:
        rating = jiggle_points[name].get('jiggle_rating', 4)
        jiggle_amount = (5 - rating) * 0.005 # Degrees lat/lon
        lat, lon = jiggle_points[name]['coords']
        bounds.extend([(lat - jiggle_amount, lat + jiggle_amount), 
                       (lon - jiggle_amount, lon + jiggle_amount)])

    # --- Run Optimization ---
    print(f"Starting optimization for {len(jiggle_points)} points...")
    
    global pbar
    pbar = tqdm(total=args.max_iterations, unit="iter")
    
    weights = {'geom': args.geom_weight, 'alt': args.alt_weight}
    
    result = minimize(
        calculate_total_error,
        initial_coords_flat,
        args=(all_points_data, jiggle_point_names, targets, dem_dataset, weights),
        method='L-BFGS-B',
        bounds=bounds,
        options={'maxiter': args.max_iterations, 'disp': True}
    )

    pbar.close()

    # --- Process and Save Results ---
    optimized_coords_flat = result.x
    optimized_coords_2d = optimized_coords_flat.reshape(len(jiggle_points), 2)

    print("\nOptimization complete.")
    print(f"Final score: {result.fun}")

    final_points_data = all_points_data.copy()
    for i, name in enumerate(jiggle_point_names):
        final_points_data[name]['coords'] = optimized_coords_2d[i]

    # TODO: Write a proper KML output function
    print("\n--- Final Optimized Coordinates ---")
    for name, data in sorted(final_points_data.items()):
        print(f"{name} (Group: {data['group']}): Lat={data['coords'][0]:.8f}, Lon={data['coords'][1]:.8f}")
    
    if dem_dataset:
        dem_dataset.close()

if __name__ == "__main__":
    main()
