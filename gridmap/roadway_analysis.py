import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, MultiLineString
from geographiclib.geodesic import Geodesic

# --- Configuration ---
# Use the direct link to the GeoJSON file for portability
# Drive Link: GEOJSON_FILE_PATH = 'https://drive.google.com/uc?id=1CoOE6yiT3S-NNQLXnXWOgkMJaU1VaBdv'
GEOJSON_FILE_PATH = r"data\Roadway_SubBlock.geojson"
# Define the type of roadway to analyze ('ST', 'AVE', etc.)
# "STREETTYPE": "ST"
ROAD_TYPE_TO_ANALYZE = 'ST'

# Azimuth tolerance in degrees for splitting a street into different objects
AZIMUTH_CHANGE_TOLERANCE = 3
# Spatial tolerance in meters. If segments are further apart than this, they are a new street object.
SPATIAL_GAP_TOLERANCE = 50 # meters
# Define azimuth ranges for classification (in degrees)
EW_AZIMUTH_RANGE = ((84, 96), (264, 276))
NS_AZIMUTH_RANGE = ((0, 6), (174, 186), (354, 360))
# Conversion factor
METERS_TO_FEET = 3.28084

# --- Main Functions ---

def calculate_azimuth(point1, point2):
    """Calculates the forward azimuth between two points (lat/lon)."""
    # Geodesic object for WGS84 ellipsoid
    geod = Geodesic.WGS84
    # Solve the inverse geodesic problem
    g = geod.Inverse(point1.y, point1.x, point2.y, point2.x)
    # Return azimuth, ensuring it's in the range [0, 360)
    return g['azi1'] % 360

def get_line_properties(line):
    """Calculates the length and azimuth of a LineString segment."""
    length = Geodesic.WGS84.Inverse(line.coords[0][1], line.coords[0][0], line.coords[-1][1], line.coords[-1][0])['s12']
    azimuth = calculate_azimuth(line.boundary.geoms[0], line.boundary.geoms[1])
    return length, azimuth

def get_weighted_azimuth(df_group):
    """Calculates the length-weighted average azimuth for a group of segments."""
    # Ensure azimuths are handled correctly across the 0/360 degree boundary
    rads = np.deg2rad(df_group['azimuth'])
    avg_sin = np.average(np.sin(rads), weights=df_group['length'])
    avg_cos = np.average(np.cos(rads), weights=df_group['length'])
    weighted_azimuth = np.rad2deg(np.arctan2(avg_sin, avg_cos))
    return weighted_azimuth % 360

def classify_street(azimuth):
    """Classifies a street as 'EW' or 'NS' based on its azimuth."""
    for r in EW_AZIMUTH_RANGE:
        if r[0] <= azimuth <= r[1]:
            return 'EW'
    for r in NS_AZIMUTH_RANGE:
        if r[0] <= azimuth <= r[1]:
            return 'NS'
    return 'Other'

def calculate_centroid_distances(gdf, classification):
    """
    Calculates the distances in feet between consecutive streets of a given classification.
    """
    if gdf.empty:
        return []

    # Sort by latitude for EW streets, longitude for NS streets
    sort_by_coord = 'lat' if classification == 'EW' else 'lon'
    sorted_gdf = gdf.sort_values(by=sort_by_coord).reset_index(drop=True)

    distances_ft = []
    for i in range(len(sorted_gdf) - 1):
        # Get centroids of consecutive streets
        p1 = sorted_gdf.iloc[i]['centroid']
        p2 = sorted_gdf.iloc[i+1]['centroid']

        # Use the appropriate coordinate for distance calculation
        if classification == 'EW':
            # Distance between latitudes (point1_lat, point1_lon -> point2_lat, point1_lon)
            dist_m = Geodesic.WGS84.Inverse(p1.y, p1.x, p2.y, p1.x)['s12']
        else: # NS
            # Distance between longitudes (point1_lat, point1_lon -> point1_lat, point2_lon)
            dist_m = Geodesic.WGS84.Inverse(p1.y, p1.x, p1.y, p2.x)['s12']

        distances_ft.append(dist_m * METERS_TO_FEET)

    return distances_ft

# --- Main Execution Logic ---

if __name__ == "__main__":
    print(f"1. Loading and filtering GeoJSON for roadway type: '{ROAD_TYPE_TO_ANALYZE}'...")
    # Load the data
    gdf = gpd.read_file(GEOJSON_FILE_PATH)
    # Filter for specific road types (e.g., 'ST' for streets)
    gdf_filtered = gdf[gdf['STREETTYPE'] == ROAD_TYPE_TO_ANALYZE].copy()

    print("2. Calculating properties for each road segment...")
    # Calculate length and azimuth for every individual segment
    properties = [get_line_properties(geom) for geom in gdf_filtered.geometry]
    gdf_filtered['length'] = [p[0] for p in properties]
    gdf_filtered['azimuth'] = [p[1] for p in properties]

    print("3. Grouping segments into continuous roadway objects...")
    processed_roadways = []
    # Group by the full street name
    for name, group in gdf_filtered.groupby('FULLNAME'):
        # Sort segments by their position to walk along the street
        group = group.sort_values('length', ascending=False).reset_index(drop=True) # Heuristic sort
        
        street_part_counter = 0
        current_part_indices = []
        
        # Iterate through segments to find discontinuities
        last_azimuth = -1
        for index, row in group.iterrows():
            if not current_part_indices:
                current_part_indices.append(index)
                last_azimuth = row['azimuth']
                continue

            # Check for significant azimuth change
            azimuth_diff = 180 - abs(abs(row['azimuth'] - last_azimuth) - 180)
            if azimuth_diff > AZIMUTH_CHANGE_TOLERANCE:
                # Discontinuity found, finalize the previous part
                processed_roadways.append(group.loc[current_part_indices])
                street_part_counter += 1
                current_part_indices = [index]
            else:
                current_part_indices.append(index)
            last_azimuth = row['azimuth']
        
        # Add the last part
        if current_part_indices:
            processed_roadways.append(group.loc[current_part_indices])

    print(f"   Found {len(gdf_filtered)} segments, processed into {len(processed_roadways)} continuous roadway objects.")

    print("4. Calculating true orientation and classifying roadways...")
    final_roadways = []
    for i, roadway_df in enumerate(processed_roadways):
        if roadway_df.empty:
            continue
        
        # Combine geometries of all segments in the part
        combined_geom = MultiLineString(list(roadway_df.geometry))
        
        final_roadways.append({
            'name': f"{roadway_df['FULLNAME'].iloc[0]} {i}",
            'true_azimuth': get_weighted_azimuth(roadway_df),
            'total_length': roadway_df['length'].sum(),
            'centroid': combined_geom.centroid,
            'geometry': combined_geom
        })

    final_gdf = gpd.GeoDataFrame(final_roadways, geometry='geometry')
    final_gdf['classification'] = final_gdf['true_azimuth'].apply(classify_street)
    final_gdf['lat'] = final_gdf['centroid'].apply(lambda p: p.y)
    final_gdf['lon'] = final_gdf['centroid'].apply(lambda p: p.x)
    
    # Filter out unclassified streets
    classified_gdf = final_gdf[final_gdf['classification'].isin(['EW', 'NS'])]
    print(f"   {len(classified_gdf)} roadways classified as EW or NS.")

    print("5. Calculating distances between streets...")
    ew_streets = classified_gdf[classified_gdf['classification'] == 'EW']
    ns_streets = classified_gdf[classified_gdf['classification'] == 'NS']

    ew_distances_ft = calculate_centroid_distances(ew_streets, 'EW')
    ns_distances_ft = calculate_centroid_distances(ns_streets, 'NS')

    print("\n--- Analysis Complete ---")
    print(f"\nDistances between consecutive East-West streets (in feet):")
    print([round(d, 2) for d in ew_distances_ft])
    
    print(f"\nDistances between consecutive North-South streets (in feet):")
    print([round(d, 2) for d in ns_distances_ft])

    # Optional: Save results to a file
    # classified_gdf.to_file("classified_roadways.geojson", driver='GeoJSON')
    print("\nDone.")