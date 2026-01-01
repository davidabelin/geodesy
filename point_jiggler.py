# /geodesy/point_jiggler.py — Jiggle KML Points to Meet Geometric & Altitude Constraints
### v5 in development  ###

"""
CURRENT VERSION —v5+

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
from concurrent.futures import ProcessPoolExecutor
import re
from scipy.optimize import minimize
from pyproj import Geod, CRS as PyprojCRS
import rasterio
from rasterio import crs as rio_crs
from rasterio.warp import transform
import simplekml

# --- Global ---
GEOD = None
GEOD_A = None
GEOD_RF = None
DEM_NODATA = None
ns = {'kml': 'http://www.opengis.net/kml/2.2'}

# ---------------------
# KML Parsing Helpers (from v3.0 logic)
# ---------------------

_DESC_RATING_RE = re.compile(r'jiggle\s*(?:factor|rating)\s*:\s*(\d+)', re.IGNORECASE)
_DESC_ALT_RE = re.compile(r'altitude\s*jiggle\s*:\s*(true|false|1|0|yes|no)', re.IGNORECASE)

def _parse_jiggle_description(desc_text):
    if not desc_text:
        return None, None

    rating = None
    jiggle_alt = None

    m = _DESC_RATING_RE.search(desc_text)
    if m:
        try:
            rating = int(m.group(1))
        except ValueError:
            rating = None

    m = _DESC_ALT_RE.search(desc_text)
    if m:
        v = m.group(1).strip().lower()
        jiggle_alt = v in ('true', '1', 'yes')

    return rating, jiggle_alt

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

    # Fallback: some KMLs encode these as human-readable <description> text.
    desc_text = folder.findtext('kml:description', default='', namespaces=ns)
    desc_rating, desc_alt = _parse_jiggle_description(desc_text)
    if desc_rating is not None:
        rating = desc_rating
    if desc_alt is not None:
        jiggle_alt = desc_alt
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

    # Fallback to <description> if present.
    desc_text = placemark.findtext('kml:description', default='', namespaces=ns)
    desc_rating, desc_alt = _parse_jiggle_description(desc_text)
    if desc_rating is not None:
        rating = desc_rating
    if desc_alt is not None:
        jiggle_alt = desc_alt
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
                points[name] = {'lat': lat, 'lon': lon, 'alt': alt, 'lat0': lat, 'lon0': lon,
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
                points[name] = {'lat': lat, 'lon': lon, 'alt': alt, 'lat0': lat, 'lon0': lon,
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
_DIST_UNIT_TO_METERS = {
    'meters': 1.0,
    'feet': 0.3048,
    'miles': 1609.344,
}

def _dist_to_meters(value, unit):
    try:
        factor = _DIST_UNIT_TO_METERS[unit]
    except KeyError:
        raise ValueError(f"Unsupported distance unit: {unit!r}")
    return float(value) * factor

def load_targets(path, dist_unit='miles'):
    dists, azs = [], []
    try:
        with open(path) as f:
            reader = csv.reader(f)
            next(reader)
            for row in reader:
                t, v = row[0].lower(), float(row[1])
                if 'dist' in t: dists.append(_dist_to_meters(v, dist_unit))
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
    global DEM_NODATA, GEOD, GEOD_A, GEOD_RF
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
    GEOD_A = ellps.semi_major_metre
    GEOD_RF = ellps.inverse_flattening
    GEOD = Geod(a=GEOD_A, rf=GEOD_RF)
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
            if ds[0] > 0: # Side Ratios
                r1, r2 = g.get('ratios') or (g['ABC'][1] / g['ABC'][0], g['ABC'][2] / g['ABC'][0])
                tr += ((ds[1] / ds[0]) - r1) ** 2 + ((ds[2] / ds[0]) - r2) ** 2
    return tr

_FAST_GEOD = None
_FAST_PAIR_IDX = None
_FAST_TRIPLE_IDX = None
_FAST_TARGET_DISTS = None
_FAST_TARGET_AZS = None
_FAST_TRI_RATIOS = None
_FAST_ALT_IDX = None
_FAST_DEM_DS = None
_FAST_DEM_NODATA = None
_FAST_DEM_CRS = None
_FAST_SRC_CRS = rio_crs.CRS.from_epsg(4326)

def _fast_init(a, rf, pair_idx, triple_idx, target_dists, target_azs, tri_ratios, alt_idx, dem_path):
    global _FAST_GEOD, _FAST_PAIR_IDX, _FAST_TRIPLE_IDX, _FAST_TARGET_DISTS, _FAST_TARGET_AZS, _FAST_TRI_RATIOS
    global _FAST_ALT_IDX, _FAST_DEM_DS, _FAST_DEM_NODATA, _FAST_DEM_CRS
    _FAST_GEOD = Geod(a=a, rf=rf)
    _FAST_PAIR_IDX = pair_idx
    _FAST_TRIPLE_IDX = triple_idx
    _FAST_TARGET_DISTS = target_dists
    _FAST_TARGET_AZS = target_azs
    _FAST_TRI_RATIOS = tri_ratios
    _FAST_ALT_IDX = alt_idx

    _FAST_DEM_DS = rasterio.open(dem_path)
    _FAST_DEM_NODATA = _FAST_DEM_DS.nodata
    _FAST_DEM_CRS = _FAST_DEM_DS.crs

def _fast_sample_slope(lat, lon):
    x, y = lon, lat
    if _FAST_SRC_CRS != _FAST_DEM_CRS:
        xs, ys = transform(_FAST_SRC_CRS, _FAST_DEM_CRS, [lon], [lat])
        x, y = xs[0], ys[0]

    zs = []
    for dx, dy in [(0, 0), (1, 0), (0, 1), (-1, 0), (0, -1)]:
        try:
            val = next(_FAST_DEM_DS.sample([(x + dx, y + dy)]))[0]
            if _FAST_DEM_NODATA is not None and val == _FAST_DEM_NODATA:
                return 1e6
            zs.append(val)
        except (StopIteration, IndexError):
            return 1e6

    if len(zs) < 5:
        return 1e6
    dzdx = (zs[1] - zs[3]) / 2
    dzdy = (zs[2] - zs[4]) / 2
    return dzdx * dzdx + dzdy * dzdy

def _fast_geom_pairs_slice_x(x, start, end):
    ge = 0.0
    dists = _FAST_TARGET_DISTS or []
    azs = _FAST_TARGET_AZS or []
    for i, j in _FAST_PAIR_IDX[start:end]:
        lat_i, lon_i = x[2 * i], x[2 * i + 1]
        lat_j, lon_j = x[2 * j], x[2 * j + 1]
        az1, _, dist = _FAST_GEOD.inv(lon_i, lat_i, lon_j, lat_j)
        if dists:
            ge += min((dist - t) ** 2 for t in dists)
        if azs:
            ge += min(((az1 % 360) - t) ** 2 for t in azs)
    return ge

def _fast_triangles_slice_x(x, start, end):
    tr = 0.0
    for i, j, k in _FAST_TRIPLE_IDX[start:end]:
        lat_i, lon_i = x[2 * i], x[2 * i + 1]
        lat_j, lon_j = x[2 * j], x[2 * j + 1]
        lat_k, lon_k = x[2 * k], x[2 * k + 1]

        _, _, dij = _FAST_GEOD.inv(lon_i, lat_i, lon_j, lat_j)
        _, _, djk = _FAST_GEOD.inv(lon_j, lat_j, lon_k, lat_k)
        _, _, dki = _FAST_GEOD.inv(lon_k, lat_k, lon_i, lat_i)
        ds = sorted((dij, djk, dki))
        if ds[0] <= 0:
            continue

        r_ds1 = ds[1] / ds[0]
        r_ds2 = ds[2] / ds[0]
        for r1, r2 in _FAST_TRI_RATIOS:
            tr += (r_ds1 - r1) ** 2 + (r_ds2 - r2) ** 2
    return tr

def _fast_alt_slice_x(x, start, end):
    al = 0.0
    for idx in _FAST_ALT_IDX[start:end]:
        lat = x[2 * idx]
        lon = x[2 * idx + 1]
        al += _fast_sample_slope(lat, lon)
    return al

def objective_function(x, names, pts, targets, tris, dem_ds, w, all_pairs, pair_slices, all_triples, triple_slices, alt_slices, executor):
    for i, n in enumerate(names):
        pts[n]['lat'], pts[n]['lon'] = x[2*i], x[2*i+1]
    ge, tr, al = 0.0, 0.0, 0.0
    
    if all_pairs:
        if executor is not None and pair_slices:
            futs = [executor.submit(_fast_geom_pairs_slice_x, x, s, e) for s, e in pair_slices]
            ge = sum(f.result() for f in futs)
        else:
            ge = _geom_pairs_slice(all_pairs, pts, targets, 0, len(all_pairs))
    
    if tris and all_triples:
        if executor is not None and triple_slices:
            futs = [executor.submit(_fast_triangles_slice_x, x, s, e) for s, e in triple_slices]
            tr = sum(f.result() for f in futs)
        else:
            tr = _triangles_slice(all_triples, pts, tris, 0, len(all_triples))

    if w.get('alt', 0.0):
        if executor is not None and alt_slices:
            futs = [executor.submit(_fast_alt_slice_x, x, s, e) for s, e in alt_slices]
            al = sum(f.result() for f in futs)
        else:
            for n in names:
                p = pts[n]
                if p['opt_alt']:
                    al += sample_slope(dem_ds, p['lat'], p['lon'])
            
    return w['geom'] * ge + w['tri'] * tr + w['alt'] * al

# ----------------
# Main Routine
# ----------------
def main():
    class _HelpFormatter(argparse.RawTextHelpFormatter):
        def _get_help_string(self, action):
            help_text = action.help or ""
            if "%(default)" in help_text:
                return help_text
            if action.required:
                return help_text
            if action.default is None or action.default is argparse.SUPPRESS:
                return help_text
            return help_text + " (default: %(default)s)"

    p = argparse.ArgumentParser(
        description="Optimize ('jiggle') KML point coordinates to satisfy geometric and DEM-based criteria.",
        epilog=(
            "Notes:\n"
            "  - KML coordinates are assumed to be WGS84 lon/lat degrees (EPSG:4326).\n"
            "  - targets.csv: az values are decimal degrees; dist values are in --dist-unit.\n"
            "  - tarcrits.csv: currently only Type='s' (side ratios) is used.\n"
            "\n"
            "Examples:\n"
            "  pj in.kml data/targets.csv data/tarcrits.csv --dem data/dem.tif out.kml\n"
            "  pj in.kml data/targets.csv data/tarcrits.csv --dem data/dem.tif out.kml --fast\n"
            "  pj in.kml data/targets.csv data/tarcrits.csv --dem data/dem.tif out.kml --workers 8\n"
        ),
        formatter_class=_HelpFormatter,
    )
    p.add_argument('in_kml', metavar='IN_KML', help='Input KML containing <Placemark> points to optimize.')
    p.add_argument('targs', metavar='TARGETS_CSV', help='Targets CSV (az/dist criteria).')
    p.add_argument('tcrit', metavar='TARCRITS_CSV', help="Triangle criteria CSV (currently only Type='s' side ratios are used).")
    p.add_argument('--dem', required=True, metavar='DEM_TIF',
                   help='DEM GeoTIFF path (required; used for altitude term when points have Altitude jiggle: True).')
    p.add_argument('out_kml', metavar='OUT_KML', help='Output KML path for optimized points.')
    p.add_argument('--dist-unit', choices=sorted(_DIST_UNIT_TO_METERS.keys()), default='miles',
                   help='Units for dist targets in TARGETS_CSV (converted internally to meters).')
    p.add_argument('--geom-weight', type=float, default=1.0,
                   help='Weight for geometric (az/dist) mismatch penalty.')
    p.add_argument('--tri-weight', type=float, default=1.0,
                   help='Weight for triangle side-ratio penalty from TARCRITS_CSV.')
    p.add_argument('--alt-weight', type=float, default=1.0,
                   help='Weight for altitude term (currently: slope/flatness penalty sampled from the DEM).')
    p.add_argument('--max-it', type=int, default=100,
                   help='Maximum optimizer iterations for L-BFGS-B.')
    p.add_argument('--fast', action='store_true',
                   help='Use all CPU cores where supported (equivalent to --workers 0).')
    p.add_argument('--workers', type=int, default=1,
                   help='Worker processes for objective evaluation (geodesic + triangle + DEM terms; 0=all cores; 1=single-process).')
    args = p.parse_args()

    pts = parse_kml_v3(args.in_kml)
    tgs = load_targets(args.targs, dist_unit=args.dist_unit)
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

    # Enforce per-point jiggle bounds to prevent the optimization from collapsing into a clump.
    # The objective includes scale-free terms (azimuth-only targets and triangle side ratios),
    # so without bounds or other regularization, points can drift and/or shrink toward zero scale.
    move_miles_by_rating = {1: 0.5, 2: 0.2, 3: 0.05, 4: 0.01, 5: 0.0}
    bounds = []
    for n in names:
        pnt = pts[n]
        rating = int(pnt.get('rating', 4) or 4)
        move_miles = move_miles_by_rating.get(rating, move_miles_by_rating[4])
        move_m = _dist_to_meters(move_miles, 'miles')
        lon0, lat0 = pnt['lon0'], pnt['lat0']
        if move_m <= 0:
            bounds.extend([(lat0, lat0), (lon0, lon0)])
            continue

        lon_e, lat_e, _ = GEOD.fwd(lon0, lat0, 90, move_m)
        lon_w, lat_w, _ = GEOD.fwd(lon0, lat0, 270, move_m)
        lon_n, lat_n, _ = GEOD.fwd(lon0, lat0, 0, move_m)
        lon_s, lat_s, _ = GEOD.fwd(lon0, lat0, 180, move_m)

        lat_min, lat_max = sorted((lat_s, lat_n))
        lon_min, lon_max = sorted((lon_w, lon_e))
        bounds.extend([(lat_min, lat_max), (lon_min, lon_max)])

    tri_side = []
    for g in tcs:
        if g.get('type') == 's' and g.get('ABC') and g['ABC'][0] != 0:
            a, b, c = g['ABC']
            tri_side.append({'ratios': (b / a, c / a)})

    all_pairs = list(itertools.combinations(names, 2))
    all_triples = list(itertools.combinations(names, 3)) if tri_side else []

    pairs_idx = list(itertools.combinations(range(len(names)), 2))
    triples_idx = list(itertools.combinations(range(len(names)), 3)) if tri_side else []
    alt_idx = [i for i, n in enumerate(names) if bool(pts[n].get('opt_alt'))]

    executor = None
    pair_slices = []
    triple_slices = []
    alt_slices = []
    workers = 0 if bool(args.fast) else int(args.workers)
    if workers != 1:
        max_workers = None if workers == 0 else workers
        cpu = os.cpu_count() or 1
        slice_workers = cpu if max_workers is None else max_workers
        executor = ProcessPoolExecutor(
            max_workers=max_workers,
            initializer=_fast_init,
            initargs=(GEOD_A, GEOD_RF, pairs_idx, triples_idx, tgs.get('dists'), tgs.get('azs'), [g['ratios'] for g in tri_side], alt_idx, args.dem),
        )
        pair_slices = _make_slices(len(pairs_idx), slice_workers)
        triple_slices = _make_slices(len(triples_idx), slice_workers)
        alt_slices = _make_slices(len(alt_idx), slice_workers)

    try:
        res = minimize(objective_function, x0,
                     args=(names, pts, tgs, tri_side, dem_ds, w, all_pairs, pair_slices, all_triples, triple_slices, alt_slices, executor),
                     bounds=bounds,
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
