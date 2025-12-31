# C:\Users\David\Documents\Local_Data\WashDC\streets.py v2.4
# DATAFILES: data\Roadway_SubBlock.geojson

import geopandas as gpd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors
from shapely.ops import linemerge, unary_union
from shapely.geometry import LineString, MultiLineString, GeometryCollection

global mask_NS_EW
mask_NS_EW = False  # Change to True to eliminate huge NS/EW contributions

def force_2d(geom):
    """
    Converts a geometry to 2D by stripping out any z-coordinate.
    Works for LineString, MultiLineString, and GeometryCollection.
    """
    if geom.is_empty:
        return geom
    if geom.geom_type == 'LineString':
        # Reconstruct LineString with only x, y coordinates.
        return LineString([(x, y) for (x, y, *_) in geom.coords])
    elif geom.geom_type in ['MultiLineString', 'GeometryCollection']:
        # Recursively convert each geometry in the collection.
        return type(geom)([force_2d(part) for part in geom.geoms])
    # For other geometry types, return as-is (or handle accordingly).
    return geom

def merge_segments(geoms):
    """
    Called from: combine_two_segments()
    Attempt to merge a list of LineString geometries.
    First, convert all geometries to 2D.
    Using unary_union first ensures overlapping or touching segments are united.
    If the union result is a MultiLineString, attempt to merge it;
    if it is already a LineString, simply return it.
    """
    # Convert all geometries to 2D:
    geoms_2d = [force_2d(g) for g in geoms]
    unioned = unary_union(geoms_2d)

    if unioned.geom_type == 'LineString':
        # Already a single LineString; nothing to merge.
        return unioned
    elif unioned.geom_type == 'MultiLineString':
        try:
            merged = linemerge(unioned)
            return merged
        except Exception as e:
            # In case linemerge still fails, fallback to the unioned result.
            print(f"linemerge failed: {e}")
            return unioned
    else:
        # For other geometry types, return as-is.
        return unioned

def ensure_linestring(geom):
    """
    Called from: combine_two_segments()
    Ensure that the geometry is a simple LineString.
    If it is a MultiLineString, return its first component.
    """
    geom_2d = force_2d(geom)
    if geom_2d.geom_type == "LineString":
        return geom_2d
    elif geom_2d.geom_type == "MultiLineString":
        # Pick the first component
        return list(geom_2d.geoms)[0]
    else:
        # Fallback: return as is.
        return geom_2d

def combine_two_segments(seg1, seg2):
    """
    Called from: combine_short_segments()
    Combine two line segments if they are connected (i.e., the end of seg1 is close to the start of seg2).
    This function ensures that both segments are simple LineStrings before attempting to access .coords.
    If they are not directly connected, fall back to merging using merge_segments().
    """
    seg1 = ensure_linestring(seg1)
    seg2 = ensure_linestring(seg2)

    # Check if the end of seg1 is nearly equal to the start of seg2.
    if np.allclose(seg1.coords[-1], seg2.coords[0], atol=1e-6):
        # They are connected, so create a new coordinate sequence by concatenation.
        new_coords = list(seg1.coords) + list(seg2.coords)[1:]
        return LineString(new_coords)
    else:
        # Fall back to union/linemerge if they're not contiguous.
        return merge_segments([seg1, seg2])

def combine_short_segments(segments, min_length):
    """
    Calling code:   sorted_geoms = list(group.geometry)
                    merged_segments = combine_short_segments(sorted_geoms, min_length)
    Given a list of segments (assumed sorted in order along the road), combine consecutive segments
    if their total length is less than min_length.
    This returns a new list of merged segments.
    """
    combined = []
    buffer = None

    for geom in segments:
        if buffer is None:
            buffer = geom
        else:
            # If the current buffer’s length is below the minimum, merge the next segment.
            if buffer.length < min_length:
                buffer = combine_two_segments(buffer, geom)
            else:
                combined.append(buffer)
                buffer = geom
    # Append the last buffered segment.
    if buffer is not None:
        combined.append(buffer)
    return combined

def calculate_bearing(p1, p2):
    """
    Calculate the bearing between two points p1 and p2.
    p1 and p2 should be tuples in (lon, lat) degrees.
    Returns the bearing in degrees from North.
    """
    # Convert lat/lon to radians
    lat1, lon1 = np.radians(p1[1]), np.radians(p1[0])
    lat2, lon2 = np.radians(p2[1]), np.radians(p2[0])
    dLon = lon2 - lon1

    x = np.sin(dLon) * np.cos(lat2)
    y = np.cos(lat1) * np.sin(lat2) - np.sin(lat1) * np.cos(lat2) * np.cos(dLon)

    initial_bearing = np.arctan2(x, y)
    bearing = (np.degrees(initial_bearing) + 360) % 360
    return bearing

def get_endpoints(geom):
    """
    Extract the start and end points from a geometry.
    For MultiLineString, take the first coordinate from the first part
    and the last coordinate from the last part.
    """
    if geom.geom_type == 'MultiLineString':
        parts = list(geom.geoms)  # Use .geoms to iterate over the components
        start = parts[0].coords[0]
        end = parts[-1].coords[-1]
    else:  # LineString
        start = geom.coords[0]
        end = geom.coords[-1]
    return start, end

def plot_polar_chart(total_lengths, bin_edges, clip_sum):
    """
    Generates and displays a polar bar chart visualizing the distribution
    of total street segment lengths across different orientation bins.

    Color coding:
      - Bins with centers that are multiples of 45° are colored bright blue (dodgerblue).
      - Bins with centers that are multiples of 18° (except 180°) are colored red.
      - Bins with centers that are multiples of 15° are colored cyan.
      - All other bins are blue.
    """
    # Calculate bin centers in degrees for color assignment
    bin_centers_deg = (bin_edges[:-1] + bin_edges[1:]) / 2
    # Also convert to radians for positioning on the polar plot
    theta = np.radians(bin_centers_deg)

    # Determine bar width in radians
    num_bins = len(total_lengths)
    width = (2 * np.pi) / num_bins

    # Build list of colors per bin based on the rules.
    colors = []
    for center in bin_centers_deg:
        # Round to the nearest integer for simplicity.
        a = int(round(center))
        if a % 45 == 0:
            colors.append("dodgerblue")  # bright blue
        elif a % 18 == 0 and a != 180:
            colors.append("red")
        elif a % 15 == 0:
            colors.append("cyan")
        else:
            colors.append("blue")

    # Create the polar plot figure and axes
    fig, ax = plt.subplots(figsize=(8, 8), subplot_kw={'projection': 'polar'})
    # Create the bars with the assigned colors
    bars = ax.bar(theta, total_lengths, width=width, bottom=0, color=colors, alpha=0.7)

    # Configure the polar plot appearance:
    # Set 0 degrees at North and increase clockwise
    ax.set_theta_direction(-1)
    ax.set_theta_offset(np.pi / 2)

    # Construct the title (appending NS/EW masked if that flag is set)
    title_msg = "Total Street Segment Length by Orientation"
    if 'mask_NS_EW' in globals() and mask_NS_EW:
        title_msg += " (NS/EW Masked)"
    ax.set_title(title_msg, va='bottom')

    # Set radial ticks, labels, and limits based on clip_sum
    ax.set_rticks([0, clip_sum/2, clip_sum])
    ax.set_rlabel_position(180)
    ax.set_rlim(0, clip_sum)

    # Set angular ticks and labels
    ax.set_xticks(np.linspace(0, 2 * np.pi, 16, endpoint=False))
    ax.set_xticklabels(['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
                        'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'])

    plt.show()

def plot_route_bars(roads_gdf, sort_by_length=True, max_routes_to_plot=50):
    """
    Generates and displays two bar charts for aggregated road data:
    1. Route Name vs. Total Length
    2. Route Name vs. Overall Bearing

    Args:
        roads_gdf (gpd.GeoDataFrame): GeoDataFrame containing aggregated road data.
                                      Expected columns: 'route', 'length', 'bearing'.
        sort_by_length (bool): If True, sorts the routes by length (descending)
                               before plotting. Otherwise, uses the order in the GeoDataFrame.
        max_routes_to_plot (int): Maximum number of routes to display on the x-axis
                                  to prevent overcrowding. If exceeded, only the top
                                  routes (by length if sorted, or first N otherwise) are shown.
    """
    # --- Data Preparation ---
    plot_gdf = roads_gdf.copy()

    if sort_by_length:
        plot_gdf = plot_gdf.sort_values('length', ascending=False)
    else:
        plot_gdf = plot_gdf.sort_values('bearing', ascending=False)

    num_routes = len(plot_gdf)
    if num_routes > max_routes_to_plot:
        print(f"Warning: Too many routes ({num_routes}) to display clearly.")
        print(f"Plotting only the top {max_routes_to_plot} routes.")
        plot_gdf = plot_gdf.head(max_routes_to_plot)
        num_routes = max_routes_to_plot # Update count for plot setup

    route_names = plot_gdf['route']
    lengths = plot_gdf['length']
    bearings = plot_gdf['bearing']

    # --- Plotting ---
    # Adjust figsize height based on number of routes for better label spacing
    fig_height = max(6, num_routes * 0.2) # Heuristic for height
    fig, axes = plt.subplots(2, 1, figsize=(12, fig_height), sharex=True) # Share x-axis

    # --- Plot 1: Length ---
    ax1 = axes[0]
    ax1.bar(route_names, lengths, color='skyblue')
    ax1.set_ylabel('Total Length (m)')
    ax1.set_title('Total Length per Route')
    ax1.grid(axis='y', linestyle='--', alpha=0.7)
    # Format y-axis for potentially large numbers if needed
    ax1.ticklabel_format(style='sci', axis='y', scilimits=(0,0)) # Use scientific notation if large

    # --- Plot 2: Bearing ---
    ax2 = axes[1]
    ax2.bar(route_names, bearings, color='lightcoral')
    ax2.set_ylabel('Overall Bearing (degrees)')
    ax2.set_title('Overall Bearing per Route')
    ax2.set_ylim(0, 360) # Bearing is 0-360
    ax2.set_yticks(np.arange(0, 361, 45)) # Ticks every 45 degrees
    ax2.grid(axis='y', linestyle='--', alpha=0.7)

    # --- Common X-axis Formatting ---
    # Rotate labels for readability, adjust font size if needed
    plt.xticks(rotation=90, ha='center', fontsize=8) # Rotate labels vertically
    plt.xlabel("Route Name")

    # Adjust layout to prevent labels overlapping titles/axes
    plt.tight_layout(rect=[0, 0.03, 1, 0.98]) # Add slight bottom margin for rotated labels

    plt.show()

def main():
    """
    Main function to demonstrate.
    """
    # -----------------------------
    # Load Data
    # -----------------------------
    # Load the GeoJSON file from the Roadway SubBlock dataset
    gdf = gpd.read_file('data/Roadway_SubBlock.geojson')
    # Optionally, load smaller datafile with *only* downtown Washington, DC:
    #gdf = gpd.read_file('data/dwntwn_streets.geojson')

    # -------
    # Optional: limit to only wanted data
    # -------
    # Filter gdf for *only* downtown Washington, DC
    # Default: set to approx. Ellicott/L'Enfant map
    # Format: (min_lon, min_lat, max_lon, max_lat)
    #downtown_bbox = (-77.058, 38.86, -76.973, 38.935)
    #gdf = gdf.cx[downtown_bbox[0]:downtown_bbox[2], downtown_bbox[1]:downtown_bbox[3]]
    
    # Define keywords to filter out
    keywords_to_remove = ['Alley', 'Driveway', 'Ramp', ' Trail ', 'Trail', 'Walkway']
    # Keep rows where ROUTENAME does NOT contain any of the keywords (case-insensitive)
    # na=False ensures rows with NaN ROUTENAME are kept (change if you want to drop them)
    mask_remove = ~gdf['ROUTENAME'].str.contains('|'.join(keywords_to_remove), case=False, na=False)
    # Apply the mask to filter the GeoDataFrame
    gdf = gdf[mask_remove].copy() # Use .copy() to avoid potential SettingWithCopyWarning later
    
    # Create a boolean mask for rows to KEEP:
    keywords_to_keep = ['AVENUE', ' AVE', 'BLVD']#[' CIR ', ' CIRCLE', ' SQR ', ' SQUARE']
    mask = gdf['ROUTENAME'].str.contains('|'.join(keywords_to_keep), case=False, na=False)
    gdf = gdf[mask].copy()

    # -----------------------------
    # Computations
    # -----------------------------
    # Compute endpoints and bearings from the original (WGS84) geometries.
    gdf['endpoints'] = gdf.geometry.apply(get_endpoints)
    gdf['bearing'] = gdf['endpoints'].apply(lambda pts: calculate_bearing(pts[0], pts[1]))

    # Reproject to an appropriate CRS for length calculations (UTM zone 18N for DC)
    gdf_proj = gdf.to_crs(epsg=32618)
    # Get the length (in meters) of each street segment
    gdf['length_m'] = gdf_proj.geometry.length

    # -----------------------------
    # Get a sorted list of unique road names.
    # -----------------------------
    # Ensure ROUTENAME is not NaN before getting unique values, just in case
    unique_routes = sorted(gdf['ROUTENAME'].dropna().unique())
    n_routes = len(unique_routes)

    # Choose a colormap. If you have more than 20 roads, consider using 'viridis' or another scalable palette.
    cmap = plt.get_cmap('tab20', n_routes)  # or veridis or use plt.get_cmap('tab20', n_routes)

    # Build a dictionary that maps each route name to a color.
    # Here, the colors are in RGBA format.
    color_dict = {route: cmap(i) for i, route in enumerate(unique_routes)}

    # If you need hex colors instead (for example if you want to store them in GeoJSON), convert:
    #color_dict_hex = {route: matplotlib.colors.rgb2hex(cmap(i)) for i, route in enumerate(unique_routes)}

    # Group by ROUTENAME. It is assumed that the 'ROUTENAME' field identifies a road.
    road_groups = gdf.groupby('ROUTENAME')

    # Prepare a list to hold aggregated road data.
    road_data = []
    min_length = 20  # Example min segment length: 20 meters

    for route_name, group in road_groups:
        # Sort segments if you have a measure column (e.g., FROMMEASURE or TOMEASURE)
        # This helps to correctly determine start and end points.
    # TO DO FIX
        if 'FROMMEASURE' in group.columns:
            group = group.sort_values('FROMMEASURE')

        # Combine short segments along the road.
        sorted_geoms = list(group.geometry)
        #merged_segments = combine_short_segments(sorted_geoms, min_length)
        # Merge the (potentially recombined) segments into a single geometry.
        #merged_geom = merge_segments(merged_segments)
        merged_geom = merge_segments(sorted_geoms)

        #
        # TO DO: Divide groups into subgroups if there are distinct bearing subgroups within the road.
        #       Road groups will be divided into and replaced by its bearing-subgroups as new groups with names like:
        #       "{route_name}_{bearing_A}", "{route_name}_{bearing_B}", etc.

        # Get the overall start and end points from the merged geometry.
        start, end = get_endpoints(merged_geom)

        # Calculate overall bearing using start and end.
        overall_bearing = calculate_bearing(start, end)

        # Sum the lengths from all segments (or recalc using merged_geom.length if projected properly)
        total_length = group['length_m'].sum()

        # Optionally, save additional information such as color coding or any other properties.
        road_data.append({
            'route': route_name,
            'geometry': merged_geom,
            'start': start,
            'end': end,
            'bearing': overall_bearing,
            'length': total_length,
            'color': color_dict.get(route_name) # Use .get() in case route_name was NaN and not in color_dict
        })

    # -----------------------------
    # Convert the segment-combined road_data list to a GeoDataFrame (to save)
    # -----------------------------
    roads_gdf = gpd.GeoDataFrame(road_data, crs=gdf.crs)

    if False: #True:
        # -----------------------------
        # Bin Bearings and Sum Total Lengths
        # -----------------------------
        num_bins = 720  # One-half degree per bin
        bins = np.linspace(0, 360, num_bins + 1)

        # Sum the lengths in each bearing bin using np.histogram with weights.
        # note: now using roads_gdf instead of gdf
        total_lengths, bin_edges = np.histogram(roads_gdf['bearing'], bins=bins, weights=roads_gdf['length'])
        #total_lengths, bin_edges = np.histogram(gdf['bearing'], bins=bins, weights=gdf['length_m'])

        # Cap the bin-sums at 10000
        clip_sum = 10000

        # Option to mask NS/EW bins:
        if mask_NS_EW:
            # Compute bin centers for clarity
            bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
            tol = 0.2  # degrees tolerance
            mask = ~(   # Factor of *2 for num_bins 720
                (np.abs(bin_centers - 0*2) < tol) |
                (np.abs(bin_centers - 360*2) < tol) |  # 360° same as 0°
                (np.abs(bin_centers - 90*2) < tol) |
                (np.abs(bin_centers - 180*2) < tol) |
                (np.abs(bin_centers - 270*2) < tol)
            )
            total_lengths = np.where(mask, total_lengths, 0)

        total_lengths = np.clip(total_lengths, a_min=0, a_max=clip_sum)

    # -----------------------------
    # Save data files
    # -----------------------------

    # Save the roads as a new GeoJSON (this file will have all the aggregated properties included)
    roads_gdf.to_file('data/dc_circles.geojson', driver='GeoJSON')
    # Select subsets of the data by column (to save)
    #subcols = ['ROUTEID', 'ROUTENAME', 'geometry', 'endpoints', 'bearing', 'length_m']
    #gdf[subcols].to_file('data/dwntwn_streets_subset.geojson', driver='GeoJSON')

    # -----------------------------
    # Create plots and charts
    # -----------------------------
    plot_route_bars(roads_gdf, sort_by_length=False, max_routes_to_plot=150)

    # Polar Chart of Total Lengths
    # -----------------------------
    #plot_polar_chart(total_lengths, bin_edges, clip_sum)


if __name__ == "__main__":
    main()