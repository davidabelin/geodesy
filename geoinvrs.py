import csv
import argparse
from geometry import inverse_geodetic

def open_file(csv_filepath):
    pntdict = {}
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
                loc = row.get('LOC')
                lat_str = row.get('LAT')
                lon_str = row.get('LON')

                if not all([loc, lat_str, lon_str]):
                    print(f"Warning: Skipping row {i+2} in CSV due to missing V, Lat, or Lon data: {row}")
                    continue
                try:
                    # Ensure Lat and Lon are stripped of potential whitespace before conversion
                    lat = float(lat_str.strip())
                    lon = float(lon_str.strip())
                except ValueError:
                    print(f"Warning: Skipping row {i+2} (ID: {loc}) due to invalid lat/lon: Lat='{lat_str}', Lon='{lon_str}'")
                    continue
            pntdict.update( {'loc': loc,
                            'lat': lat,
                            'lon': lon})
        for x in pntdict:
            print(x)
    except FileNotFoundError:
        print(f"Error: The file {csv_filepath} was not found.")
        return
    except Exception as e:
        print(f"An unexpected error occurred while reading {csv_filepath}: {e}")
        return
    return pntdict

if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Spherical Geometry Toolkit')
    sub = p.add_subparsers(dest='cmd')
    
    c_help = 'Input file of points with required columns LOC, LAT, LON, returns azimuth and dist between them all.'
    c = sub.add_parser('csv', help=c_help)
    c.add_argument('file',type=str, help='Required: csv file path')
    
    args = p.parse_args()
    #TO DO: args for single pnt pair
    
    if args.cmd == 'csv':
        pntdict = open_file(args.file)
        print(pntdict)
        refdict = {}
        for pnt in pntdict:
            lat1=pnt['lat']
            lon1=pnt['lon']
            bddict = {}
            for p in pntdict:
                if p['loc'] != pnt['loc']:
                    lat2=p['lat']
                    lon2=p['lon']
                    az, _, dist = inverse_geodetic(lat1,lon1,
                                                    lat2,lon2,
                                                    unit='miles')
                    bddict.update({p['loc']: {'az':az, 'dist':dist}})
            refdict.update({pnt['loc']: bddict})
        print("Refpnt, Pnt, Az, Dist")
        for refpnt in refdict:
            for pnt in refpnt:
                print(f"{refpnt}, {pnt}, {refdict[refpnt][pnt]['az']}, {refdict[refpnt][pnt]['dist']}")
    #elif: lat lon lat lon  TO DO
    else:
        p.print_help()
