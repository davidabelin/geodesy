# C:\Users\David\Documents\Local_Python\geodesy\dev\walk_kml.py
import xml.etree.ElementTree as ET
import os

# --- Configuration ---
# Use a raw string (r'...') for Windows paths to handle backslashes correctly
kml_file_path = r"C:\Users\David\Documents\Local_Python\geodesy\data\Intersections.kml"
#\centerpnts.kml'
# --- End Configuration ---

# Define the KML namespace - KML files use namespaces, which we need for searching
# The main namespace is usually defined at the <kml> tag
# From kml file: xmlns="http://www.opengis.net/kml/2.2"
namespaces = {'kml': 'http://www.opengis.net/kml/2.2'}

extracted_points = []

# Check if the file exists
if not os.path.exists(kml_file_path):
    print(f"Error: File not found at {kml_file_path}")
else:
    try:
        # Parse the XML file
        tree = ET.parse(kml_file_path)
        root = tree.getroot()

        # Find all Placemark elements within the document
        # Using './/' searches the entire tree under the root
        for placemark in root.findall('.//kml:Placemark', namespaces):
            # Find the Point element *within this specific Placemark*
            # Using '.' searches only within the current placemark element
            point_element = placemark.find('kml:Point', namespaces)

            # Check if this Placemark actually contains a Point element
            if point_element is not None:
                # Find the name element within the Placemark
                name_element = placemark.find('kml:name', namespaces)
                placemark_name = name_element.text.strip() if name_element is not None and name_element.text else "Unnamed Placemark"

                # Find the coordinates element within the Point
                coordinates_element = point_element.find('kml:coordinates', namespaces)
                coordinates_text = coordinates_element.text.strip() if coordinates_element is not None and coordinates_element.text else "No Coordinates"
                coord_split = coordinates_text.split(',')
                coord = (coord_split[1], coord_split[0])
                # Store the found data
                extracted_points.append({
                    'name': placemark_name,
                    'coordinates': coord
                })

    except ET.ParseError as e:
        print(f"Error parsing KML file: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

# Print the results
if extracted_points:
    print(f"Found {len(extracted_points)} Placemarks with Point coordinates in '{os.path.basename(kml_file_path)}':")
    print("-" * 30)
    for point in extracted_points:
        print(f"{point['name']},{point['coordinates'][0][:11]},{point['coordinates'][1][:12]}")    
        #print(f"Name: {point['name']}")
        #print(f"Coordinates: {point['coordinates']}")
        #print("-" * 10)
else:
    print("No Placemarks containing Point coordinates were found in the file.")
