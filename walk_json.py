# originally c:\Users\David\Documents\Local_Python\zenbot\training\trainset03\walk_json.py
# Usage: python walk_json.py some_json_data.json
import json
import argparse
import os

def walk_json_recursive(data, depth=0, prefix=""):
    """
    Recursively walks through a JSON-like data structure (dicts, lists, primitives)
    and prints its structure with indentation.

    Args:
        data: The current piece of data (dict, list, str, int, float, bool, None).
        depth (int): The current recursion depth for indentation.
        prefix (str): A string to prepend to the output line (e.g., key name, index).
    """
    indent = "  " * depth  # 2 spaces per indentation level
    full_prefix = f"{indent}{prefix}"

    if isinstance(data, dict):
        # Print dictionary marker and size
        print(f"{full_prefix} {len(data)} keys:")
        # Recursively call for each key-value pair
        # Sort keys for consistent output order (optional but helpful)
        for key in sorted(data.keys()):
            value = data[key]
            #if key in ['STONE_NUM', 'LATITUDE', 'LONGITUDE']:
                # Pass the key information down as the prefix for the next level
            walk_json_recursive(value, depth + 1, prefix=f"'{key}' ")

    elif isinstance(data, list):
        # Print list marker and size
        print(f"{full_prefix} [{len(data)}]:")
        # Recursively call for each item in the list
        for i, item in enumerate(data):
            # Pass the index information down as the prefix for the next level
            walk_json_recursive(item, depth + 1, prefix=f"[{i}] ")

    elif isinstance(data, str):
        # Print string value (truncate if too long)
        # Replace newlines for cleaner single-line output
        display_str = data.replace('\n', '\n').replace('\r', '')
        if len(display_str) > 35:
            display_str = display_str[:32] + "..."
        print(f"{full_prefix} \"{display_str}\"")

    elif isinstance(data, (int, float)):
        # Print numeric value
        print(f"{full_prefix} {data}")

    elif isinstance(data, bool):
        # Print boolean value
        print(f"{full_prefix} {data}")

    elif data is None:
        # Print None value
        print(f"{full_prefix} (empty)")

    else:
        # Handle any unexpected types
        print(f"{full_prefix}Unknown Type: {type(data).__name__}")


def main():
    """
    Main function to parse arguments, load JSON, and start the walk.
    """
    parser = argparse.ArgumentParser(
        description="Walk through a JSON file and print its hierarchical structure."
    )
    parser.add_argument(
        "json_file",
        help="Path to the input JSON file to analyze."
    )
    args = parser.parse_args()

    input_path = args.json_file

    # --- Validate Input File ---
    if not os.path.exists(input_path):
        print(f"Error: File not found at '{input_path}'")
        return
    if not os.path.isfile(input_path):
        print(f"Error: Path '{input_path}' is not a file.")
        return

    print(f"--- Walking JSON structure in: {os.path.basename(input_path)} ---")

    # --- Load and Parse JSON ---
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            # Attempt to load the entire file as a single JSON object/array
            try:
                data = json.load(f)
                print(f"Successfully loaded '{os.path.basename(input_path)}' as a single JSON object/array.")
                # Start the recursive walk from the root
                walk_json_recursive(data, prefix="Root: ")
            except json.JSONDecodeError as e_single:
                # If loading as single object fails, try loading as JSON Lines (JSONL)
                print(f"Could not load as single JSON object ({e_single}). Attempting to read as JSON Lines (JSONL)...")
                f.seek(0) # Reset file pointer to the beginning
                data_lines = []
                has_content = False
                for i, line in enumerate(f):
                    line = line.strip()
                    if not line: # Skip empty lines
                        continue
                    has_content = True
                    try:
                        data_lines.append(json.loads(line))
                    except json.JSONDecodeError as e_line:
                        print(f"\nError: Failed to decode JSON on line {i+1} in '{os.path.basename(input_path)}'.")
                        print(f"Line content: {line[:100]}...") # Show start of problematic line
                        print(f"Details: {e_line}")
                        return # Stop processing on error

                if not has_content:
                     print("Error: File is empty or contains only whitespace.")
                     return

                print(f"Successfully loaded '{os.path.basename(input_path)}' as JSON Lines ({len(data_lines)} lines).")
                # Treat the list of loaded lines as the root data structure
                walk_json_recursive(data_lines, prefix="Root (List of Lines): ")

    except FileNotFoundError:
        # This case is already handled by os.path.exists, but good practice
        print(f"Error: File not found at '{input_path}'")
    except Exception as e:
        print(f"\nAn unexpected error occurred while reading or processing the file:")
        print(f"Error Type: {type(e).__name__}")
        print(f"Details: {e}")

    print(f"--- End of walk for {os.path.basename(input_path)} ---")

# --- Script Execution ---
if __name__ == "__main__":
    main()
