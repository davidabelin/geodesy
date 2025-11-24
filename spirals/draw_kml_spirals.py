# v1.0
import csv
import math
import re
import os
from collections import defaultdict
import argparse

try:
    import rasterio
    from rasterio.mask import mask as rio_mask
    import geopandas as gpd
    import simplekml
    from shapely.geometry import shape
except ImportError as ie:
    print("Input error: ", ie)
    rasterio = None
    gpd = None
    simplekml = None

def generate_kml_from_csv(csv_filepath, kml_filepath, include_points=False):
    """
    Reads a CSV file with point data (LOC, LAT, LON) and generates a KML file
    with lines connecting sequences of points.

    Points are grouped by the non-numeric prefix of their LOC (Vertex ID) value.
    Within each group, points are connected in ascending order of their numeric suffix.
    Individual points can be optionally included in the KML.
    """
    # Args:
    #   csv_filepath (str): Path to the input CSV file.
    #   kml_filepath (str): Path to the output KML file.
    #   include_points (bool): If True, individual points will be added to the KML.
    # Dictionary to store points, grouped by their prefix
    # e.g., {'ANS': [{'lon': lon, 'lat': lat, 'num': num_suffix, 'id': vertex_id}, ...], ...}
    grouped_points_data = defaultdict(list)

    # Define a palette of colors (AABBGGRR format for KML)
    # AA = Alpha (26 for 85% transparent, FF for opaque)
    # Using 85% transparency for lines (Alpha = 15% opaque = 0.15 * 255 = 38 = 0x26)
    # For lines 100% opaque: Alpha = 1.0 * 255 = 255 (decimal) = 0xFF (hex).
    # For point icons: Fully opaque. Alpha = 1.0 * 255 = 255 (decimal) = 0xFF (hex).
    color_palette_base = [
        "0000FF", "00FF00", "FF0000", "00FFFF", "FF00FF", "AAAA00", # Blue, Green, Red, Cyan, Magenta, DarkYellow
        "00A5FF", "500050", "2A2AA5", "32CD32", "A9A0DD", "505000", # Orange, DarkPurple, Brown, Lime, DarkPink, DarkTeal
        "005050"  # DarkOlive
    ]
    # This line sets the alpha value for all line and icon colors:
    # 'FF' (100% opacity) for icons, 'AA' (67% opacity) for lines   :
    #line_color_palette = [f"FF{color_hex}" for color_hex in color_palette_base]
    line_color_palette = [f"AA{color_hex}" for color_hex in color_palette_base]
    icon_color_palette = [f"AA{color_hex}" for color_hex in color_palette_base]


    group_to_color_index = {}
    next_color_idx = 0
    # e.g., {'ANS': [{'lon': lon, 'lat': lat, 'num': num_suffix, 'id': vertex_id}, ...], ...}
    grouped_points_data = defaultdict(list)

    try:
        with open(csv_filepath, mode='r', newline='', encoding='utf-8') as infile:
            reader = csv.DictReader(infile)
            # Check for required headers
            if not reader.fieldnames or not all(f in reader.fieldnames for f in ['LOC', 'LAT', 'LON']):
                print(f"Error: CSV file {csv_filepath} must have 'LOC', 'LAT', 'LON' columns.")
                print(f"Found headers: {reader.fieldnames}")
                return

            for i, row in enumerate(reader):
                # Using .get() for safer access in case of malformed rows
                vertex_id = row.get('LOC')
                lat_str = row.get('LAT')
                lon_str = row.get('LON')

                if not all([vertex_id, lat_str, lon_str]):
                    print(f"Warning: Skipping row {i+2} in CSV due to missing Loc, Lat, or Lon data: {row}")
                    continue
                
                try:
                    # Ensure Lat and Lon are stripped of potential whitespace before conversion
                    lat = float(lat_str.strip())
                    lon = float(lon_str.strip())
                except ValueError:
                    print(f"Warning: Skipping row {i+2} (ID: {vertex_id}) due to invalid lat/lon: Lat='{lat_str}', Lon='{lon_str}'")
                    continue

                # Try to parse vertex_id into prefix and numeric suffix using a hierarchy of rules:
                # 1. "ANYTHING-NUMBER" -> prefix=ANYTHING, suffix=NUMBER
                # 2. "ANYTHINGNUMBER" (ends with digits) -> prefix=ANYTHING, suffix=NUMBER (ANYTHING can be empty)
                # 3. "ANYTHINGELSE" (no dash, no trailing digits) -> prefix=ANYTHINGELSE, suffix=sequential (0, 1, ...)
                
                prefix = None
                num_suffix = None

                # Rule 1: Try dash-based parsing (e.g., "GROUP-1", "TEST-SUBGROUP-007")
                dash_match = re.match(r'(.*)-(\d+)$', vertex_id)
                if dash_match:
                    prefix = dash_match.group(1)
                    num_suffix = int(dash_match.group(2))
                else:
                    # Rule 2: Try trailing digits parsing (e.g., "ANS0", "cSAN10", "123")
                    trailing_digits_match = re.match(r'(.*?)(\d+)$', vertex_id)
                    if trailing_digits_match:
                        prefix = trailing_digits_match.group(1) # Can be empty if vertex_id is all digits
                        num_suffix = int(trailing_digits_match.group(2))
                    elif vertex_id: # Rule 3: Fallback for non-empty vertex_id (e.g., "SiteA", "NorthMarker")
                        prefix = vertex_id
                        # Assign sequential suffix (0, 1, 2,...) for this prefix
                        num_suffix = len(grouped_points_data[prefix])
                    # else: vertex_id is empty or unparseable by any rule, will be caught below

                if prefix is not None and num_suffix is not None:
                    grouped_points_data[prefix].append({'lon': lon, 'lat': lat, 'num': num_suffix, 'id': vertex_id})
                else:
                    print(f"Warning: Could not parse vertex ID '{vertex_id}' into a known prefix/suffix structure. Skipping.")

    except FileNotFoundError:
        print(f"Error: The file {csv_filepath} was not found.")
        return
    except Exception as e:
        print(f"An unexpected error occurred while reading {csv_filepath}: {e}")
        return

    # Create a KML object
    kml = simplekml.Kml()
    kml.document.name = f"Lines for points in {os.path.basename(csv_filepath)}"

    # For each group, create a Folder, Point Placemarks, and a LineString
    for prefix, points_list in grouped_points_data.items():
        if not points_list:
            continue

        # Assign a color to this group if not already assigned
        if prefix not in group_to_color_index:
            group_to_color_index[prefix] = next_color_idx
            next_color_idx = (next_color_idx + 1) % len(line_color_palette)
        
        current_color_idx = group_to_color_index[prefix]
        current_line_color = line_color_palette[current_color_idx]
        current_icon_color = icon_color_palette[current_color_idx]

        # Sort points by their numeric suffix to ensure correct line order
        sorted_points = sorted(points_list, key=lambda p: p['num'])

        # Create a folder for this group
        group_folder = kml.newfolder(name=f"Group {prefix}")
        # Optionally, create Point Placemarks for each point in the group
        if include_points:
            points_subfolder = group_folder.newfolder(name=f"{prefix} Points")
            for p_data in sorted_points:
                kml_point = points_subfolder.newpoint(name=p_data['id'])
                kml_point.coords = [(p_data['lon'], p_data['lat'])]
                kml_point.altitudemode = simplekml.AltitudeMode.clamptoground
                
                # Add ExtendedData
                kml_point.extendeddata.newdata('Loc', value=p_data['id'])
                kml_point.extendeddata.newdata('Lat', value=str(p_data['lat']))
                kml_point.extendeddata.newdata('Lon', value=str(p_data['lon']))
                kml_point.extendeddata.newdata('Group', value=prefix)
                
                # Style the point icon
                kml_point.style.iconstyle.color = current_icon_color
                kml_point.style.iconstyle.icon.href = 'http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png'

        # Prepare coordinates for the LineString (lon, lat, alt)
        # Altitude is set to 0 (ground level) by default in simplekml if not specified for LineString
        coordinates = [(p['lon'], p['lat']) for p in sorted_points] # simplekml adds altitude=0 by default

        if len(coordinates) >= 2:  # A LineString needs at least two points
            linestring = group_folder.newlinestring(name=f"{prefix} Sequence")
            linestring.coords = coordinates
            linestring.altitudemode = simplekml.AltitudeMode.clamptoground
            linestring.extrude = 0 # Do not extrude to ground if points have altitude (not the case here but good practice)
            
            # Style the line
            linestring.style.linestyle.color = current_line_color # Hopefully 85% *opaque* color
            linestring.style.linestyle.width = 1.0
        # No special handling for len(coordinates) == 1 for lines, as points are created individually above.

    # Save the KML file
    try:
        kml.save(kml_filepath)
        print(f"KML file successfully generated: {kml_filepath}")
    except NameError as ne:
        if 'simplekml' in str(ne):
            print("Error: The 'simplekml' library is not installed or imported correctly.")
            print("Please install it using: pip install simplekml")
        else:
            print(f"An unexpected NameError occurred: {ne}")
    except Exception as e:
        print(f"An error occurred while saving the KML file {kml_filepath}: {e}")

if __name__ == "__main__":
    # Default path to your input CSV file
    input_csv_file = r"c:\Users\David\Documents\Local_Python\geodesy\spirals\penspiA_pnts.csv"
    
    # Default output KML file path (saved in the same directory as the CSV)
    output_kml_file = os.path.join(os.path.dirname(input_csv_file), "pen_spirals_A.kml")

    p = argparse.ArgumentParser(description='Geodetic Connect-the-Dots Utility')
    sub = p.add_subparsers(dest='cmd')
    
    k_help = 'Connect the (lat,lon) Dots Imported from a CSV file'
    k = sub.add_parser('kml', help=k_help)
    k.add_argument('csv_path', type=str, default='input_csv_file', help='Input CSV file path')
    k.add_argument('kml_file', type=str, default='output_kml_file', help='Output KML file path')
    k.add_argument('--line_alpha',type=hex, required=False, default=0x99, help='Line opacity; FF = 100% opaque')
    k.add_argument('--include_points',action='store_true', help='True if set, False if not (default); save (lat,lon) points with their connecting lines')
    
    args = p.parse_args()
    if args.cmd == 'kml':
        print(f"Input: {args.csv_path}\tOutput: {args.kml_file}\tInclude Points: {args.include_points}")
        generate_kml_from_csv(args.csv_path, args.kml_file, args.include_points)
    elif args.cmd is None:
        print(f"Trying defaults...\tInput: {input_csv_file}\tOutput: {output_kml_file}\tInclude Points: False")
        generate_kml_from_csv(input_csv_file, output_kml_file) # Default to False for include_points
    else:
        p.print_help()