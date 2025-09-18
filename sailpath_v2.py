# .\sailpath_v2.py
#
# python sailpath_v2.py --input-csv data/diagonal_stones.csv --output-kml data/diagonal_stones.kml > data/diagonal_sailpaths.csv

import argparse
import csv
import sys
from dataclasses import dataclass, field
from typing import Iterable, List, Tuple, Optional, Dict
from pyproj import Geod
import xml.etree.ElementTree as ET
import os
import math

# ------------------------------------------------------------
# Core path computation
# ------------------------------------------------------------
def sail_to_target(lat0: float, lon0: float, lat1: float, lon1: float,
                   step_dist: float, ellipsoid: str = 'WGS84') -> List[Tuple[float, float, float]]:
    """
    Walk from (lat0,lon0) to (lat1,lon1) by fixed steps along the current azimuth.
    Returns list of (lat, lon, az_used_at_point). The final endpoint is included once.
    """
    geod = Geod(ellps=ellipsoid)
    path: List[Tuple[float, float, float]] = []
    cur_lat, cur_lon = lat0, lon0

    while True:
        fwd_az, _, dist = geod.inv(cur_lon, cur_lat, lon1, lat1)
        if dist <= step_dist:
            path.append((lat1, lon1, float(fwd_az)))
            break
        path.append((cur_lat, cur_lon, float(fwd_az)))
        next_lon, next_lat, _ = geod.fwd(cur_lon, cur_lat, fwd_az, step_dist)
        cur_lat, cur_lon = next_lat, next_lon

    return path

# ------------------------------------------------------------
# Data structures
# ------------------------------------------------------------
@dataclass
class StepRec:
    lat: float
    lon: float
    az: float
    step: int
    dist_to_target_m: float
    bin_name: str = ""      # filled later

@dataclass
class Leg:
    from_loc: str
    to_loc: str
    target_lat: float
    target_lon: float
    points: List[StepRec] = field(default_factory=list)

    @property
    def folder_name(self) -> str:
        return f"{self.from_loc}\u2192{self.to_loc}"  # "S→N"

# ------------------------------------------------------------
# CSV helpers
# ------------------------------------------------------------
def _as_float(field_name: str, s: str) -> float:
    try:
        return float(s)
    except Exception as e:
        raise ValueError(f"{field_name}='{s}' is not a float") from e

def _print_path_rows(path: Iterable[Tuple[float, float, float]],
                     from_loc: str, to_loc: str, label_fmt: str = "{from_loc} {to_loc} {step}",
                     start_step: int = 0) -> None:
    """Emits rows (no Step column). Label holds the step number."""
    for i, (lat, lon, az) in enumerate(path):
        label = label_fmt.format(from_loc=from_loc, to_loc=to_loc, step=start_step + i)
        print(f"{label}, {lat:.9f}, {lon:.9f}, {az:.4f}")

def _collect_leg(path: List[Tuple[float, float, float]],
                 from_loc: str, to_loc: str,
                 target_lat: float, target_lon: float,
                 geod: Geod,
                 bin_func, is_forward: bool) -> Leg:
    leg = Leg(from_loc=from_loc, to_loc=to_loc, target_lat=target_lat, target_lon=target_lon)
    for i, (lat, lon, az) in enumerate(path):
        _, _, dist = geod.inv(lon, lat, target_lon, target_lat)
        leg.points.append(StepRec(
            lat=lat, lon=lon, az=float(az), step=i,
            dist_to_target_m=dist,
            bin_name=bin_func(float(az), is_forward)
        ))
    return leg

# ------------------------------------------------------------
# Color & icons
# ------------------------------------------------------------
def _rgb_to_kml_abgr_hex(r: int, g: int, b: int, a: int = 255) -> str:
    """KML wants aabbggrr."""
    r = max(0, min(255, r))
    g = max(0, min(255, g))
    b = max(0, min(255, b))
    a = max(0, min(255, a))
    return f"{a:02x}{b:02x}{g:02x}{r:02x}"

def _hsv_to_rgb(h: float, s: float, v: float) -> Tuple[int, int, int]:
    h = h % 360.0
    c = v * s
    x = c * (1 - abs(((h / 60.0) % 2) - 1))
    m = v - c
    if 0 <= h < 60:
        rp, gp, bp = c, x, 0
    elif 60 <= h < 120:
        rp, gp, bp = x, c, 0
    elif 120 <= h < 180:
        rp, gp, bp = 0, c, x
    elif 180 <= h < 240:
        rp, gp, bp = 0, x, c
    elif 240 <= h < 300:
        rp, gp, bp = x, 0, c
    else:
        rp, gp, bp = c, 0, x
    r = int(round((rp + m) * 255))
    g = int(round((gp + m) * 255))
    b = int(round((bp + m) * 255))
    return r, g, b

# Custom 12-bin scheme with two-name diagonals and 'oob' fallback
# Centers (deg) and names:
#  - Cardinals: SN@0, WE@90, NS@180, EW@270  (±15° window)
#  - Diagonals (±15° window around centers), with forward/reverse name pairs:
#      45:  WN / SE
#      135: NE / WS
#      225: NW / ES
#      315: EN / SW
def _make_bin12(cardinal_halfwidth_deg: float = 15.0):
    cw = float(cardinal_halfwidth_deg)

    def within(center: float, az: float) -> bool:
        lo = (center - cw) % 360.0
        hi = (center + cw) % 360.0
        if lo < hi:
            return lo <= az < hi
        else:  # wrap-around
            return az >= lo or az < hi

    # Diagonal centers with (forward_name, reverse_name)
    diag_centers = [
        (45.0,  ("WN", "SE")),
        (135.0, ("NE", "WS")),
        (225.0, ("NW", "ES")),
        (315.0, ("EN", "SW")),
    ]

    # Cardinal mapping: unique names per window
    cardinal_map = {
        0.0:   "SN",
        90.0:  "WE",
        180.0: "NS",
        270.0: "EW",
    }

    def bin_func(az_deg: float, is_forward_leg: bool) -> str:
        az = (az_deg % 360.0 + 360.0) % 360.0

        # Cardinals first
        for center, name in cardinal_map.items():
            if within(center, az):
                return name

        # Diagonals
        for center, (name_fwd, name_rev) in diag_centers:
            if within(center, az):
                return name_fwd if is_forward_leg else name_rev

        # Out-of-bin (explicit): pure white style later
        return "oob"

    # Assign hues to the 12 named bins (excluding 'oob')
    ordered_bins = ["SN","WE","NS","EW","WN","SE","NE","WS","NW","ES","EN","SW"]
    hue_map: Dict[str, float] = {name: (i / len(ordered_bins)) * 360.0
                                 for i, name in enumerate(ordered_bins)}

    def color_func(bin_name: str, step_idx: int, step_max: int) -> str:
        if bin_name == "oob":
            # Pure white, fully opaque
            return _rgb_to_kml_abgr_hex(255, 255, 255, a=255)
        hue = hue_map.get(bin_name, 0.0)
        sat = 0.95
        t = (step_idx / float(step_max)) if step_max > 0 else 0.0
        val = 0.95 - 0.25 * t  # subtle darken with step
        r, g, b = _hsv_to_rgb(hue, sat, val)
        return _rgb_to_kml_abgr_hex(r, g, b, a=255)

    return bin_func, color_func

# Known-good shape icons (no pushpins/paddles)
_ICON_URLS = [
    "http://maps.google.com/mapfiles/kml/shapes/star.png",
    "http://maps.google.com/mapfiles/kml/shapes/triangle.png",
    "http://maps.google.com/mapfiles/kml/shapes/cross-hairs.png",
    "http://maps.google.com/mapfiles/kml/shapes/target.png",
    "http://maps.google.com/mapfiles/kml/shapes/open-diamond.png",
    "http://maps.google.com/mapfiles/kml/shapes/shaded_dot.png",
    "http://maps.google.com/mapfiles/kml/shapes/donut.png",
    "http://maps.google.com/mapfiles/kml/shapes/square.png",
    "http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png",
    "http://maps.google.com/mapfiles/kml/shapes/placemark_square.png",
    "http://maps.google.com/mapfiles/kml/shapes/polygon.png",
    "http://maps.google.com/mapfiles/kml/paddle/ltblu-blank.png",
    "http://maps.google.com/mapfiles/kml/paddle/grn-blank.png",
    "http://maps.google.com/mapfiles/kml/paddle/wht-blank.png",
    "http://maps.google.com/mapfiles/kml/paddle/pink-blank.png",
    "http://maps.google.com/mapfiles/kml/paddle/blu-blank.png",
]

# ------------------------------------------------------------
# KML builder
# ------------------------------------------------------------
def _build_kml(legs: List[Leg], output_path: str,
               pointer_scale: float = 0.05, pointer_min_m: float = 50.0, pointer_max_m: float = 1000.0,
               ellipsoid: str = "WGS84",
               cardinal_halfwidth_deg: float = 15.0,
               draw_gridlines: bool = True) -> None:
    ns = "http://www.opengis.net/kml/2.2"
    ET.register_namespace("", ns)
    kml = ET.Element(ET.QName(ns, "kml"))
    doc = ET.SubElement(kml, ET.QName(ns, "Document"))
    geod = Geod(ellps=ellipsoid)

    bin_func, color_func = _make_bin12(cardinal_halfwidth_deg)

    # ---- Top-level gridlines folder (one line per entered LOC0→LOC1) ----
    if draw_gridlines and legs:
        grid_folder = ET.SubElement(doc, ET.QName(ns, "Folder"))
        ET.SubElement(grid_folder, ET.QName(ns, "name")).text = "gridlines"

        # Shared style: black @ 80% opacity, width 2.0
        grid_style = ET.SubElement(doc, ET.QName(ns, "Style"), {"id": "gridline_style"})
        ls = ET.SubElement(grid_style, ET.QName(ns, "LineStyle"))
        # Set gridline color: aabbggrr e.g. (aa=0xCC, 000000=black) or (aa=0xDD, ffffff=white)
        ET.SubElement(ls, ET.QName(ns, "color")).text = "ccffffff"  
        ET.SubElement(ls, ET.QName(ns, "width")).text = "2.0"

        seen = set()  # avoid duplicates (skip reverse)
        for leg in legs:
            key = (leg.from_loc, leg.to_loc)
            rkey = (leg.to_loc, leg.from_loc)
            if key in seen or rkey in seen:
                continue
            if not leg.points:
                continue
            start_lat = leg.points[0].lat
            start_lon = leg.points[0].lon
            end_lat   = leg.target_lat
            end_lon   = leg.target_lon

            pm = ET.SubElement(grid_folder, ET.QName(ns, "Placemark"))
            ET.SubElement(pm, ET.QName(ns, "name")).text = f"{leg.from_loc}\u2192{leg.to_loc} gridline"
            ET.SubElement(pm, ET.QName(ns, "styleUrl")).text = "#gridline_style"
            line = ET.SubElement(pm, ET.QName(ns, "LineString"))
            ET.SubElement(line, ET.QName(ns, "tessellate")).text = "1"
            coords = ET.SubElement(line, ET.QName(ns, "coordinates"))
            coords.text = f"{start_lon:.9f},{start_lat:.9f},0 {end_lon:.9f},{end_lat:.9f},0"

            seen.add(key)

    # ---- Per-leg folders with points + pointers ----
    for leg_idx, leg in enumerate(legs):
        if not leg.points:
            continue

        folder = ET.SubElement(doc, ET.QName(ns, "Folder"))
        ET.SubElement(folder, ET.QName(ns, "name")).text = leg.folder_name

        step_max = max(p.step for p in leg.points)

        for i, pt in enumerate(leg.points):
            # Color via bin
            color_hex = color_func(pt.bin_name, pt.step, step_max)
            icon_href = _ICON_URLS[i % len(_ICON_URLS)]

            # Unique style per point
            style_id = f"s_{leg_idx}_{i}"
            style = ET.SubElement(doc, ET.QName(ns, "Style"), {"id": style_id})

            icon_style = ET.SubElement(style, ET.QName(ns, "IconStyle"))
            ET.SubElement(icon_style, ET.QName(ns, "color")).text = color_hex
            ET.SubElement(icon_style, ET.QName(ns, "scale")).text = "0.75"
            icon = ET.SubElement(icon_style, ET.QName(ns, "Icon"))
            ET.SubElement(icon, ET.QName(ns, "href")).text = icon_href

            line_style = ET.SubElement(style, ET.QName(ns, "LineStyle"))
            ET.SubElement(line_style, ET.QName(ns, "color")).text = color_hex
            ET.SubElement(line_style, ET.QName(ns, "width")).text = "2.0"

            # Labels at 80% opacity
            label_style = ET.SubElement(style, ET.QName(ns, "LabelStyle"))
            ET.SubElement(label_style, ET.QName(ns, "scale")).text = "0.75"
            ET.SubElement(label_style, ET.QName(ns, "color")).text = _rgb_to_kml_abgr_hex(255, 255, 255, a=155)

            # Placemark (point)
            pm_point = ET.SubElement(folder, ET.QName(ns, "Placemark"))
            ET.SubElement(pm_point, ET.QName(ns, "name")).text = f"{leg.from_loc} {leg.to_loc} {pt.step}"
            ET.SubElement(pm_point, ET.QName(ns, "styleUrl")).text = f"#{style_id}"
            ext = ET.SubElement(pm_point, ET.QName(ns, "ExtendedData"))
            for k, v in {
                "bin": pt.bin_name,
                "azimuth_deg": f"{pt.az:.4f}",
                "dist_to_target_m": f"{pt.dist_to_target_m:.3f}"
            }.items():
                data = ET.SubElement(ext, ET.QName(ns, "Data"), {"name": k})
                ET.SubElement(data, ET.QName(ns, "value")).text = v

            point = ET.SubElement(pm_point, ET.QName(ns, "Point"))
            coords = ET.SubElement(point, ET.QName(ns, "coordinates"))
            coords.text = f"{pt.lon:.9f},{pt.lat:.9f},0"

            # Pointer line (skip at target)
            if pt.dist_to_target_m > 0.5:
                length_m = max(pointer_min_m, min(pointer_max_m, pt.dist_to_target_m * float(pointer_scale)))
                end_lon, end_lat, _ = geod.fwd(pt.lon, pt.lat, pt.az, length_m)

                pm_line = ET.SubElement(folder, ET.QName(ns, "Placemark"))
                ET.SubElement(pm_line, ET.QName(ns, "name")).text = f"{leg.from_loc} {leg.to_loc} {pt.step} pointer"
                ET.SubElement(pm_line, ET.QName(ns, "styleUrl")).text = f"#{style_id}"
                line = ET.SubElement(pm_line, ET.QName(ns, "LineString"))
                ET.SubElement(line, ET.QName(ns, "tessellate")).text = "1"
                coords2 = ET.SubElement(line, ET.QName(ns, "coordinates"))
                coords2.text = f"{pt.lon:.9f},{pt.lat:.9f},0 {end_lon:.9f},{end_lat:.9f},0"

    tree = ET.ElementTree(kml)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)

# ------------------------------------------------------------
# Orchestration
# ------------------------------------------------------------
def _process_one_run(loc0: str, lat0: float, lon0: float,
                     loc1: str, lat1: float, lon1: float,
                     step_dist: float, ellipsoid: str,
                     one_way: bool, label_fmt: str,
                     legs_out: Optional[List[Leg]] = None,
                     cardinal_halfwidth_deg: float = 15.0) -> None:
    geod = Geod(ellps=ellipsoid)
    bin_func, _ = _make_bin12(cardinal_halfwidth_deg)

    # Forward leg
    forward = sail_to_target(lat0, lon0, lat1, lon1, step_dist, ellipsoid=ellipsoid)
    _print_path_rows(forward, loc0, loc1, label_fmt=label_fmt)
    if legs_out is not None:
        legs_out.append(_collect_leg(forward, loc0, loc1, lat1, lon1, geod, bin_func, is_forward=True))

    # Return leg
    if not one_way:
        backward = sail_to_target(lat1, lon1, lat0, lon0, step_dist, ellipsoid=ellipsoid)
        _print_path_rows(backward, loc1, loc0, label_fmt=label_fmt)
        if legs_out is not None:
            legs_out.append(_collect_leg(backward, loc1, loc0, lat0, lon0, geod, bin_func, is_forward=False))

def _process_csv(path: str, ellipsoid: str, one_way: bool, label_fmt: str,
                 kml_out_path: Optional[str],
                 pointer_scale: float, pointer_min_m: float, pointer_max_m: float,
                 cardinal_halfwidth_deg: float,
                 draw_gridlines: bool) -> None:
    legs: List[Leg] = [] if kml_out_path else None

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = ["LOC0", "LAT0", "LON0", "LOC1", "LAT1", "LON1", "STEP_DIST"]
        missing = [h for h in required if h not in reader.fieldnames]
        if missing:
            raise ValueError(f"Input CSV is missing required header(s): {', '.join(missing)}")

        print("Label, Latitude, Longitude, Azimuth_To_Target")
        for rownum, row in enumerate(reader, start=2):
            try:
                loc0 = (row["LOC0"] or "").strip()
                loc1 = (row["LOC1"] or "").strip()
                lat0 = _as_float("LAT0", row["LAT0"])
                lon0 = _as_float("LON0", row["LON0"])
                lat1 = _as_float("LAT1", row["LAT1"])
                lon1 = _as_float("LON1", row["LON1"])
                step_dist = _as_float("STEP_DIST", row["STEP_DIST"])
            except Exception as e:
                print(f"# Skipping row {rownum}: {e}", file=sys.stderr)
                continue

            _process_one_run(loc0, lat0, lon0, loc1, lat1, lon1,
                             step_dist, ellipsoid, one_way, label_fmt,
                             legs_out=legs, cardinal_halfwidth_deg=cardinal_halfwidth_deg)

    if kml_out_path and legs:
        _build_kml(
            legs, kml_out_path, ellipsoid=ellipsoid,
            pointer_scale=pointer_scale, pointer_min_m=pointer_min_m, pointer_max_m=pointer_max_m,
            cardinal_halfwidth_deg=cardinal_halfwidth_deg,
            draw_gridlines=draw_gridlines
        )

def main():
    parser = argparse.ArgumentParser(
        description="Calculate sail paths and emit CSV (stdout) + KML (file) with custom azimuth bins."
    )
    # Batch mode
    parser.add_argument("--input-csv", help="CSV headers: LOC0,LAT0,LON0,LOC1,LAT1,LON1,STEP_DIST")

    # Single-run mode
    parser.add_argument("--loc0")
    parser.add_argument("--lat0", type=float)
    parser.add_argument("--lon0", type=float)
    parser.add_argument("--loc1")
    parser.add_argument("--lat1", type=float)
    parser.add_argument("--lon1", type=float)
    parser.add_argument("--step-dist", type=float, help="Step distance in meters.")
    parser.add_argument("--ellipsoid", default="WGS84")
    parser.add_argument("--one-way", action="store_true")
    parser.add_argument("--label-format", default="{from_loc} {to_loc} {step}",
                        help="Must include {from_loc},{to_loc},{step}.")

    # KML options
    parser.add_argument("--output-kml", default=None,
                        help="If omitted in batch mode, defaults to input CSV path with '.kml'.")
    parser.add_argument("--no-kml", action="store_true")
    parser.add_argument("--kml-pointer-scale", type=float, default=0.05)
    parser.add_argument("--kml-pointer-min", type=float, default=100.0)
    parser.add_argument("--kml-pointer-max", type=float, default=300.0)
    parser.add_argument("--kml-cardinal-halfwidth", type=float, default=15.0,
                        help="Half-width (degrees) for bins around centers (default 15°).")
    # Gridlines toggle (default ON)
    parser.add_argument("--no-gridlines", action="store_true",
                        help="Disable drawing a top-level 'gridlines' folder of LOC0→LOC1 lines.")

    args = parser.parse_args()
    want_kml = not args.no_kml

    if args.input_csv:
        kml_out = None
        if want_kml:
            if args.output_kml:
                kml_out = args.output_kml
            else:
                base, _ = os.path.splitext(args.input_csv)
                kml_out = f"{base}.kml"

        _process_csv(args.input_csv, args.ellipsoid, args.one_way, args.label_format,
                     kml_out_path=kml_out,
                     pointer_scale=args.kml_pointer_scale,
                     pointer_min_m=args.kml_pointer_min,
                     pointer_max_m=args.kml_pointer_max,
                     cardinal_halfwidth_deg=args.kml_cardinal_halfwidth,
                     draw_gridlines=(not args.no_gridlines))
        return

    # Single-run path
    required = ["lat0", "lon0", "lat1", "lon1", "step_dist"]
    missing = [r for r in required if getattr(args, r) is None]
    if missing:
        parser.error(f"Missing required args for single-run mode: {', '.join(missing)} (or provide --input-csv).")

    print("Label, Latitude, Longitude, Azimuth_To_Target")
    kml_out_single = args.output_kml if want_kml else None
    if want_kml and not kml_out_single:
        kml_out_single = "sailpath_output.kml"

    legs: List[Leg] = [] if kml_out_single else None
    _process_one_run(args.loc0 or "", args.lat0, args.lon0,
                     args.loc1 or "", args.lat1, args.lon1,
                     args.step_dist, args.ellipsoid, args.one_way, args.label_format,
                     legs_out=legs, cardinal_halfwidth_deg=args.kml_cardinal_halfwidth)

    if kml_out_single and legs:
        _build_kml(
            legs, kml_out_single, ellipsoid=args.ellipsoid,
            pointer_scale=args.kml_pointer_scale,
            pointer_min_m=args.kml_pointer_min,
            pointer_max_m=args.kml_pointer_max,
            cardinal_halfwidth_deg=args.kml_cardinal_halfwidth,
            draw_gridlines=(not args.no_gridlines)
        )

if __name__ == "__main__":
    main()
