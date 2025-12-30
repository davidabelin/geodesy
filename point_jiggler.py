# /geodesy/point_jiggler.py — v4.2
"""
CURRENT VERSION — v4.2 (Corrected Default)

------------------------------------------------
3.55
Optimize point coordinates based on:
  - Geometric distance/azimuths (targets.csv)
  - Triangle-based criteria (tarcrits.csv)
  - Altitude maxima (DEM GeoTIFF)

Usage:
  python point_jiggler.py input.kml targets.csv tarcrits.csv --dem dem.tif output.kml \
      [--geom-weight 1.0] [--tri-weight 1.0] [--alt-weight 1.0] [--max-it 100]
------------------------------------------------

3.5
Optimize point coordinates based on:
  - Geometric distance/azimuth targets (targets.csv)
  - Triangle-based criteria (side ratios/angle multiples) (tarcrits.csv)
  - Altitude constraints from a DEM GeoTIFF

Usage:
  python point_jiggler.py \
      input.kml targets.csv tarcrits.csv --dem dem.tif output.kml \
      [--geom-weight 1.0] [--tri-weight 1.0] [--alt-weight 0.5] [--max-it 100]

All placeholders have been replaced with working code, and key steps documented.
------------------------------------------------

Version 3.0
**point_jiggler.py Key DEM Logic (v3.0)**

This script **requires** a DEM file for all altitude-based calculations. Any point flagged with `optimize_altitude=True` will cause the script to exit if the DEM cannot be loaded.

Below is the fully integrated `point_jiggler.py` combining our original code with:

1. Mandatory `--dem` argument.
2. DEM loading and `GEOD` initialization based on the DEM’s CRS.
3. `load_tarcrits()` inserted seamlessly alongside `load_targets()`.
4. Updated `calculate_total_error()` signature to include `tarcrits` and enforce altitude errors.

Optimize point coordinates based on:
 - Geometric distance/azimuth targets
 - Triangle-based criteria (side ratios/angle multiples)
 - Altitude constraints from a DEM GeoTIFF

Usage:
  python point_jiggler.py input.kml targets.csv tarcrits.csv --dem dem.tif output.kml
This script uses optimization algorithms to "jiggle" the coordinates of points
in a KML file to best satisfy a set of geometric and/or altitude-based constraints.

Basic CLI Usage:
python point_jiggler.py
            --dem data/dc_dem.tif 
            --geom-weight 0.8 
            --alt-weight 1.2 
            --max-iterations 64 
            data/JigglePnts.kml data/tarcrits.csv data/JiggledSet01.kml
------------------------------------------------

VERSION 2.5
This is `point_jiggler.py` script, implementing folder/point-based `jiggle-rating` and `jiggle-alt` as described above. All relevant code insertion points are included.

# point_jiggler.py (Refactored for per-folder/point jiggle-rating & jiggle-alt)

This script optimizes coordinates of points in a KML file, using geometric and/or altitude-based constraints.
Supports per-folder and per-point jiggle-rating (1=full mobility, 5=anchor) and jiggle-alt (True/False) for altitude-seeking points.

* **Per-folder and per-point `jiggle-rating`/`jiggle-alt` now work automatically, by inheritance with optional per-point override.**
* **The bounds on each point are set according to the rating (rating=5 means fully anchored).**
* **Output KML includes these as ExtendedData for audit.**
------------------------------------------------

VERSION 2.0
Core Concepts:
1.  Objective Function: A master "score" is calculated that represents how
    well the current point configuration meets all desired goals. The optimizer's
    job is to find the coordinates that minimize this score.

2.  Point Groups (from KML Folders): Points are grouped by the KML <Folder>
    they reside in. Points in a folder named "HillPnts" will be flagged for
    altitude optimization. Points in other folders will not.

3.  Geometric Error: A measure of how well the distances and azimuths between
    all pairs of points match a list of targets (e.g., specific distances,
    multiples of a value, side ratios of special triangles).

4.  Altitude Error: For a point in the "HillPnts" group, this measures how
    close they are to the highest possible elevation in their local vicinity.
    This error is normalized and weighted.

5.  NEEDS UPDATE: Jiggle Rating: For now, this is assumed to be a property we can add later.
    The primary control for mobility is whether a point is an anchor or not.

6.  Weights: The user provides weights to balance the importance of the
    geometric goals vs. the altitude-seeking goals.
------------------------------------------------

"""
import sys
import csv
import os
import itertools
import argparse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from scipy.optimize import minimize
from pyproj import Geod, CRS as PyprojCRS
import rasterio
from rasterio import crs as rio_crs
from rasterio.warp import transform
import simplekml

# --- Global ---
GEOD = None
DEM_NODATA = None
ns = {'kml': 'http://www.opengis.net/kml/2.2'}

# ---------------------
# KML Parsing Helpers (from v3.0 logic)
# ---------------------

def get_folder_properties(folder):
    """Parses jiggle-rating and jiggle-alt from a folder's ExtendedData."""
    # FIX: Restore the original default rating of 4.
    rating = 4
    jiggle_alt = False
    ext_data = folder.find('kml:ExtendedData', ns)
    if ext_data is not None:
        for data in ext_data.findall('kml:Data', ns):
            name = data.get('name', '').lower()
            value_node = data.find('kml:value', ns)
            if value_node is not None and value_node.text is not None:
                value = value_node.text.strip().lower()
                if name in ('jiggle-rating', 'jiggle_rating'):
                    try:
                        rating = int(value)
                    except (ValueError, TypeError):
                        pass
                elif name in ('jiggle-alt', 'jiggle_alt'):
                    jiggle_alt = value in ('true', '1', 'yes')
    return rating, jiggle_alt

def get_point_overrides(placemark, folder_rating, folder_jiggle_alt):
    """Parses point-specific overrides, inheriting from folder if not present."""
    rating = folder_rating
    jiggle_alt = folder_jiggle_alt
    ext_data = placemark.find('kml:ExtendedData', ns)
    if ext_data is not None:
        for data in ext_data.findall('kml:Data', ns):
            name = data.get('name', '').lower()
            value_node = data.find('kml:value', ns)
            if value_node is not None and value_node.text is not None:
                value = value_node.text.strip().lower()
                if name in ('jiggle-rating', 'jiggle_rating'):
                    try:
                        rating = int(value)
                    except (ValueError, TypeError):
                        pass
                elif name in ('jiggle-alt', 'jiggle_alt'):
                    jiggle_alt = value in ('true', '1', 'yes')
    return rating, jiggle_alt

def parse_kml_v3(kml_path):
    """
    Parses a KML file, reading point coordinates and inheriting jiggle
    properties from parent folders, with point-specific overrides.
    """
    points = {}
    try:
        tree = ET.parse(kml_path)
        root = tree.getroot()
        doc = root.find('kml:Document', ns)
        if doc is None:
            doc = root # Handle case where Document is the root

        # Process points within folders
        for folder in doc.findall('.//kml:Folder', ns):
            folder_rating, folder_jiggle_alt = get_folder_properties(folder)
            for pm in folder.findall('kml:Placemark', ns):
                name = pm.findtext('kml:name', default='', namespaces=ns)
                coords_text = pm.findtext('.//kml:coordinates', default='', namespaces=ns).strip()
                if not name or not coords_text:
                    continue
                
                coords = coords_text.split(',')
                lon, lat, alt = map(float, coords[:3])
                rating, jiggle_alt = get_point_overrides(pm, folder_rating, folder_jiggle_alt)
                points[name] = {'lat': lat, 'lon': lon, 'alt': alt,
                                'rating': rating, 'opt_alt': jiggle_alt}

        # Process points at the root level (not in any folder)
        for pm in doc.findall('kml:Placemark', ns):
             name = pm.findtext('kml:name', default='', namespaces=ns)
             if name and name not in points: # Avoid re-processing points already in folders
                coords_text = pm.findtext('.//kml:coordinates', default='', namespaces=ns).strip()
                if not coords_text: continue
                
                coords = coords_text.split(',')
                lon, lat, alt = map(float, coords[:3])
                # FIX: Inherit from a default RATING OF 4 for root points
                rating, jiggle_alt = get_point_overrides(pm, 4, False)
                points[name] = {'lat': lat, 'lon': lon, 'alt': alt,
                                'rating': rating, 'opt_alt': jiggle_alt}

    except ET.ParseError as e:
        print(f"Error parsing KML file: {e}"); sys.exit(1)
    except FileNotFoundError:
        print(f"Error: KML file not found at {kml_path}"); sys.exit(1)

    if not points:
        print('Error: no placemarks found'); sys.exit(1)
    
    print(f"Loaded {len(points)} points from KML (using folder-aware parsing).")
    return points

# ------------------
# Load Targets.csv
# ------------------
def load_targets(path):
    dists, azs = [], []
    try:
        with open(path) as f:
            reader = csv.reader(f)
            next(reader)
            for row in reader:
                t, v = row[0].lower(), float(row[1])
                if 'dist' in t: dists.append(v)
                if 'az' in t: azs.append(v%360)
    except FileNotFoundError:
        print(f"Warning: Targets file '{path}' not found. Proceeding without geometric targets.")
    return {'dists':dists,'azs':azs}

# --------------------
# Load Tarcrits.csv
# --------------------
def load_tarcrits(path):
    groups = []
    try:
        with open(path) as f:
            reader = csv.DictReader(f)
            for r in reader:
                grp, typ = r['TriGroup'], r['Type'].lower()
                A, B, C = float(r['A']), float(r['B']), float(r['C'])
                groups.append({'grp': grp, 'type': typ, 'ABC': sorted([A, B, C])})
    except FileNotFoundError:
        print(f"Warning: Triangle criteria file '{path}' not found. Proceeding without triangle targets.")
    return groups

# ---------------------
# DEM & Geod init (from v4.0)
# ---------------------
def init_dem(dem_path):
    global DEM_NODATA, GEOD
    try:
        ds = rasterio.open(dem_path)
    except rasterio.errors.RasterioIOError as e:
        print(f"Error opening DEM file: {e}"); sys.exit(1)
        
    DEM_NODATA = ds.nodata
    dem_pyproj_crs = PyprojCRS(ds.crs)

    if dem_pyproj_crs.is_projected:
        geod_crs = dem_pyproj_crs.sub_crs_list[0]
    else:
        geod_crs = dem_pyproj_crs

    ellps = geod_crs.ellipsoid
    GEOD = Geod(a=ellps.semi_major_metre, rf=ellps.inverse_flattening)
    return ds

# -------------------------
# Sample DEM & Gradient
# -------------------------
def sample_slope(dem_ds, lat, lon):
    src_crs=rio_crs.CRS.from_epsg(4326); dst=dem_ds.crs
    x, y = (lon, lat)
    if src_crs != dst:
        xs, ys = transform(src_crs, dst, [lon], [lat]); x, y = xs[0], ys[0]
    
    zs=[]
    for dx,dy in [(0,0),(1,0),(0,1),(-1,0),(0,-1)]:
        try:
            val = next(dem_ds.sample([(x + dx, y + dy)]))[0]
            if DEM_NODATA is not None and val == DEM_NODATA: return 1e6
            zs.append(val)
        except (StopIteration, IndexError):
            return 1e6
            
    if len(zs) < 5: return 1e6
    dzdx = (zs[1] - zs[3]) / 2; dzdy = (zs[2] - zs[4]) / 2
    return dzdx*dzdx + dzdy*dzdy

# --------------------------------
# Objective Function
# --------------------------------
def _make_slices(total, slice_count):
    if total <= 0:
        return []
    slice_count = max(1, min(slice_count, total))
    base = total // slice_count
    rem = total % slice_count
    out = []
    start = 0
    for i in range(slice_count):
        end = start + base + (1 if i < rem else 0)
        out.append((start, end))
        start = end
    return out

def _geom_pairs_slice(pairs, pts, targets, start, end):
    ge = 0.0
    dists = targets.get('dists') or []
    azs = targets.get('azs') or []
    for a, b in pairs[start:end]:
        pa, pb = pts[a], pts[b]
        az1, _, dist = GEOD.inv(pa['lon'], pa['lat'], pb['lon'], pb['lat'])
        if dists:
            ge += min((dist - t)**2 for t in dists)
        if azs:
            ge += min(((az1 % 360) - t)**2 for t in azs)
    return ge

def _triangles_slice(triples, pts, tris, start, end):
    tr = 0.0
    for a, b, c in triples[start:end]:
        pa, pb, pc = pts[a], pts[b], pts[c]
        ds = sorted([GEOD.inv(p['lon'], p['lat'], q['lon'], q['lat'])[2]
                     for p, q in [(pa, pb), (pb, pc), (pc, pa)]])
        for g in tris:
            if g['type'] == 's' and ds[0] > 0: # Side Ratios
                tr += sum(((ds[i] / ds[0]) - (g['ABC'][i] / g['ABC'][0]))**2 for i in range(1,3))
    return tr

def objective_function(x, names, pts, targets, tris, dem_ds, w, all_pairs, pair_slices, all_triples, triple_slices, executor):
    for i, n in enumerate(names):
        pts[n]['lat'], pts[n]['lon'] = x[2*i], x[2*i+1]
    ge, tr, al = 0.0, 0.0, 0.0
    
    if all_pairs:
        if executor is not None and pair_slices:
            futs = [executor.submit(_geom_pairs_slice, all_pairs, pts, targets, s, e) for s, e in pair_slices]
            ge = sum(f.result() for f in futs)
        else:
            ge = _geom_pairs_slice(all_pairs, pts, targets, 0, len(all_pairs))
    
    if tris and all_triples:
        if executor is not None and triple_slices:
            futs = [executor.submit(_triangles_slice, all_triples, pts, tris, s, e) for s, e in triple_slices]
            tr = sum(f.result() for f in futs)
        else:
            tr = _triangles_slice(all_triples, pts, tris, 0, len(all_triples))
    
    for n in names:
        p = pts[n]
        if p['opt_alt']:
            al += sample_slope(dem_ds, p['lat'], p['lon'])
            
    return w['geom'] * ge + w['tri'] * tr + w['alt'] * al

# ----------------
# Main Routine
# ----------------
def main():
    p = argparse.ArgumentParser(description="Jiggle KML points to meet constraints.")
    p.add_argument('in_kml')
    p.add_argument('targs')
    p.add_argument('tcrit')
    p.add_argument('--dem', required=True)
    p.add_argument('out_kml')
    p.add_argument('--geom-weight', type=float, default=1.0)
    p.add_argument('--tri-weight', type=float, default=1.0)
    p.add_argument('--alt-weight', type=float, default=1.0)
    p.add_argument('--max-it', type=int, default=100)
    p.add_argument('--fast', action='store_true',
                   help='Use all logical CPUs (threads) to speed up distance/triangle evaluation.')
    args = p.parse_args()

    pts = parse_kml_v3(args.in_kml)
    tgs = load_targets(args.targs)
    tcs = load_tarcrits(args.tcrit)
    dem_ds = init_dem(args.dem)
    
    names = [n for n, d in pts.items() if d['rating'] > 0]
    
    if not names:
        print("\nWarning: No points with jiggle_rating > 0 found in KML. Nothing to optimize.")
        print("This may be because no points or their parent folders have a rating set to a value > 0.\n")
        sys.exit(0)
    
    print(f"Optimizing {len(names)} points: {', '.join(names)}")

    x0 = []
    for n in names:
        x0.extend([pts[n]['lat'], pts[n]['lon']])
        
    w = {'geom': args.geom_weight, 'tri': args.tri_weight, 'alt': args.alt_weight}

    all_pairs = list(itertools.combinations(names, 2))
    all_triples = list(itertools.combinations(names, 3)) if tcs else []

    executor = None
    pair_slices = []
    triple_slices = []
    if args.fast:
        max_workers = os.cpu_count() or 1
        executor = ThreadPoolExecutor(max_workers=max_workers)
        pair_slices = _make_slices(len(all_pairs), max_workers * 4)
        triple_slices = _make_slices(len(all_triples), max_workers * 4)

    try:
        res = minimize(objective_function, x0,
                     args=(names, pts, tgs, tcs, dem_ds, w, all_pairs, pair_slices, all_triples, triple_slices, executor),
                     method='L-BFGS-B', options={'maxiter': args.max_it, 'disp': True})
    finally:
        if executor is not None:
            executor.shutdown(wait=True)
        dem_ds.close()
                 
    if not res.success:
        print('Optimization failed or did not converge:', res.message)

    final_x = res.x
    for i, n in enumerate(names):
        pts[n]['lat'], pts[n]['lon'] = final_x[2*i], final_x[2*i+1]
        
    kml = simplekml.Kml()
    for n, p in pts.items():
        kml.newpoint(name=n, coords=[(p['lon'], p['lat'], p['alt'])])
        
    kml.save(args.out_kml)
    print('Saved final optimized points to', args.out_kml)

if __name__ == '__main__':
    main()