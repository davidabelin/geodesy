# C:\Users\David\Documents\Local_Python\qgis\octant_heights.py
import rasterio
import numpy as np
from rasterio.mask import mask
import geopandas as gpd
from shapely.geometry import Polygon, Point
from pyproj import Transformer
from rasterio.warp import calculate_default_transform, reproject, Resampling
#from triangle_data import locs  # Assumes locs maps keys "N", "S", "E", "W", "C" to (lat, lon) tuples

locs = {
    "N": (38.995957,-77.040986),
    "S": (38.790339,-77.040586),
    "E": (38.89286,-76.909166),
    "W": (38.893245,-77.1723),
    "SW5": (38.842081,-77.10674),
    "SE5": (38.841683,-76.974839),
    "NE5": (38.944414,-76.975009),
    "NW5": (38.944639,-77.106682),
    "C": (38.8931523,-77.040789)
}

def get_top_ten_peaks_octant(dem_path, triad, buffer_m=100, separation_m=100):
    """
    Finds the top ten peaks within a octant defined by a triad of vertices.
    The triangle (defined by triad vertices in order) is buffered by buffer_m meters,
    then the DEM is masked to that area. A circular exclusion (of radius separation_m)
    is used to ensure peaks are not too close together.
    
    All distance calculations (buffering and separation) are done in meters.
    
    Args:
        dem_path (str): Path to the DEM file.
        triad (list of str): Three keys (e.g. ["N", "W", "C"]) defining the octant.
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
                proj_crs = "EPSG:26985" # for NAD83 which dem file is in; use "EPSG:32618" for projection with WGS84 
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
            for rank in range(1, 11):
                if np.isnan(dem_iter).all():
                    break
                flat_index = np.nanargmax(dem_iter)
                row, col = np.unravel_index(flat_index, dem_iter.shape)
                peak_alt = dem_iter[row, col]
                # Convert array indices to coordinates in the working CRS.
                x_work, y_work = rasterio.transform.xy(working_transform, row, col)
                # Transform the coordinates to EPSG:4326.
                transformer = Transformer.from_crs(working_crs, "EPSG:4326", always_xy=True)
                lon_wgs, lat_wgs = transformer.transform(x_work, y_work)
                peaks.append({"rank": rank, "lat": lat_wgs, "lon": lon_wgs, "alt": float(peak_alt)})

                # Apply the circular exclusion mask.
                row_min = max(0, row - separation_pixels)
                row_max = min(dem_iter.shape[0], row + separation_pixels + 1)
                col_min = max(0, col - separation_pixels)
                col_max = min(dem_iter.shape[1], col + separation_pixels + 1)
                mask_row_start = row_min - (row - separation_pixels)
                mask_row_end = mask_row_start + (row_max - row_min)
                mask_col_start = col_min - (col - separation_pixels)
                mask_col_end = mask_col_start + (col_max - col_min)
                dem_iter[row_min:row_max, col_min:col_max][
                    circle_mask[mask_row_start:mask_row_end, mask_col_start:mask_col_end]
                ] = np.nan

            return peaks

    except Exception as e:
        print(f"Error processing octant {triad}: {e}")
        return None

def main():
    buffer_m=1000
    separation_m=10
    dem_file = "qgis/dc_dem.tif"
    # Define the four octant triads.
    triads = [
        ["N", "NW5", "C"],  # NW octant
        ["NW5", "W", "C"],  # NW5 octant
        ["W", "SW5", "C"],  # SW5 octant
        ["SW5", "S", "C"],  # SW octant
        ["S", "SE5", "C"],  # SE5 octant 
        ["SE5", "E", "C"],  # SE octant
        ["E", "NE5", "C"],  # SE octant
        ["NE5", "N", "C"],  # NE5 octant
    ]
    # Mapping from the triad key to a two-letter octant name.
    triad_to_quad = {
        "NWC": "NW",
        "WSC": "SW",
        "SEC": "SE",
        "ENC": "NE"
    }
    # Marker colors for each octant.
    marker_colors = {
        "NW": "#FF0000",  # Red for NW
        "NW5": "#FF5050",  # Red for NW
        "SW": "#0000FF",  # Blue for SW
        "SW5": "#5050FF",  # Blue for SW
        "SE": "#00FF00",  # Green for SE
        "SE5": "#50FF50",  # Green for SE
        "NE": "#800080",   # Purple for NE
        "NE5": "#804080"   # Purple for NE
    }
    all_features = []
    for triad in triads:
        triad_key = ''.join(triad)
        quad_name = triad_to_quad.get(triad_key, triad_key)
        print(f"Processing octant: {quad_name}")
        peaks = get_top_ten_peaks_octant(dem_file, triad, buffer_m=buffer_m, separation_m=separation_m)
        if peaks:
            for peak in peaks:
                point_geom = Point(peak["lon"], peak["lat"])
                # Create a name like "NW1", "SE8", etc.
                point_name = f"{quad_name}{peak['rank']}"
                feature = {
                    "name": point_name,
                    "octant": quad_name,
                    "rank": peak["rank"],
                    "elevation": peak["alt"],
                    "marker-color": marker_colors.get(quad_name, "#000000")
                }
                all_features.append({"geometry": point_geom, "properties": feature})
        else:
            print(f"No peaks found for octant {quad_name}.")

    if not all_features:
        print("No features were collected. Exiting without creating GeoJSON.")
        return

    gdf = gpd.GeoDataFrame(
        [feat["properties"] for feat in all_features],
        geometry=[feat["geometry"] for feat in all_features],
        #crs="EPSG:4326" #generic WGS84
        crs="EPSG:32618" #WGS84 UTM 18N
    )
    out_geojson = "data/octant_highpnts.geojson"
    gdf.to_file(out_geojson, driver='GeoJSON')
    print(f"GeoJSON with octant peaks saved to: {out_geojson}")

if __name__ == "__main__":
    main()
