import csv
import os

# Data file paths
base_data_folder = r"c:\Users\David\Documents\Local_Python\geodesy\dev\data"

datafile_A = os.path.join(base_data_folder, "selected.csv")
datafile_B = os.path.join(base_data_folder, "ref.csv")
#datafile_C = os.path.join(base_data_folder, "CapPnts.csv")

# linepairs_file: exported hand-selected point-pairs from Excel db:
linepairs_file = os.path.join(base_data_folder, "linepairs.csv")
output_file = os.path.join(base_data_folder, "paired_lines.csv")

def load_coords_from_csv(filepath, coords_dict):
    """
    Loads LOC, LAT, LON from a CSV file into a dictionary.
    The dictionary is updated in place.
    Assumes header row with 'LOC', 'LAT', 'LON' columns.
    """
    try:
        with open(filepath, 'r', newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            try:
                header = next(reader)
            except StopIteration:
                print(f"Warning: File {filepath} is empty or has no header.")
                return

            try:
                loc_col_idx = header.index('LOC')
                lat_col_idx = header.index('LAT')
                lon_col_idx = header.index('LON')
            except ValueError as e:
                print(f"Warning: Missing expected column in {filepath}. Error: {e}. Skipping this file.")
                return

            for row_num, row in enumerate(reader, start=2): # start=2 for 1-based data row numbering
                if not row or len(row) <= max(loc_col_idx, lat_col_idx, lon_col_idx):
                    print(f"Warning: Skipping malformed or short row {row_num} in {filepath}: {row}")
                    continue
                try:
                    loc = row[loc_col_idx]
                    lat = row[lat_col_idx]
                    lon = row[lon_col_idx]
                    coords_dict[loc] = (lat, lon)
                except IndexError:
                    print(f"Warning: Skipping row {row_num} in {filepath} due to insufficient columns: {row}")
    except FileNotFoundError:
        print(f"Warning: Data file not found: {filepath}. It will be skipped.")
    except Exception as e:
        print(f"An unexpected error occurred while reading {filepath}: {e}")

def process_line_pairs():
    """
    Main function to process line pairs and generate the output CSV.
    """

    all_points_coords = {}

    # Load points from CSVswith specified precedence:
    #  first (lowest precedence)
    #print(f"Loading data from {datafile_C}...")
    #load_coords_from_csv(datafile_C, all_points_coords)
    #  next (medium precedence)
    print(f"Loading data from {datafile_B}...")
    load_coords_from_csv(datafile_B, all_points_coords)
    #  last (highest precedence)
    print(f"Loading data from {datafile_A}...")
    load_coords_from_csv(datafile_A, all_points_coords)

    if not all_points_coords:
        print("Error: No point data loaded. Please check the data files and paths. Exiting.")
        return

    print(f"\nProcessing {linepairs_file}...")
    try:
        with open(linepairs_file, 'r', newline='', encoding='utf-8') as lp_file, \
             open(output_file, 'w', newline='', encoding='utf-8') as out_f:

            lp_reader = csv.reader(lp_file)
            csv_writer = csv.writer(out_f)

            # Write header to output file
            csv_writer.writerow(['LOC', 'LAT', 'LON'])

            try:
                header_lp = next(lp_reader)
            except StopIteration:
                print(f"Error: {linepairs_file} is empty or has no header. Exiting.")
                return

            try:
                refpnt_col_idx = header_lp.index('Refpnt')
                pnt_col_idx = header_lp.index('Pnt')
            except ValueError as e:
                print(f"Error: Missing expected column ('Refpnt' or 'Pnt') in {linepairs_file}. Error: {e}. Exiting.")
                return

            processed_pairs_count = 0
            skipped_pairs_count = 0
            missing_coords_log = []

            for row_num, row in enumerate(lp_reader, start=2): # start=2 because of header
                if not row or len(row) <= max(refpnt_col_idx, pnt_col_idx):
                    print(f"Warning: Skipping malformed or short row {row_num} in {linepairs_file}: {row}")
                    skipped_pairs_count +=1
                    continue

                try:
                    loc1_id = row[refpnt_col_idx]
                    loc2_id = row[pnt_col_idx]
                except IndexError:
                    print(f"Warning: Skipping row {row_num} in {linepairs_file} due to insufficient columns: {row}")
                    skipped_pairs_count += 1
                    continue
                
                coords1 = all_points_coords.get(loc1_id)
                coords2 = all_points_coords.get(loc2_id)

                if coords1 and coords2:
                    lat1, lon1 = coords1
                    lat2, lon2 = coords2

                    combined_loc_base = f"{loc1_id}{loc2_id}"
                    new_loc1 = f"{combined_loc_base}-0"
                    new_loc2 = f"{combined_loc_base}-1"

                    csv_writer.writerow([new_loc1, lat1, lon1])
                    csv_writer.writerow([new_loc2, lat2, lon2])
                    processed_pairs_count += 1
                else:
                    skipped_pairs_count += 1
                    missing_msg_parts = []
                    if not coords1:
                        missing_msg_parts.append(f"'{loc1_id}'")
                    if not coords2:
                        missing_msg_parts.append(f"'{loc2_id}'")
                    missing_coords_log.append(f"Pair ({loc1_id}, {loc2_id}) at row {row_num}: Missing coordinates for {', '.join(missing_msg_parts)}.")

        print(f"\n--- Processing Summary ---")
        print(f"Successfully processed {processed_pairs_count} pairs.")
        if skipped_pairs_count > 0:
            print(f"Skipped {skipped_pairs_count} pairs due to missing data or malformed rows.")
            if missing_coords_log:
                print("Details of pairs skipped due to missing coordinates:")
                for entry in missing_coords_log:
                    print(f"  - {entry}")
        print(f"Output written to {output_file}")

    except FileNotFoundError:
        print(f"Error: Main input file {linepairs_file} not found. Exiting.")
    except Exception as e:
        print(f"An unexpected error occurred during line pair processing: {e}")

if __name__ == '__main__':
    process_line_pairs()
