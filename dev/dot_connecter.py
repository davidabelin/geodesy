#!/usr/bin/env python3
"""
Dot Connecter — select/draw lines and export full azimuth/distance metrics for *all* points → every other point,
with optional spherical or ellipsoidal calculations, and optional CSV output.
"""
import argparse
import json
import csv
import xml.etree.ElementTree as ET
from itertools import combinations
import geometry
import simplekml

# ——— 1) Parse KML to dict name → (lat, lon) ———
def load_points_from_kml(path):
    ns = {'kml': 'http://www.opengis.net/kml/2.2'}
    tree = ET.parse(path)
    root = tree.getroot()
    pts = {}
    for pm in root.findall('.//kml:Placemark', ns):
        name = pm.findtext('kml:name', default='', namespaces=ns).strip()
        coord_text = pm.findtext('kml:Point/kml:coordinates', default='', namespaces=ns).strip()
        if not name or not coord_text:
            continue
        lon, lat, *_ = coord_text.split(',')
        pts[name] = (float(lat), float(lon))
    return pts

# ——— 2) Compute full metrics for every point → every other point ———
def compute_point_metrics(points, ellipsoid=True):
    metrics = {name: {'lat': lat, 'lon': lon, 'others': {}}
               for name, (lat, lon) in points.items()}
    for a, b in combinations(points, 2):
        lat1, lon1 = points[a]
        lat2, lon2 = points[b]
        az_ab, az_ba, dist = geometry.inverse_geodetic(
            lat1, lon1, lat2, lon2,
            unit='miles', ellipsoid=ellipsoid
        )
        metrics[a]['others'][b] = {'az': az_ab % 360, 'dist': dist}
        metrics[b]['others'][a] = {'az': az_ba % 360, 'dist': dist}
    return metrics

# ——— 3) Compute unique pairs for filtering/drawing ———
def compute_metrics(points, ellipsoid=True):
    pairs = []
    for a, b in combinations(points, 2):
        lat1, lon1 = points[a]
        lat2, lon2 = points[b]
        az1, az2, dist = geometry.inverse_geodetic(
            lat1, lon1, lat2, lon2,
            unit='miles', ellipsoid=ellipsoid
        )
        pairs.append({
            'p1': a, 'p2': b,
            'az': az1 % 360, 'baz': az2 % 360,
            'dist': dist
        })
    return pairs

# ——— 4) Filter pairs by tolerance rules ———
def filter_pairs(pairs, args):
    keep = []
    for pi in pairs:
        d, az = pi['dist'], pi['az']
        ok = False
        if args.tenth and abs(d - round(d, 1)) <= args.dist_tol:
            ok = True
        if args.whole and abs(az - round(az)) <= args.az_tol:
            ok = True
        if args.half and abs(az - round(az * 2) / 2) <= args.az_tol:
            ok = True
        if not (args.tenth or args.whole or args.half):
            ok = True
        if ok:
            keep.append(pi)
    return keep

# ——— 5) Draw selected lines to KML ———
def draw_lines(pairs, points, out_path):
    kml = simplekml.Kml()
    palette = ['FF0000FF','FF00FF00','FFFF0000','FFFFFF00','FF00FFFF','FFFF00FF']
    style_map = {}
    for pi in pairs:
        grp = pi['p1']
        if grp not in style_map:
            style_map[grp] = palette[len(style_map) % len(palette)]
        line = kml.newlinestring(name=f"{grp}→{pi['p2']}")
        lat1, lon1 = points[grp]
        lat2, lon2 = points[pi['p2']]
        line.coords = [(lon1, lat1), (lon2, lat2)]
        line.style.linestyle.color = style_map[grp]
        line.style.linestyle.width = 1.5
    kml.save(out_path)
    print(f"Wrote KML with {len(pairs)} lines to {out_path}.")

# ——— CLI glue ———
if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Export full azimuth/distance metrics and draw filtered lines in KML."
    )
    parser.add_argument('input_kml', help='Input placemarks KML')
    parser.add_argument('output_kml', help='Output lines KML')
    parser.add_argument(
        '--metrics-json', help='Write full metrics to JSON file'
    )
    parser.add_argument(
        '--metrics-geojson', help='Write full metrics as GeoJSON'
    )
    parser.add_argument(
        '--metrics-csv', help='Write full metrics to CSV file (RefPnt,Pnt,Az,Dist)'
    )
    parser.add_argument(
        '--dist-tol', type=float, default=0.0001,
        help='Distance tolerance (miles)'
    )
    parser.add_argument(
        '--az-tol', type=float, default=0.005,
        help='Azimuth tolerance (degrees)'
    )
    parser.add_argument(
        '--tenth', action='store_true',
        help='Filter pairs with distance ≈ 0.1 mile'
    )
    parser.add_argument(
        '--whole', action='store_true',
        help='Filter pairs with azimuth ≈ whole degree'
    )
    parser.add_argument(
        '--half', action='store_true',
        help='Filter pairs with azimuth ≈ half degree'
    )
    parser.add_argument(
        '--spherical', action='store_true',
        help='Use spherical (not ellipsoidal) calculations'
    )
    args = parser.parse_args()

    # Load and prepare
    pts = load_points_from_kml(args.input_kml)
    ellipsoid = not args.spherical

    # 1) Full metrics for every point → every other point
    full_metrics = compute_point_metrics(pts, ellipsoid=ellipsoid)

    # 2) Dump JSON if requested
    if args.metrics_json:
        with open(args.metrics_json, 'w') as f:
            json.dump(full_metrics, f, indent=2)
        print(f"Wrote full metrics JSON: {args.metrics_json}")

    # 3) Dump GeoJSON if requested
    if args.metrics_geojson:
        features = [
            {
                'type': 'Feature',
                'geometry': {
                    'type': 'Point',
                    'coordinates': [info['lon'], info['lat']]
                },
                'properties': {
                    'name': name,
                    'others': info['others']
                }
            }
            for name, info in full_metrics.items()
        ]
        geo = {'type': 'FeatureCollection', 'features': features}
        with open(args.metrics_geojson, 'w') as f:
            json.dump(geo, f, indent=2)
        print(f"Wrote full metrics GeoJSON: {args.metrics_geojson}")

    # 4) Dump CSV if requested
    if args.metrics_csv:
        with open(args.metrics_csv, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['RefPnt', 'Pnt', 'Az', 'Dist'])
            for ref, info in full_metrics.items():
                for other, met in info['others'].items():
                    writer.writerow([ref, other, met['az'], met['dist']])
        print(f"Wrote full metrics CSV: {args.metrics_csv}")

    # 5) Original filter & draw pipeline
    pairs = compute_metrics(pts, ellipsoid=ellipsoid)
    selected = filter_pairs(pairs, args)
    draw_lines(selected, pts, args.output_kml)
