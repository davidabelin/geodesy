import csv
import math
#import os
#import sys

import argparse
from typing import Tuple
from pyproj import Geod
_GEOID = Geod(ellps='WGS84')
EARTH_RADIUS_MILES = 3958
EARTH_RADIUS_FEET = EARTH_RADIUS_MILES * 5280
MILES_PER_FOOT = 1 / 5280.0
MILES_PER_YARD = 3 / 5280.0
MILES_PER_POLE = 16.5 / 5280.0  # 1 rod = 16.5 ft
DEG2RAD = math.pi / 180.0
RAD2DEG = 180.0 / math.pi
FIp = (math.sqrt(5)+1)/2
FIm = (math.sqrt(5)-1)/2

def inverse_geodetic(phi1: float, lam1: float, phi2: float, lam2: float,
                      unit: str='miles', ellipsoid: bool=False) -> Tuple[float, float, float]:
    """
    Inverse geodetic: given two lat/lon points -> az1, az2, distance.
    """
    if ellipsoid:
        az1, az2, dist_m = _GEOID.inv(lam1, phi1, lam2, phi2)
        dist = dist_m / (1609.344 if unit=='miles' else 0.3048)
        return az1 % 360, az2 % 360, dist
    # spherical fallback
    phi1r, phi2r = phi1*DEG2RAD, phi2*DEG2RAD
    dlam = (lam2 - lam1)*DEG2RAD
    cos_sigma = math.sin(phi1r)*math.sin(phi2r) + math.cos(phi1r)*math.cos(phi2r)*math.cos(dlam)
    sigma = math.acos(max(-1, min(1, cos_sigma)))
    az1 = math.atan2(math.sin(dlam)*math.cos(phi2r),
                      math.cos(phi1r)*math.sin(phi2r) - math.sin(phi1r)*math.cos(phi2r)*math.cos(dlam))
    az2 = math.atan2(math.sin(-dlam)*math.cos(phi1r),
                      math.cos(phi2r)*math.sin(phi1r) - math.sin(phi2r)*math.cos(phi1r)*math.cos(dlam))
    dist = EARTH_RADIUS_MILES * sigma
    if unit!='miles':
        dist *= 5280
    return az1*RAD2DEG % 360, az2*RAD2DEG % 360, dist

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
                # Store each point's data under its location identifier
                pntdict[loc] = {'lat': lat, 'lon': lon}

    except FileNotFoundError:
        print(f"Error: The file {csv_filepath} was not found.")
        return {} # Return an empty dictionary on error
    except Exception as e:
        print(f"An unexpected error occurred while reading {csv_filepath}: {e}")
        return {} # Return an empty dictionary on error
    return pntdict

if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Inverse Geodetic Tool')
    sub = p.add_subparsers(dest='cmd')
    
    c_help = 'Input file of points with required columns LOC, LAT, LON; returns azimuth and dist between them all.'
    c = sub.add_parser('csv', help=c_help)
    c.add_argument('file',type=str, help='Required: csv file path')
    c.add_argument('--ellipsoid',action='store_true', help='Use WGS84 ellipsoid (default: spherical)')
    
    args = p.parse_args()
    #TO DO: args for single pnt pair
    
    if args.cmd == 'csv':
        pntdict = open_file(args.file)
        if not pntdict:
            print("No data loaded from file. Exiting.")
        #else:
        #    print("Loaded points:", pntdict) # For verification
        refdict = {}
        # Iterate through each point as the reference point (pnt1)
        for pnt1_loc, pnt1_data in pntdict.items():
            lat1 = pnt1_data['lat']
            lon1 = pnt1_data['lon']
            bddict = {}
            # Iterate through each point as the target point (pnt2)
            for pnt2_loc, pnt2_data in pntdict.items():
                if pnt1_loc != pnt2_loc: # Don't compare a point to itself
                    lat2 = pnt2_data['lat']
                    lon2 = pnt2_data['lon']
                    az, baz, dist = inverse_geodetic(lat1,lon1,
                                                    lat2,lon2,
                                                    unit='miles',
                                                    ellipsoid=args.ellipsoid)
                    bddict[pnt2_loc] = {'az':az, 'baz':baz, 'dist':dist}
            refdict[pnt1_loc] = bddict
        print("Refpnt, Pnt, Az, Back, Dist")
        for refpnt_loc, connections in refdict.items():
            for pnt_loc, data in connections.items():
                print(f"{refpnt_loc}, {pnt_loc}, {data['az']:.2f}, {data['baz']:.2f}, {data['dist']:.6f}")
    else:
        p.print_help()
