# Version 2.3 Semi Gemi Assisted
import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, MultiLineString
from geographiclib.geodesic import Geodesic
import warnings
import re
import matplotlib.colors as mcolors
import matplotlib.cm as cm
import simplekml
import os

# --- Configuration ---
# Local file path for the GeoJSON data
GEOJSON_FILE_PATH = r"data\Roadway_SubBlock.geojson"
# Output file path for the KML
OUTPUT_KML_PATH = r"data\classified_roadways.kml"
# Define the type of roadway to analyze ('ST', 'AVE', etc.)
ROAD_TYPE_TO_ANALYZE = 'ST'

# --- Tunable Parameters ---
# INCREASED TOLERANCE to reduce the number of street divisions.
# A higher value is more forgiving of curves.
AZIMUTH_CHANGE_TOLERANCE = 20
# Define azimuth ranges for classification (in degrees)
EW_AZIMUTH_RANGE = ((84, 96), (264, 276))
NS_AZIMUTH_RANGE = ((0, 6), (174, 186), (354, 360))

# Conversion factor
METERS_TO_FEET = 3.28084

# Suppress pandas SettingWithCopyWarning
warnings.simplefilter(action='ignore', category=pd.errors.SettingWithCopyWarning)

# --- Styling Function ---

def get_style_color(street_name):
    """Generates a KML-compatible hex color code (aabbggrr)."""
    if not isinstance(street_name, str):
        return "ff808080"  # Default gray

    # Use matplotlib to get a standard hex color (e.g., #RRGGBB)
    hex_color = "#808080" # Default gray
    num_match = re.match(r'^(\d+)', street_name)
    if num_match:
        number = int(num_match.group(1))
        norm = mcolors.Normalize(vmin=1, vmax=60)
        cmap = cm.plasma
        hex_color = mcolors.to_hex(cmap(norm(number)))
    else:
        letter_match = re.match(r'^([A-Z])\s', street_name)
        if letter_match:
            letter = letter_match.group(1)
            norm = mcolors.Normalize(vmin=0, vmax=25)
            cmap = cm.viridis
            hex_color = mcolors.to_hex(cmap(norm(ord(letter) - ord('A'))))

    # Convert #RRGGBB to KML's aabbggrr format
    return "ff" + hex_color[5:7] + hex_color[3:5] + hex_color[1:3]

# --- Core Geodetic Functions ---

def calculate_azimuth(point1, point2):
    """Calculates the forward azimuth between two points."""
    geod = Geodesic.WGS84
    g = geod.Inverse(point1.y, point1.x, point2.y, point2.x)
    return g['azi1'] % 360

def get_line_properties(line):
    """Calculates the length and azimuth of a LineString segment."""
    if not isinstance(line, LineString) or len(line.coords) < 2 or line.is_empty:
        return 0.0, 0.0
    if line.coords[0] == line.coords[-1]:
        return 0.0, 0.0
    try:
        length = Geodesic.WGS84.Inverse(line.coords[0][1], line.coords[0][0], line.coords[-1][1], line.coords[-1][0])['s12']
        if hasattr(line.boundary, 'geoms') and len(line.boundary.geoms) >= 2:
            azimuth = calculate_azimuth(line.boundary.geoms[0], line.boundary.geoms[1])
        else:
            p1, p2 = gpd.points_from_xy([line.coords[0][0]], [line.coords[0][1]]), gpd.points_from_xy([line.coords[-1][0]], [line.coords[-1][1]])
            azimuth = calculate_azimuth(p1[0], p2[0])
        return length, azimuth
    except Exception:
        return 0.0, 0.0

def get_weighted_azimuth(df_group):
    """Calculates the length-weighted average azimuth."""
    rads = np.deg2rad(df_group['azimuth'])
    avg_sin = np.average(np.sin(rads), weights=df_group['length'])
    avg_cos = np.average(np.cos(rads), weights=df_group['length'])
    return np.rad2deg(np.arctan2(avg_sin, avg_cos)) % 360

def classify_street(azimuth):
    """Classifies a street as 'EW' or 'NS'."""
    for r in EW_AZIMUTH_RANGE:
        if r[0] <= azimuth <= r[1]: return 'EW'
    for r in NS_AZIMUTH_RANGE:
        if r[0] <= azimuth <= r[1]: return 'NS'
    return 'Other'

def calculate_centroid_distances(gdf, classification):
    """Calculates distances in feet between consecutive streets."""
    if gdf.empty: return []
    sort_by_coord = 'lat' if classification == 'EW' else 'lon'
    sorted_gdf = gdf.sort_values(by=sort_by_coord).reset_index(drop=True)
    distances_ft = []
    for i in range(len(sorted_gdf) - 1):
        p1, p2 = sorted_gdf.iloc[i]['centroid'], sorted_gdf.iloc[i+1]['centroid']
        dist_m = Geodesic.WGS84.Inverse(p1.y, p1.x, p2.y, p1.x)['s12'] if classification == 'EW' else Geodesic.WGS84.Inverse(p1.y, p1.x, p1.y, p2.x)['s12']
        distances_ft.append(dist_m * METERS_TO_FEET)
    return distances_ft

# --- KML Output Function ---
def create_kml_output(gdf, output_path):
    """Creates a styled KML file with streets grouped into folders."""
    kml = simplekml.Kml(name=f"DC Roadway Analysis ({ROAD_TYPE_TO_ANALYZE})")

    # Group by the original street name to create folders
    for street_name, group in gdf.groupby('original_name'):
        folder = kml.newfolder(name=street_name)
        for idx, row in group.iterrows():
            # Create a placemark for each street part
            placemark = folder.newlinestring(name=row['name'])
            
            # Extract coordinates from the geometry
            if isinstance(row.geometry, MultiLineString):
                for line in row.geometry.geoms:
                    placemark.coords.addcoordinates(line.coords)
            else: # LineString
                placemark.coords.addcoordinates(row.geometry.coords)

            # --- THIS IS THE FIX FOR VISIBILITY ---
            # Drape the line on the Earth's surface
            placemark.altitudemode = simplekml.AltitudeMode.clamptoground
            
            # Apply styling
            placemark.style.linestyle.color = row['kml_color']
            placemark.style.linestyle.width = 3 # Make lines thicker

    print(f"7. Saving KML file to '{output_path}'...")
    kml.save(output_path)


# --- Main Execution Logic ---

if __name__ == "__main__":
    print(f"1. Loading GeoJSON and filtering for roadway type: '{ROAD_TYPE_TO_ANALYZE}'...")
    # Ensure the 'data' directory exists
    if not os.path.exists('data'):
        print("FATAL: 'data' directory not found. Please create it and place the GeoJSON file inside.")
        exit()
    try:
        gdf = gpd.read_file(GEOJSON_FILE_PATH)
    except Exception as e:
        print(f"FATAL: Error loading GeoJSON file: {e}")
        exit()

    TYPE_KEY = 'STREETTYPE'
    NAME_KEY = 'ROUTENAME'
    if TYPE_KEY not in gdf.columns or NAME_KEY not in gdf.columns:
        print(f"FATAL: Required columns ('{TYPE_KEY}', '{NAME_KEY}') not found.")
        exit()

    gdf_filtered = gdf[gdf[TYPE_KEY] == ROAD_TYPE_TO_ANALYZE].copy()

    print("2. Calculating properties for each road segment...")
    properties = [get_line_properties(geom) for geom in gdf_filtered.geometry]
    gdf_filtered['length'] = [p[0] for p in properties]
    gdf_filtered['azimuth'] = [p[1] for p in properties]
    gdf_filtered = gdf_filtered[gdf_filtered['length'] > 0.1].copy()

    print("3. Grouping segments into continuous roadway objects...")
    processed_roadways = []
    for name, group in gdf_filtered.groupby(NAME_KEY):
        group = group.sort_values('length', ascending=False).reset_index(drop=True)
        current_part_indices = []
        last_azimuth = -1
        for index, row in group.iterrows():
            if not current_part_indices:
                current_part_indices.append(index)
                last_azimuth = row['azimuth']
                continue
            azimuth_diff = 180 - abs(abs(row['azimuth'] - last_azimuth) - 180)
            if azimuth_diff > AZIMUTH_CHANGE_TOLERANCE and current_part_indices:
                processed_roadways.append(group.loc[current_part_indices])
                current_part_indices = [index]
            else:
                current_part_indices.append(index)
            last_azimuth = row['azimuth']
        if current_part_indices:
            processed_roadways.append(group.loc[current_part_indices])

    print(f"   > Processed into {len(processed_roadways)} continuous roadway objects.")

    print("4. Calculating true orientation and classifying roadways...")
    final_roadways = []
    for i, roadway_df in enumerate(processed_roadways):
        if roadway_df.empty: continue
        full_name = roadway_df[NAME_KEY].iloc[0]
        combined_geom = MultiLineString(list(roadway_df.geometry))
        final_roadways.append({
            'name': f"{full_name} Part-{i}",
            'original_name': full_name,
            'true_azimuth': get_weighted_azimuth(roadway_df),
            'geometry': combined_geom
        })

    final_gdf = gpd.GeoDataFrame(final_roadways, geometry='geometry')
    final_gdf['classification'] = final_gdf['true_azimuth'].apply(classify_street)
    final_gdf['lat'] = final_gdf['centroid'].apply(lambda p: p.y)
    final_gdf['lon'] = final_gdf['centroid'].apply(lambda p: p.x)
    classified_gdf = final_gdf[final_gdf['classification'].isin(['EW', 'NS'])].copy()
    print(f"   > {len(classified_gdf)} roadways classified as EW or NS.")

    print("5. Calculating distances between streets...")
    ew_streets = classified_gdf[classified_gdf['classification'] == 'EW']
    ns_streets = classified_gdf[classified_gdf['classification'] == 'NS']
    ew_distances_ft = calculate_centroid_distances(ew_streets, 'EW')
    ns_distances_ft = calculate_centroid_distances(ns_streets, 'NS')
    
    print("6. Applying color styling for KML output...")
    classified_gdf['kml_color'] = classified_gdf['original_name'].apply(get_style_color)

    # --- Generate KML Output ---
    create_kml_output(classified_gdf, OUTPUT_KML_PATH)

    print("\n--- Analysis Complete ---")
    print("\nDone.")
