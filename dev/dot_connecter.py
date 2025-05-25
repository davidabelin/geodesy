#!/usr/bin/env python3
import argparse
import xml.etree.ElementTree as ET
from itertools import combinations
from pyproj import Geod
import simplekml

# ——— 1) KML → {name: (lat,lon)} ———
def load_points_from_kml(path):
    ns = {'kml': 'http://www.opengis.net/kml/2.2'}
    tree = ET.parse(path)
    root = tree.getroot()
    pts = {}
    for pm in root.findall('.//kml:Placemark', ns):
        # pass namespaces=ns here, so 'kml:' is defined
        name = pm.findtext('kml:name', default='', namespaces=ns).strip()
        coord_text = pm.findtext(
            'kml:Point/kml:coordinates',
            default='',
            namespaces=ns
        ).strip()
        if not name or not coord_text:
            continue
        lon, lat, *_ = coord_text.split(',')
        pts[name] = (float(lat), float(lon))
    return pts


# ——— 2) Compute all pairwise azimuth & distance ———
def compute_metrics(points, ellipsoid=True):
    geod = Geod(ellps='WGS84')
    pairs = []
    for a, b in combinations(points.keys(), 2):
        lat1, lon1 = points[a]
        lat2, lon2 = points[b]
        if ellipsoid:
            az1, az2, dist_m = geod.inv(lon1, lat1, lon2, lat2)
            dist = dist_m / 1609.344         # → miles
        else:
            # fallback to spherical law‑of‑cosines if you really want
            raise NotImplementedError
        pairs.append({
            'p1': a, 'p2': b,
            'az': az1 % 360, 'baz': az2 % 360,
            'dist': dist
        })
    return pairs

# ——— 3) Filter by tolerance rules ———
def filter_pairs(pairs, args):
    keep = []
    for pi in pairs:
        d, az = pi['dist'], pi['az']
        ok = False
        # distance near a tenth‑mile?
        if args.tenth and abs(d - round(d,1)) <= args.dist_tol:
            ok = True
        # azimuth near whole‑degree?
        if args.whole and abs(az - round(az)) <= args.az_tol:
            ok = True
        # azimuth near half‑degree?
        if args.half and abs(az - round(az*2)/2) <= args.az_tol:
            ok = True
        # if no filters specified, keep all
        if not (args.tenth or args.whole or args.half):
            ok = True
        if ok:
            keep.append(pi)
    return keep

# ——— 4) Draw to KML ———
def draw_lines(pairs, points, out_path):
    kml = simplekml.Kml()
    # simple color‐by‐first‐point palette
    palette = ['FF0000FF','FF00FF00','FFFF0000','FFFFFF00','FF00FFFF','FFFF00FF']
    style_map = {}
    for i, pi in enumerate(pairs):
        grp = pi['p1']
        if grp not in style_map:
            style_map[grp] = palette[len(style_map) % len(palette)]
        pnt = kml.newlinestring(name=f"{pi['p1']}→{pi['p2']}")
        lat1, lon1 = points[pi['p1']]
        lat2, lon2 = points[pi['p2']]
        pnt.coords = [(lon1, lat1), (lon2, lat2)]
        pnt.style.linestyle.color = style_map[grp]
        pnt.style.linestyle.width = 1.5
    kml.save(out_path)
    print(f"Wrote {out_path} with {len(pairs)} lines.")

# ——— CLI glue ———
if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="Select and draw 'interesting' lines between points in a KML.")
    p.add_argument("input_kml", help="Your exported Google Earth placemarks.kml")
    p.add_argument("output_kml", help="Where to write the new lines‑KML")
    p.add_argument("--dist-tol", type=float, default=0.005,
                   help="Miles tolerance for distance‑based filtering")
    p.add_argument("--az-tol",   type=float, default=0.01,
                   help="Degrees tolerance for azimuth‑based filtering")
    p.add_argument("--tenth",    action="store_true",
                   help="Keep pairs whose distance is within tol of an exact 0.1 mile")
    p.add_argument("--whole",    action="store_true",
                   help="Keep pairs whose azimuth is within tol of a whole degree")
    p.add_argument("--half",     action="store_true",
                   help="Keep pairs whose azimuth is within tol of a half degree")
    args = p.parse_args()

    pts  = load_points_from_kml(args.input_kml)
    allp = compute_metrics(pts, ellipsoid=True)
    sel  = filter_pairs(allp, args)
    draw_lines(sel, pts, args.output_kml)
