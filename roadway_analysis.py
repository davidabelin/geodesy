# Version 2.5 Semi Gemi Assisted
import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, MultiLineString
from shapely.ops import linemerge
from geographiclib.geodesic import Geodesic
import warnings
import re
import matplotlib.colors as mcolors
import matplotlib.cm as cm
import simplekml
import os

# --- Configuration ---
GEOJSON_FILE_PATH = r"data\Roadway_SubBlock.geojson"
OUTPUT_KML_PATH = r"data\classified_roadways.kml"
OUTPUT_EW_CSV_PATH = r"data\ew_street_distances.csv"
OUTPUT_NS_CSV_PATH = r"data\ns_street_distances.csv"
ROAD_TYPE_TO_ANALYZE = 'ST'

# --- Tunable Parameters ---
EW_AZIMUTH_RANGE = ((84, 96), (264, 276))
NS_AZIMUTH_RANGE = ((0, 6), (174, 186), (354, 360))
METERS_TO_FEET = 3.28084

warnings.simplefilter(action='ignore', category=pd.errors.SettingWithCopyWarning)

# --- NEW: Smart Sorting Function ---
def get_street_sort_key(street_name):
    """
    Creates a key for sorting streets logically (by quadrant, then type, then value).
    Example: "16TH ST NW" -> ('NW', 1, 16)
             "K ST NW"    -> ('NW', 2, 10) (K is 10th letter, skipping J)
    """
    if not isinstance(street_name, str):
        return ('', 9, '') # Default for non-string names

    # 1. Extract Quadrant
    quadrant_match = re.search(r'\s(NW|NE|SW|SE)$', street_name)
    quadrant = quadrant_match.group(1) if quadrant_match else 'ZZ' # ZZ sorts last

    # 2. Determine Type (Numbered, Lettered, Other) and Value
    num_match = re.match(r'^(\d+)', street_name)
    if num_match:
        return (quadrant, 1, int(num_match.group(1))) # Type 1 for Numbered

    letter_match = re.match(r'^([A-Z])\s', street_name)
    if letter_match:
        letter = letter_match.group(1)
        # Handle the infamous missing 'J' street
        alpha_pos = ord(letter) - ord('A')
        if letter > 'J':
            alpha_pos -= 1
        return (quadrant, 2, alpha_pos) # Type 2 for Lettered

    return (quadrant, 3, street_name) # Type 3 for Named

# --- Styling Function ---
def get_style_color(street_name, other_streets_sorted_list):
    if not isinstance(street_name, str): return "ff808080"
    hex_color = "#808080"
    num_match = re.match(r'^(\d+)', street_name)
    if num_match:
        number = int(num_match.group(1))
        norm = mcolors.Normalize(vmin=1, vmax=60); cmap = cm.plasma
        hex_color = mcolors.to_hex(cmap(norm(number)))
    else:
        letter_match = re.match(r'^([A-Z])\s', street_name)
        if letter_match:
            letter = letter_match.group(1)
            norm = mcolors.Normalize(vmin=0, vmax=25); cmap = cm.viridis
            hex_color = mcolors.to_hex(cmap(norm(ord(letter) - ord('A'))))
        elif street_name in other_streets_sorted_list:
            index = other_streets_sorted_list.index(street_name)
            norm = mcolors.Normalize(vmin=0, vmax=len(other_streets_sorted_list) - 1); cmap = cm.cividis
            hex_color = mcolors.to_hex(cmap(norm(index)))
    return "ff" + hex_color[5:7] + hex_color[3:5] + hex_color[1:3]

# --- Core Geodetic Functions ---
def calculate_azimuth_from_line(line):
    if not isinstance(line, LineString) or len(line.coords) < 2: return 0.0
    g = Geodesic.WGS84.Inverse(line.coords[0][1], line.coords[0][0], line.coords[-1][1], line.coords[-1][0])
    return g['azi1'] % 360

def classify_street(azimuth):
    for r in EW_AZIMUTH_RANGE:
        if r[0] <= azimuth <= r[1]: return 'EW'
    for r in NS_AZIMUTH_RANGE:
        if r[0] <= azimuth <= r[1]: return 'NS'
    return 'Other'

# --- REVISED: Distance Calculation ---
def calculate_and_save_distances_csv(gdf, classification, output_path):
    if gdf.empty: return
    
    # Apply the new smart sorting key
    gdf['sort_key'] = gdf['original_name'].apply(get_street_sort_key)
    sorted_gdf = gdf.sort_values(by='sort_key').reset_index(drop=True)
    
    distance_records = []
    # Group by quadrant to calculate distances only within the same quadrant
    for quadrant, group in sorted_gdf.groupby(lambda i: sorted_gdf.loc[i, 'sort_key'][0]):
        if len(group) < 2: continue
        group = group.reset_index(drop=True)
        for i in range(len(group) - 1):
            street_a_row, street_b_row = group.iloc[i], group.iloc[i+1]
            p1, p2 = street_a_row.centroid, street_b_row.centroid
            dist_m = Geodesic.WGS84.Inverse(p1.y, p1.x, p2.y, p1.x)['s12'] if classification == 'EW' else Geodesic.WGS84.Inverse(p1.y, p1.x, p1.y, p2.x)['s12']
            distance_records.append({
                'StreetA': street_a_row['original_name'],
                'StreetB': street_b_row['original_name'],
                'Distance': round(dist_m * METERS_TO_FEET, 2)
            })
            
    pd.DataFrame(distance_records).to_csv(output_path, index=False)
    print(f"   > Saved {classification} distances to '{output_path}'")

# --- KML Output Function ---
def create_kml_output(gdf, output_path):
    kml = simplekml.Kml(name=f"DC Roadway Analysis ({ROAD_TYPE_TO_ANALYZE})")
    for street_name, group in gdf.groupby('original_name'):
        folder = kml.newfolder(name=street_name)
        for idx, row in group.iterrows():
            placemark = folder.newlinestring(name=row['name'])
            placemark.coords.addcoordinates(row.geometry.coords)
            placemark.altitudemode = simplekml.AltitudeMode.clamptoground
            placemark.style.linestyle.color = row['kml_color']
            placemark.style.linestyle.width = 3
    print(f"6. Saving KML file to '{output_path}'...")
    kml.save(output_path)

# --- Main Execution Logic ---
if __name__ == "__main__":
    print(f"1. Loading GeoJSON and filtering for roadway type: '{ROAD_TYPE_TO_ANALYZE}'...")
    if not os.path.exists('data'):
        print("FATAL: 'data' directory not found.")
        exit()
    try:
        gdf = gpd.read_file(GEOJSON_FILE_PATH)
    except Exception as e:
        print(f"FATAL: Error loading GeoJSON file: {e}")
        exit()

    TYPE_KEY, NAME_KEY = 'STREETTYPE', 'ROUTENAME'
    if TYPE_KEY not in gdf.columns or NAME_KEY not in gdf.columns:
        print(f"FATAL: Required columns ('{TYPE_KEY}', '{NAME_KEY}') not found.")
        exit()

    gdf_filtered = gdf[gdf[TYPE_KEY] == ROAD_TYPE_TO_ANALYZE].copy()

    print("2. Merging roadway segments...")
    merged_roadways = []
    for name, group in gdf_filtered.groupby(NAME_KEY):
        merged_line = linemerge(MultiLineString(list(group.geometry)))
        if isinstance(merged_line, LineString):
            merged_roadways.append({'original_name': name, 'geometry': merged_line})
        elif isinstance(merged_line, MultiLineString):
            for i, part in enumerate(merged_line.geoms):
                merged_roadways.append({'original_name': name, 'part': i, 'geometry': part})
    merged_gdf = gpd.GeoDataFrame(merged_roadways, geometry='geometry')

    print("3. Calculating orientation and classifying roadways...")
    merged_gdf['azimuth'] = merged_gdf['geometry'].apply(calculate_azimuth_from_line)
    merged_gdf['classification'] = merged_gdf['azimuth'].apply(classify_street)
    classified_gdf = merged_gdf[merged_gdf['classification'].isin(['EW', 'NS'])].copy()
    classified_gdf['name'] = classified_gdf.apply(
        lambda row: f"{row['original_name']} Part-{row.get('part', 0)}", axis=1
    )
    classified_gdf['centroid'] = classified_gdf.geometry.centroid
    print(f"   > {len(classified_gdf)} roadways classified as EW or NS.")

    print("4. Applying color styling for KML output...")
    all_names = classified_gdf['original_name'].unique()
    other_streets = sorted([
        name for name in all_names if name and not re.match(r'^(\d+)', name) and not re.match(r'^([A-Z])\s', name)
    ])
    classified_gdf['kml_color'] = classified_gdf['original_name'].apply(
        lambda name: get_style_color(name, other_streets)
    )

    print("5. Calculating distances and saving to CSV...")
    ew_streets = classified_gdf[classified_gdf['classification'] == 'EW']
    ns_streets = classified_gdf[classified_gdf['classification'] == 'NS']
    calculate_and_save_distances_csv(ew_streets, 'EW', OUTPUT_EW_CSV_PATH)
    calculate_and_save_distances_csv(ns_streets, 'NS', OUTPUT_NS_CSV_PATH)
    
    create_kml_output(classified_gdf, OUTPUT_KML_PATH)

    print("\n--- Analysis Complete ---")
    print("\nDone.")
