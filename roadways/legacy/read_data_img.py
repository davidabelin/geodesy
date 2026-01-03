import rasterio
import matplotlib.pyplot as plt
import numpy as np

def inspect_raster_image(img_file_path):
    """
    Opens, inspects, and displays a sample of a raster image file.

    Args:
        img_file_path (str): The path to the .img raster file.
    """
    try:
        # Open the raster file using rasterio
        with rasterio.open(img_file_path) as src:
            print(f"--- Inspecting: {src.name} ---")

            # Print basic metadata from the raster file
            print(f"Format: {src.driver}") # Should be HFA (Erdas Imagine Images) or similar
            print(f"Dimensions (width x height): {src.width} x {src.height} pixels")
            print(f"Number of bands: {src.count}")
            print(f"Coordinate Reference System (CRS): {src.crs}")
            print(f"Bounds (bounding box in CRS units): {src.bounds}")
            print(f"Transform (affine transformation parameters): {src.transform}")
            print(f"Data types of bands: {src.dtypes}") # e.g., ['uint8', 'uint8', 'uint8']
            print(f"No-data values for bands: {src.nodatavals}")

            # The XML indicated 3 bands, 8 bits per pixel.
            # This is suitable for an RGB image.
            if src.count not in [1, 3, 4]:
                print("\nWarning: Raster does not appear to be a typical grayscale or RGB/RGBA image.")
                print("The script will attempt to display it, but you might need custom band handling.")
                # Optionally, print a sample of pixel values from the first band
                print("\nSample pixel values from the first band (top-left 5x5 corner):")
                # Read a small window from the first band
                print(src.read(1, window=rasterio.windows.Window(0, 0, 5, 5)))
                # If you don't want to proceed with plotting for non-standard bands, you can return here.
                # return

            # Information about the .rrd file (pyramid file)
            # Rasterio uses these automatically if present and correctly named.
            print("\nNote: If a '.rrd' file (e.g., Hawkins_Topography.rrd) exists in the same directory,")
            print("rasterio likely uses it automatically to speed up the display of overviews (pyramids).")
            print("You typically do not need to open the .rrd file directly with rasterio.")

            # --- Reading and Displaying the Image ---
            # For large files, reading the entire dataset into memory can be problematic.
            # We will read a subsampled version for display.

            # Define a subsampling factor. A factor of 10 means 1/10th of rows and 1/10th of columns.
            # Adjust this factor based on your image size and memory capacity.
            # If the image is 10685x8364, subsampling by 10 gives ~1068x836.
            subsampling_factor = 10

            # Ensure subsampling_factor is not too large for the image dimensions
            if src.width < subsampling_factor or src.height < subsampling_factor:
                print(f"\nImage dimensions ({src.width}x{src.height}) are smaller than the subsampling factor ({subsampling_factor}).")
                print("Reading the image at its original resolution or a smaller subsampling if possible.")
                # Adjust subsampling or read full if very small
                if src.width < 10 or src.height < 10: # Arbitrary small size
                    out_shape = (src.count, src.height, src.width)
                    print("Reading full resolution for very small image.")
                else:
                    # Choose a smaller subsampling factor, e.g., 2 or 1 if factor is too large
                    safe_subsampling_factor = max(1, min(subsampling_factor, src.width // 2, src.height // 2))
                    if safe_subsampling_factor == 1 and subsampling_factor > 1:
                         print("Subsampling factor too large, reading full resolution.")
                         out_shape=(src.count, src.height, src.width)
                    else:
                        out_shape=(
                            src.count,
                            int(src.height / safe_subsampling_factor),
                            int(src.width / safe_subsampling_factor)
                        )
                        print(f"Using adjusted subsampling factor: {safe_subsampling_factor}")

            else:
                out_shape=(
                    src.count,
                    int(src.height / subsampling_factor),
                    int(src.width / subsampling_factor)
                )
                print(f"\nReading data with subsampling factor: {subsampling_factor}")


            # Read the data with subsampling.
            # `masked=True` will return a NumPy masked array if nodata values are defined.
            data = src.read(out_shape=out_shape, masked=True)

            print(f"Shape of subsampled data read: {data.shape} (bands, height, width)")
            print(f"Data type of array: {data.dtype}")

            # Prepare data for plotting with matplotlib.
            # Matplotlib imshow expects:
            # - (height, width) for single-band (grayscale)
            # - (height, width, bands) for multi-band (RGB/RGBA)

            if src.count == 1:
                # Single-band image (e.g., elevation data, grayscale)
                image_data_to_plot = data[0] # Get the first (and only) band
                cmap_to_use = 'viridis' # Or 'gray', 'terrain', etc.
                plot_title = f"Subsampled Grayscale Image: {img_file_path.split('/')[-1].split('\\')[-1]}"
            elif src.count == 3:
                # 3-band image (likely RGB)
                # Rasterio reads as (bands, height, width). Transpose to (height, width, bands).
                image_data_to_plot = np.moveaxis(data, 0, -1)
                cmap_to_use = None # Matplotlib handles RGB automatically
                plot_title = f"Subsampled RGB Image: {img_file_path.split('/')[-1].split('\\')[-1]}"
            elif src.count == 4:
                # 4-band image (likely RGBA)
                # Plotting only RGB, ignoring Alpha for simplicity, or you can handle transparency.
                image_data_to_plot = np.moveaxis(data[:3], 0, -1) # Take first 3 bands (RGB)
                cmap_to_use = None
                plot_title = f"Subsampled RGB Image (from RGBA): {img_file_path.split('/')[-1].split('\\')[-1]}"
                print("Displaying RGB components of a 4-band image.")
            else:
                # For other band counts, display the first band as grayscale.
                print(f"Displaying the first band of the {src.count}-band image as grayscale.")
                image_data_to_plot = data[0]
                cmap_to_use = 'viridis'
                plot_title = f"Subsampled First Band: {img_file_path.split('/')[-1].split('\\')[-1]}"

            # Display the image using matplotlib
            print("\nDisplaying a subsampled version of the image...")
            plt.figure(figsize=(12, 10)) # Adjust figure size as needed

            # Handle potential masked arrays by filling nodata values for display if necessary,
            # or let imshow handle it (it often does well with masked arrays).
            # If image_data_to_plot is a MaskedArray, imshow should handle it.
            # If pixel values are integers (like uint8 for 0-255), imshow scales them.
            # If they are floats, they are typically scaled from min to max.
            plt.imshow(image_data_to_plot, cmap=cmap_to_use)

            plt.title(plot_title, fontsize=14)
            plt.xlabel(f"Pixel Column (subsampled by {subsampling_factor if 'safe_subsampling_factor' not in locals() else safe_subsampling_factor})", fontsize=10)
            plt.ylabel(f"Pixel Row (subsampled by {subsampling_factor if 'safe_subsampling_factor' not in locals() else safe_subsampling_factor})", fontsize=10)

            # Add a colorbar, especially useful for single-band data
            if src.count == 1 or (src.count > 4) : # Or if you displayed only one band from a multi-band image
                plt.colorbar(label="Pixel Value")

            plt.show()

    except rasterio.errors.RasterioIOError as e:
        print(f"ERROR: Could not open or read raster file: {img_file_path}")
        print(f"Rasterio error: {e}")
        print("Please ensure the file path is correct and the file is a valid raster format supported by GDAL/rasterio.")
    except FileNotFoundError:
        print(f"ERROR: File not found at path: {img_file_path}")
        print("Please ensure the 'hawkins_img_file' variable points to the correct location of your .img file.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    
    # Examples:
    # On Windows: hawkins_img_file = r"C:\Users\YourName\Documents\GISData\Hawkins_Topography.img"
    # If the script is in the SAME directory as the .img file:
    # hawkins_img_file = "Hawkins_Topography.img"

    hawkins_img_file = r"C:\Users\David\Documents\Local_Python\geodesy\qgis\Hawkins\Hawkins_Topography_unzip\Hawkins_Topography.img"
    # zipped in: "C:\Users\David\Documents\Local_Python\geodesy\qgis\Hawkins\Hawkins_Topography_unzip\Hawkins_Topography.img.zip"

    # --- End of path configuration ---

    if hawkins_img_file == "Hawkins_Topography.img" or not hawkins_img_file or "REPLACE THIS PATH" in hawkins_img_file:
        print("--------------------------------------------------------------------------")
        print("SCRIPT EXECUTION HALTED: File path needs to be configured.")
        print("Please open this script and update the 'hawkins_img_file' variable")
        print("to the correct full path of your 'Hawkins_Topography.img' file.")
        print(r"Example for Windows: hawkins_img_file = r'C:\data\Hawkins_Topography.img'")
        print("Example for macOS/Linux: hawkins_img_file = '/data/Hawkins_Topography.img'")
        print("If the file is in the same directory as the script, just its name is fine:")
        print("   hawkins_img_file = 'Hawkins_Topography.img'")
        print("--------------------------------------------------------------------------")
    else:
        inspect_raster_image(hawkins_img_file)

