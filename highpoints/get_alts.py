import csv
import os
import rasterio
import rasterio.crs
import rasterio.warp
import geojson

# --- Configuration ---
# Absolute path to your input CSV file with point data
CSV_FILE_PATH = r"data\crosspnts.csv"  # r"data\map_refpnts.csv"
# Absolute path to your Digital Elevation Model (DEM) GeoTIFF file
DEM_FILE_PATH = r"C:\Users\David\Documents\Local_Python\geodesy\data\tif\dc_dem.tif"
# Absolute path for the output GeoJSON file
GEOJSON_OUTPUT_PATH = r"coverage\input\crosspnts.geojson" 

# EPSG code for the coordinate system of your input LAT/LON points
# NAD83 geographic coordinates (latitude/longitude)
#INPUT_POINTS_EPSG = 4269 # For NAD83
# For WGS84, use:
INPUT_POINTS_EPSG = 4326 # For WGS84

# Conversion factor
METERS_TO_FEET = 3.28084
# --- End Configuration ---

def get_altitudes_for_points(csv_path, dem_path, input_points_epsg):
    """
    Reads points from a CSV, queries their elevations from a DEM,
    converts elevations to feet, and returns a list of dictionaries.
    """
    input_lon_lat_coords = []  # Stores (lon, lat) tuples from CSV
    initial_point_data = []    # Stores original data like LOC, LAT, LON

    print(f"Reading points from: {csv_path}")
    try:
        with open(csv_path, 'r', newline='') as csvfile:
            reader = csv.DictReader(csvfile)
            if not reader.fieldnames or not all(col in reader.fieldnames for col in ['LOC', 'LAT', 'LON']):
                print(f"Error: CSV file must contain 'LOC', 'LAT', 'LON' columns. Found: {reader.fieldnames}")
                return []

            for row_num, row in enumerate(reader, 1):
                try:
                    loc = row['LOC']
                    lat = float(row['LAT'])
                    lon = float(row['LON'])
                    
                    input_lon_lat_coords.append((lon, lat))
                    initial_point_data.append({'LOC': loc, 'LAT': lat, 'LON': lon, 'OriginalRow': row_num})
                except (KeyError, ValueError) as e:
                    print(f"Skipping row {row_num} due to error: {row} - {e}")
                    continue
    except FileNotFoundError:
        print(f"Error: CSV file not found at {csv_path}")
        return []
    except Exception as e:
        print(f"An unexpected error occurred while reading {csv_path}: {e}")
        return []
    
    if not input_lon_lat_coords:
        print("No valid points found or read from CSV file.")
        return []

    print(f"Querying elevations from DEM: {dem_path}")
    processed_points_with_alt = []

    try:
        with rasterio.open(dem_path) as src_dem:
            # Define the CRS of the input points
            points_crs_input = rasterio.crs.CRS.from_epsg(input_points_epsg)
            print(f"Assuming input point coordinates are in CRS: {points_crs_input.to_string()} (EPSG:{input_points_epsg})")
            print(f"DEM CRS is: {src_dem.crs.to_string()}")

            coords_for_sampling = input_lon_lat_coords 

            # Transform coordinates if DEM's CRS is different from the input points' CRS
            if src_dem.crs != points_crs_input:
                print(f"DEM CRS ({src_dem.crs}) differs from input points CRS ({points_crs_input}). Transforming points for DEM sampling...")
                lons = [p[0] for p in input_lon_lat_coords]
                lats = [p[1] for p in input_lon_lat_coords]
                
                transformed_coords = rasterio.warp.transform(
                    points_crs_input,    # Source CRS (e.g., NAD83)
                    src_dem.crs,         # Destination CRS (DEM's CRS)
                    lons,                # List of longitudes
                    lats                 # List of latitudes
                )
                # Re-zip into (x, y) tuples for sampling
                coords_for_sampling = list(zip(transformed_coords[0], transformed_coords[1]))
            
            # Sample DEM for elevations (returns a generator)
            # ** The values are typically in meters for DEMs **
            sampled_elevations_m_raw = src_dem.sample(coords_for_sampling)
            
            # Extract the first band value for each point
            # and combine with original data
            for i, base_data in enumerate(initial_point_data):
                elevation_m_array = next(sampled_elevations_m_raw) # Get the numpy array for the point
                elevation_m = elevation_m_array[0] # Get the first (and only) band value
                
                alt_ft = None
                valid_elevation_m = None 

                # Check if the sampled elevation is the NoData value for the DEM
                if src_dem.nodata is not None and elevation_m == src_dem.nodata:
                    print(f"Warning: Point {base_data['LOC']} (Lat: {base_data['LAT']}, Lon: {base_data['LON']}) is on a NoData pixel or outside DEM extent. Elevation set to N/A.")
                elif elevation_m is None or elevation_m < -10000: # Arbitrary large negative for potential other NoData markers
                    print(f"Warning: Point {base_data['LOC']} (Lat: {base_data['LAT']}, Lon: {base_data['LON']}) has an invalid elevation value ({elevation_m}). Elevation set to N/A.")
                else:
                    try:
                        valid_elevation_m = float(elevation_m)
                        alt_ft = valid_elevation_m * METERS_TO_FEET
                    except (ValueError, TypeError):
                         print(f"Warning: Point {base_data['LOC']} (Lat: {base_data['LAT']}, Lon: {base_data['LON']}) has a non-numeric elevation value ({elevation_m}). Elevation set to N/A.")


                processed_points_with_alt.append({
                    **base_data, 
                    'ALT_M': valid_elevation_m, 
                    'ALT_FT': alt_ft            
                })
                
    except FileNotFoundError:
        print(f"Error: DEM file not found at {dem_path}")
        return []
    except rasterio.errors.RasterioIOError as e:
        print(f"Error opening or reading DEM file {dem_path}: {e}")
        return []
    except Exception as e:
        print(f"An unexpected error occurred during DEM processing: {e}")
        return []
            
    return processed_points_with_alt

def main():
    # Ensure output directory exists (though for a file in 'data' it likely does)
    output_dir = os.path.dirname(GEOJSON_OUTPUT_PATH)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f"Created output directory: {output_dir}")

    # Process points to get altitudes
    points_with_altitudes = get_altitudes_for_points(CSV_FILE_PATH, DEM_FILE_PATH, INPUT_POINTS_EPSG)

    if not points_with_altitudes:
        print("No data processed. Exiting.")
        return

    # Print results in CSV format to console
    #print("\n--- Output in CSV format (to console) ---")
    print("LOC,LAT,LON,ALT_M") # Header
    for point in points_with_altitudes:
        alt_ft_str = f"{point['ALT_FT']:.3f}" if point['ALT_FT'] is not None else "N/A"
        alt_m_str = f"{point['ALT_M']:.3f}" if point['ALT_M'] is not None else "N/A"
        lat_str = f"{point['LAT']:.7f}" # Consistent formatting
        lon_str = f"{point['LON']:.7f}" # Consistent formatting
        print(f"{point['LOC']},{lat_str},{lon_str},{alt_m_str}")

    # Create GeoJSON features
    geojson_features = []
    for point in points_with_altitudes:
        # Properties for GeoJSON. Ensure ALT_M and ALT_FT are rounded or None
        properties = {
            'LOC': point['LOC'],
            'LAT_Orig': point['LAT'], # Original LAT from CSV
            'LON_Orig': point['LON'], # Original LON from CSV
            'ALT_M': round(point['ALT_M'], 3) if point['ALT_M'] is not None else None,
            'ALT_FT': round(point['ALT_FT'], 3) if point['ALT_FT'] is not None else None
        }
        # GeoJSON geometry uses (longitude, latitude) order
        feature = geojson.Feature(
            geometry=geojson.Point((point['LON'], point['LAT'])), 
            properties=properties
        )
        geojson_features.append(feature)
    
    feature_collection = geojson.FeatureCollection(geojson_features)
    
    try:
        with open(GEOJSON_OUTPUT_PATH, 'w') as f:
            geojson.dump(feature_collection, f, indent=2) # indent for pretty printing
        print(f"\nGeoJSON output successfully saved to: {GEOJSON_OUTPUT_PATH}")
    except IOError as e:
        print(f"Error: Could not write GeoJSON file to {GEOJSON_OUTPUT_PATH}: {e}")
    except Exception as e:
        print(f"An unexpected error occurred while saving GeoJSON: {e}")


if __name__ == "__main__":
    print("Starting script to get point altitudes...")
    main()
    print("Script finished.")
