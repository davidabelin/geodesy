# C:\Users\David\Documents\Local_Data\WashDC\geojson_to_kml.py  v1.1 (Fixed)
import json
import simplekml
import re # Import regular expressions for parsing the color string

# --- Configuration ---
geojson_file_path = 'data/dc_avenues.geojson'
kml_file_path = 'data/dcmap_styled.kml' # Choose your output KML path

# --- Helper Function: Convert RGBA String to KML Color ---
def rgba_string_to_kml_color(rgba_str, default_color='ff0000ff'): # Default to opaque red if error
    """
    Converts an RGBA string like "(0.267, 0.004, 0.329, 1.0)"
    or a tuple like (0.267, 0.004, 0.329, 1.0)
    to KML's aabbggrr hex format like "ff540144".
    Handles both string and tuple/list inputs.
    """
    if not rgba_str:
        print(f"Warning: Missing color data, using default {default_color}")
        return default_color

    try:
        if isinstance(rgba_str, (list, tuple)):
            # Handle list/tuple directly
            if len(rgba_str) != 4:
                 raise ValueError("Color tuple/list does not contain 4 numbers")
            r, g, b, a = [float(val) for val in rgba_str]
        elif isinstance(rgba_str, str):
            # Use regex to find floating point numbers within the string
            matches = re.findall(r"[-+]?\d*\.\d+|\d+", rgba_str)
            if len(matches) != 4:
                raise ValueError("String does not contain 4 numbers")
            r, g, b, a = [float(val) for val in matches]
        else:
            raise TypeError(f"Unsupported color format: {type(rgba_str)}")


        # Clamp values between 0.0 and 1.0
        r = max(0.0, min(1.0, r))
        g = max(0.0, min(1.0, g))
        b = max(0.0, min(1.0, b))
        a = max(0.0, min(1.0, a))

        # Convert 0-1 floats to 0-255 integers
        r_int = int(r * 255)
        g_int = int(g * 255)
        b_int = int(b * 255)
        a_int = int(a * 255)

        # Format as aabbggrr hex string (Note the order: Alpha, Blue, Green, Red)
        # :02x ensures two digits with leading zero if needed
        kml_color_hex = f"{a_int:02x}{b_int:02x}{g_int:02x}{r_int:02x}"
        return kml_color_hex

    except (ValueError, TypeError) as e:
        print(f"Warning: Could not parse color data '{rgba_str}'. Error: {e}. Using default {default_color}")
        return default_color

# --- Main Script ---
print(f"Loading GeoJSON from: {geojson_file_path}")
try:
    with open(geojson_file_path, 'r') as f:
        geojson_data = json.load(f)
except FileNotFoundError:
    print(f"Error: GeoJSON file not found at {geojson_file_path}")
    exit()
except json.JSONDecodeError:
    print(f"Error: Could not decode JSON from {geojson_file_path}. Check file format.")
    exit()

# Initialize KML object
# Use the filename as the default KML document name if not present in GeoJSON
default_kml_name = geojson_file_path.split('/')[-1].replace('.geojson', '')
kml = simplekml.Kml(name=geojson_data.get('name', default_kml_name))

print("Processing features...")
feature_count = 0
# Iterate through features in the GeoJSON
for feature in geojson_data.get('features', []):
    feature_count += 1
    props = feature.get('properties', {})
    geom = feature.get('geometry', {})

    if not geom:
        print(f"Warning: Feature {feature_count} has no geometry. Skipping.")
        continue

    geom_type = geom.get('type')
    coordinates = geom.get('coordinates')

    if not geom_type or not coordinates:
        print(f"Warning: Feature {feature_count} has invalid geometry data. Skipping.")
        continue

    # --- Styling ---
    # Use 'route' if present, otherwise 'ROUTENAME', otherwise a default
    route_name = props.get('route', props.get('ROUTENAME', f'Feature {feature_count}'))
    color_data = props.get('color') # Get the color property
    kml_color = rgba_string_to_kml_color(color_data) # Convert color

    # Create a KML Style for this specific feature
    # (Alternatively, you could create shared styles if many routes have the same color)
    style = simplekml.Style()
    style.linestyle.color = kml_color
    style.linestyle.width = 4 # Set line width (adjust as needed)

    # --- Geometry Handling ---
    if geom_type == "LineString":
        placemark = kml.newlinestring(name=route_name)
        placemark.coords = coordinates # simplekml handles list of [lon, lat] tuples
        placemark.style = style
        placemark.altitudemode = simplekml.AltitudeMode.clamptoground # Clamp lines to the ground
        placemark.extrude = 0 # Typically 0 for roads, 1 can make it stand up slightly

    elif geom_type == "MultiLineString":
        # For MultiLineString, create a MultiGeometry placemark
        # and add individual LineString geometries to it.
        placemark = kml.newmultigeometry(name=route_name)
        placemark.style = style # Apply style to the whole MultiGeometry
        placemark.altitudemode = simplekml.AltitudeMode.clamptoground
        placemark.extrude = 0

        for line_coords in coordinates:
            # Create simplekml LineString *geometry* (not placemark) inside MultiGeometry
            kml_linestring = simplekml.LineString(coords=line_coords)
            # *** CORRECTED LINE BELOW ***
            placemark.geoms.append(kml_linestring) # Add geometry to the MultiGeometry's geoms list

    else:
        print(f"Warning: Unsupported geometry type '{geom_type}' for feature {feature_count}. Skipping.")
        continue

    # Add other properties as description (optional)
    description = ""
    for key, value in props.items():
        # Exclude geometry-related or style-related keys from the description if desired
        if key not in ['color', 'geometry', 'start', 'end']:
             # Format value nicely (e.g., round floats)
             if isinstance(value, float):
                 value_str = f"{value:.4f}" # Example: round float to 4 decimal places
             else:
                 value_str = str(value)
             description += f"<b>{key}:</b> {value_str}<br>"
    placemark.description = description


print(f"Processed {feature_count} features.")

# Save the KML file
print(f"Saving KML to: {kml_file_path}")
try:
    kml.save(kml_file_path)
    print("KML file saved successfully.")
except Exception as e:
    print(f"Error saving KML file: {e}")

