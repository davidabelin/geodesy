# 
# originally:  "C:\Users\David\Documents\Local_Python\geodesy\highpoints.py" v4.5
# 
# originally:  "C:\Users\David\Documents\Local_Python\geodesy\highpoints.py" v4.5

import rasterio
import numpy as np
from rasterio.mask import mask
import geopandas as gpd
from shapely.geometry import Polygon, Point
from pyproj import Transformer
from rasterio.warp import calculate_default_transform, reproject, Resampling
import argparse
#from triangle_data import locs  # Assumes locs maps keys "N", "S", "E", "W", "C" to (lat, lon) tuples

locs = {
    "N": (38.995957,-77.040986),
    "S": (38.790339,-77.040586),
    "E": (38.89286,-76.909166),
    "W": (38.893245,-77.172301),
    "C": (38.8931003,-77.0407598)
}

def get_top_ten_peaks_quadrant(dem_path, triad, buffer_m=100, separation_m=100):
    """
    Finds the top ten peaks within a quadrant defined by a triad of vertices.
    The triangle (defined by triad vertices in order) is buffered by buffer_m meters,
    then the DEM is masked to that area. A circular exclusion (of radius separation_m)
    is used to ensure peaks are not too close together.
    
    All distance calculations (buffering and separation) are done in meters.
    
    Args:
        dem_path (str): Path to the DEM file.
        triad (list of str): Three keys (e.g. ["N", "W", "C"]) defining the quadrant.
        buffer_m (float): Buffer distance in meters (default 100).
        separation_m (float): Minimum separation distance in meters between peaks (default 100).

    Returns:
        list: A list of dictionaries for each peak (up to 10) with keys:
              "rank", "lat", "lon", and "alt".
    """
    try:
        with rasterio.open(dem_path) as dataset:
            # Build the triangle from the provided triad.
            coords = []
            for card in triad:
                lat, lon = locs[card]
                coords.append((lon, lat))
            if coords[0] != coords[-1]:
                coords.append(coords[0])
            triangle = Polygon(coords)

            # Create a GeoDataFrame for the triangle in EPSG:4326 for WGS 84, 4269 for NAD83
            tri_gdf = gpd.GeoDataFrame(geometry=[triangle], crs="EPSG:4269")
            # Determine a projected CRS for buffering.
            # If the DEM is geographic, use EPSG:32618 (UTM Zone 18N, appropriate for DC).
            if not dataset.crs.is_projected:
                proj_crs = "EPSG:26985" #"EPSG:32618"
            else:
                proj_crs = dataset.crs

            # Reproject the triangle to the projected CRS, buffer it, then reproject back.
            tri_proj = tri_gdf.to_crs(proj_crs)
            buffered_proj = tri_proj.geometry.buffer(buffer_m).iloc[0]
            buffered_poly = gpd.GeoSeries([buffered_proj], crs=proj_crs).to_crs(dataset.crs).iloc[0]

            # Mask the DEM using the buffered polygon.
            shapes = [buffered_poly.__geo_interface__]
            dem_masked, mask_transform = mask(dataset, shapes, crop=True)
            dem_masked = dem_masked[0].astype(float)
            nodata = dataset.nodata
            if nodata is not None:
                dem_masked[dem_masked == nodata] = np.nan

            # If the DEM is in a geographic CRS, reproject the masked DEM so that pixel sizes are in meters.
            if not dataset.crs.is_projected:
                dst_crs = proj_crs
                height, width = dem_masked.shape
                src_bounds = rasterio.transform.array_bounds(height, width, mask_transform)
                dst_transform, dst_width, dst_height = calculate_default_transform(
                    dataset.crs, dst_crs, width, height, *src_bounds)
                dem_projected = np.empty((dst_height, dst_width), dtype=dem_masked.dtype)
                reproject(
                    source=dem_masked,
                    destination=dem_projected,
                    src_transform=mask_transform,
                    src_crs=dataset.crs,
                    dst_transform=dst_transform,
                    dst_crs=dst_crs,
                    resampling=Resampling.nearest)
                working_transform = dst_transform
                dem_working = dem_projected.copy()
                working_crs = dst_crs
            else:
                working_transform = mask_transform
                dem_working = dem_masked.copy()
                working_crs = dataset.crs

            # Determine pixel size in meters (assumes square pixels).
            pixel_size = abs(working_transform.a)
            separation_pixels = max(1, int(separation_m / pixel_size))

            # Precompute a circular exclusion mask.
            y_grid, x_grid = np.ogrid[-separation_pixels:separation_pixels+1,
                                        -separation_pixels:separation_pixels+1]
            circle_mask = x_grid**2 + y_grid**2 <= separation_pixels**2

            peaks = []
            dem_iter = dem_working.copy()
            # ... inside the loop ...
            for rank in range(1, 11):
                if np.isnan(dem_iter).all():
                    break

                # --- NEW IMPROVED RANDOMIZATION LOGIC ---

                # 1. Find the true maximum elevation value in the current array.
                max_elevation = np.nanmax(dem_iter)

                # 2. Find the array indices (row, col) of ALL pixels that
                #    are tied for this maximum value.
                tie_indices = np.where(dem_iter == max_elevation)

                # 3. From the list of all tied peaks, randomly select ONE to be
                #    the winner for this round.
                num_ties = len(tie_indices[0])
                random_winner_index = np.random.randint(0, num_ties)

                row = tie_indices[0][random_winner_index]
                col = tie_indices[1][random_winner_index]
                
                # --- END OF NEW IMPROVED RANDOMIZATION LOGIC ---

                peak_alt = dem_iter[row, col]
                # Convert array indices to coordinates in the working CRS.
                x_work, y_work = rasterio.transform.xy(working_transform, row, col)
                # Transform the coordinates to EPSG:4326.
                transformer = Transformer.from_crs(working_crs, "EPSG:4326", always_xy=True)
                lon_wgs, lat_wgs = transformer.transform(x_work, y_work)
                peaks.append({"rank": rank, "lat": lat_wgs, "lon": lon_wgs, "alt": float(peak_alt)})
 
                # Apply the circular exclusion mask with a random jitter.
                # This correctly moves the entire exclusion zone instead of skewing it.
                row_offset = (np.random.rand() - 0.5) * separation_pixels
                col_offset = (np.random.rand() - 0.5) * separation_pixels
 
                # Define the floating-point center of the exclusion circle (original peak + jitter)
                center_row_float = row + row_offset
                center_col_float = col + col_offset
 
                # Calculate integer bounds for the slice around the jittered center
                row_min = int(max(0, center_row_float - separation_pixels))
                row_max = int(min(dem_iter.shape[0], center_row_float + separation_pixels + 1))
                col_min = int(max(0, center_col_float - separation_pixels))
                col_max = int(min(dem_iter.shape[1], center_col_float + separation_pixels + 1))
 
                # Calculate the correct slice from the pre-computed circle_mask
                mask_row_start = row_min - int(center_row_float - separation_pixels)
                mask_row_end = mask_row_start + (row_max - row_min)
                mask_col_start = col_min - int(center_col_float - separation_pixels)
                mask_col_end = mask_col_start + (col_max - col_min)
                dem_iter[row_min:row_max, col_min:col_max][
                    circle_mask[mask_row_start:mask_row_end, mask_col_start:mask_col_end]
                ] = np.nan

            return peaks

    except Exception as e:
        print(f"Error processing quadrant {triad}: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(
        description="Finds the top ten highest points within defined polygonal areas of a Digital Elevation Model (DEM).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("dem_file", help="Path to the input DEM file (e.g., dc_dem.tif).")
    parser.add_argument("--buffer", type=float, default=1000, help="Buffer distance in meters to apply to the quadrant polygons.")
    parser.add_argument("--separation", type=float, default=100, help="Minimum separation distance in meters between identified peaks.")
    parser.add_argument("--output-kml", default="Highpoints_per_Hidrant", help="Base name for the output KML file.")
    parser.add_argument("--csv", action='store_true', help="Output a CSV data file of the peaks.")
    args = parser.parse_args()

    # Define the four quadrant triads.
    triads = [
        #["N", "W", "C"],  # NW quadrant
        #["W", "S", "C"],  # SW quadrant
        #["S", "E", "C"],  # SE quadrant
        #["E", "N", "C"]   # NE quadrant
        ["N", "W", "S"],  # W hidrant
        ["W", "S", "E"],  # S hidrant
        ["S", "E", "N"],  # E hidrant
        ["E", "N", "W"]   # N hidrant
    ]
    # Mapping from the triad key to a two-letter quadrant name.
    triad_to_quad = {
        #"NWC": "NW",
        #"WSC": "SW",
        #"SEC": "SE",
        #"ENC": "NE",
        "NWS": "We",
        "WSE": "So",
        "SEN": "Ea",
        "ENW": "No"
    }
    # Marker colors for each quadrant.
    marker_colors = {
        "We": "#FF0000",  # Red for NW
        "So": "#0000FF",  # Blue for SW
        "Ea": "#00FF00",  # Green for SE
        "No": "#800080"   # Purple for No
    }
    all_features = []
    for triad in triads:
        triad_key = ''.join(triad)
        quad_name = triad_to_quad.get(triad_key, triad_key)
        print(f"Processing quadrant: {quad_name}")
        peaks = get_top_ten_peaks_quadrant(args.dem_file, triad, buffer_m=args.buffer, separation_m=args.separation)
        if peaks:
            for peak in peaks:
                point_geom = Point(peak["lon"], peak["lat"])
                # Create a name like "NW1", "SE8", etc.
                point_name = f"{quad_name}{peak['rank']}"
                # Manually create a 'description' field. The KML driver uses this for the
                # popup, which is a reliable way to fix the malformed KML output.
                description = (f"Quadrant: {quad_name}\n"
                               f"Rank: {peak['rank']}\n"
                               f"Elevation (m): {peak['alt']:.2f}\n"
                               f"Elevation (ft): {peak['alt'] * 3.28084:.2f}")
                feature = {
                    "name": point_name, # Used for the KML <name> tag
                    "description": description, # Used for the KML <description> tag
                    "quadrant": quad_name,
                    "rank": peak["rank"],
                    "elevation": peak["alt"],
                    "marker-color": marker_colors.get(quad_name, "#000000")
                }
                all_features.append({"geometry": point_geom, "properties": feature})
        else:
            print(f"No peaks found for quadrant {quad_name}.")

    if not all_features:
        print("No features were collected. Exiting without creating GeoJSON.")
        return

    gdf = gpd.GeoDataFrame(
        [feat["properties"] for feat in all_features],
        geometry=[feat["geometry"] for feat in all_features],
        crs="EPSG:4326"
    )
    # For KML, the 'name' and 'description' columns are used for the placemark tags.
    # This gives us reliable and well-formatted output in Google Earth.
    # This also fixes a bug where the script was using 'args.output_base' which is not defined.
    out_kml = f"{args.output_kml}.kml"
    gdf.to_file(out_kml, driver='KML')
    print(f"KML with hidrant peaks saved to: {out_kml}")

    # --- CSV Output Generation ---
    # Create a new DataFrame tailored for the CSV output to avoid modifying the original gdf.
    if args.csv:
        csv_df = gdf.copy()

        # Extract LAT and LON from the geometry column.
        csv_df['Lat'] = csv_df.geometry.y
        csv_df['Lon'] = csv_df.geometry.x

        # Convert elevation from meters to feet for the 'ALT (feet)' column.
        csv_df['Alt'] = csv_df['elevation'] * 3.28084

        # Select, rename, and reorder the columns as requested.
        csv_df = csv_df.rename(columns={'name': 'Name', 'quadrant': 'Sector', 'rank': 'Rank'})
        # Corrected column names to match the DataFrame for accurate CSV export.
        output_columns = ['Name', 'Lat', 'Lon', 'Alt', 'Sector', 'Rank']
        out_csv = "highpoints_out.csv"
        csv_df[output_columns].to_csv(out_csv, index=False, float_format='%.6f')
        print(f"CSV with hidrant peaks saved to: {out_csv}")
    
if __name__ == "__main__":
    main()
