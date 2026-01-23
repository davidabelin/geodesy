"""
KML drawing utilities for the `spirals/` workspace.

Primary entrypoints:
- Connect CSV points into line strings (grouped by LOC prefix).
- Draw a Fibonacci square spiral as KML squares, quarter-arcs, or both.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

try:
    import simplekml
except ImportError as ie:  # pragma: no cover
    simplekml = None
    _simplekml_import_error = ie
else:
    _simplekml_import_error = None

try:
    from pyproj import Geod
except ImportError:  # pragma: no cover
    Geod = None

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = REPO_ROOT / "data" / "refpnts.csv"
DEFAULT_OUT_DIR = REPO_ROOT / "spirals" / "out"

EARTH_RADIUS_M = 6_371_008.8


def _require_simplekml() -> None:
    if simplekml is None:  # pragma: no cover
        raise SystemExit(
            f"Missing dependency: simplekml ({_simplekml_import_error}).\n"
            "Install with: pip install simplekml"
        )


def _parse_alpha(value: str) -> int:
    v = value.strip().lower()
    if v.startswith("0x"):
        v = v[2:]
        n = int(v, 16)
    elif re.fullmatch(r"[0-9a-f]{1,2}", v):
        n = int(v, 16)
    else:
        n = int(v, 10)
    if not (0 <= n <= 255):
        raise argparse.ArgumentTypeError("alpha must be between 0 and 255")
    return n


def _kml_color_from_rgb(rgb_hex: str, alpha: int) -> str:
    rgb = rgb_hex.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", rgb):
        raise ValueError(f"Invalid RGB hex color: {rgb_hex!r}")
    rr = int(rgb[0:2], 16)
    gg = int(rgb[2:4], 16)
    bb = int(rgb[4:6], 16)
    return f"{alpha:02x}{bb:02x}{gg:02x}{rr:02x}".lower()


def generate_kml_from_csv(
    csv_filepath: str,
    kml_filepath: str,
    include_points: bool = False,
    *,
    line_alpha: int = 0xAA,
    point_alpha: int = 0xFF,
    line_width: float = 1.0,
) -> None:
    """
    Reads a CSV file with point data (LOC, LAT, LON) and generates a KML file
    with lines connecting sequences of points.

    Points are grouped by the non-numeric prefix of their LOC (Vertex ID) value.
    Within each group, points are connected in ascending order of their numeric suffix.
    Individual points can be optionally included in the KML.
    """
    # Args:
    #   csv_filepath (str): Path to the input CSV file.
    #   kml_filepath (str): Path to the output KML file.
    #   include_points (bool): If True, individual points will be added to the KML.
    _require_simplekml()

    # Palette is specified in standard RGB; converted to KML AABBGGRR when used.
    color_palette_rgb = [
        "0000ff",  # blue
        "00ff00",  # green
        "ff0000",  # red
        "00ffff",  # cyan
        "ff00ff",  # magenta
        "aaaa00",  # dark yellow
        "ffa500",  # orange
        "500050",  # dark purple
        "a52a2a",  # brown
        "32cd32",  # lime
        "dda0dd",  # plum
        "005050",  # dark teal
    ]
    line_color_palette = [_kml_color_from_rgb(c, line_alpha) for c in color_palette_rgb]
    icon_color_palette = [_kml_color_from_rgb(c, point_alpha) for c in color_palette_rgb]

    group_to_color_index = {}
    next_color_idx = 0
    grouped_points_data = defaultdict(list)

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
                vertex_id = row.get('LOC')
                lat_str = row.get('LAT')
                lon_str = row.get('LON')

                if not all([vertex_id, lat_str, lon_str]):
                    print(f"Warning: Skipping row {i+2} in CSV due to missing Loc, Lat, or Lon data: {row}")
                    continue
                
                try:
                    # Ensure Lat and Lon are stripped of potential whitespace before conversion
                    lat = float(lat_str.strip())
                    lon = float(lon_str.strip())
                except ValueError:
                    print(f"Warning: Skipping row {i+2} (ID: {vertex_id}) due to invalid lat/lon: Lat='{lat_str}', Lon='{lon_str}'")
                    continue

                # Try to parse vertex_id into prefix and numeric suffix using a hierarchy of rules:
                # 1. "ANYTHING-NUMBER" -> prefix=ANYTHING, suffix=NUMBER
                # 2. "ANYTHINGNUMBER" (ends with digits) -> prefix=ANYTHING, suffix=NUMBER (ANYTHING can be empty)
                # 3. "ANYTHINGELSE" (no dash, no trailing digits) -> prefix=ANYTHINGELSE, suffix=sequential (0, 1, ...)
                
                prefix = None
                num_suffix = None

                # Rule 1: Try dash-based parsing (e.g., "GROUP-1", "TEST-SUBGROUP-007")
                dash_match = re.match(r'(.*)-(\d+)$', vertex_id)
                if dash_match:
                    prefix = dash_match.group(1)
                    num_suffix = int(dash_match.group(2))
                else:
                    # Rule 2: Try trailing digits parsing (e.g., "ANS0", "cSAN10", "123")
                    trailing_digits_match = re.match(r'(.*?)(\d+)$', vertex_id)
                    if trailing_digits_match:
                        prefix = trailing_digits_match.group(1) # Can be empty if vertex_id is all digits
                        num_suffix = int(trailing_digits_match.group(2))
                    elif vertex_id: # Rule 3: Fallback for non-empty vertex_id (e.g., "SiteA", "NorthMarker")
                        prefix = vertex_id
                        # Assign sequential suffix (0, 1, 2,...) for this prefix
                        num_suffix = len(grouped_points_data[prefix])
                    # else: vertex_id is empty or unparseable by any rule, will be caught below

                if prefix is not None and num_suffix is not None:
                    grouped_points_data[prefix].append({'lon': lon, 'lat': lat, 'num': num_suffix, 'id': vertex_id})
                else:
                    print(f"Warning: Could not parse vertex ID '{vertex_id}' into a known prefix/suffix structure. Skipping.")

    except FileNotFoundError:
        print(f"Error: The file {csv_filepath} was not found.")
        return
    except Exception as e:
        print(f"An unexpected error occurred while reading {csv_filepath}: {e}")
        return

    # Create a KML object
    kml = simplekml.Kml()
    kml.document.name = f"Lines for points in {os.path.basename(csv_filepath)}"

    # For each group, create a Folder, Point Placemarks, and a LineString
    for prefix, points_list in grouped_points_data.items():
        if not points_list:
            continue

        # Assign a color to this group if not already assigned
        if prefix not in group_to_color_index:
            group_to_color_index[prefix] = next_color_idx
            next_color_idx = (next_color_idx + 1) % len(line_color_palette)
        
        current_color_idx = group_to_color_index[prefix]
        current_line_color = line_color_palette[current_color_idx]
        current_icon_color = icon_color_palette[current_color_idx]

        # Sort points by their numeric suffix to ensure correct line order
        sorted_points = sorted(points_list, key=lambda p: p['num'])

        # Create a folder for this group
        group_folder = kml.newfolder(name=f"Group {prefix}")
        # Optionally, create Point Placemarks for each point in the group
        if include_points:
            points_subfolder = group_folder.newfolder(name=f"{prefix} Points")
            for p_data in sorted_points:
                kml_point = points_subfolder.newpoint(name=p_data['id'])
                kml_point.coords = [(p_data['lon'], p_data['lat'])]
                kml_point.altitudemode = simplekml.AltitudeMode.clamptoground
                
                # Add ExtendedData
                kml_point.extendeddata.newdata('Loc', value=p_data['id'])
                kml_point.extendeddata.newdata('Lat', value=str(p_data['lat']))
                kml_point.extendeddata.newdata('Lon', value=str(p_data['lon']))
                kml_point.extendeddata.newdata('Group', value=prefix)
                
                # Style the point icon
                kml_point.style.iconstyle.color = current_icon_color
                kml_point.style.iconstyle.icon.href = 'http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png'

        # Prepare coordinates for the LineString (lon, lat, alt)
        # Altitude is set to 0 (ground level) by default in simplekml if not specified for LineString
        coordinates = [(p['lon'], p['lat']) for p in sorted_points] # simplekml adds altitude=0 by default

        if len(coordinates) >= 2:  # A LineString needs at least two points
            linestring = group_folder.newlinestring(name=f"{prefix} Sequence")
            linestring.coords = coordinates
            linestring.altitudemode = simplekml.AltitudeMode.clamptoground
            linestring.extrude = 0 # Do not extrude to ground if points have altitude (not the case here but good practice)
            
            # Style the line
            linestring.style.linestyle.color = current_line_color # Hopefully 85% *opaque* color
            linestring.style.linestyle.width = float(line_width)
        # No special handling for len(coordinates) == 1 for lines, as points are created individually above.

    # Save the KML file
    try:
        Path(kml_filepath).parent.mkdir(parents=True, exist_ok=True)
        kml.save(kml_filepath)
        print(f"KML file successfully generated: {kml_filepath}")
    except NameError as ne:
        if 'simplekml' in str(ne):
            print("Error: The 'simplekml' library is not installed or imported correctly.")
            print("Please install it using: pip install simplekml")
        else:
            print(f"An unexpected NameError occurred: {ne}")
    except Exception as e:
        print(f"An error occurred while saving the KML file {kml_filepath}: {e}")


def _read_points_csv(csv_path: str) -> List[Tuple[str, float, float]]:
    points: List[Tuple[str, float, float]] = []
    with open(csv_path, mode="r", newline="", encoding="utf-8") as infile:
        reader = csv.DictReader(infile)
        if not reader.fieldnames or not all(f in reader.fieldnames for f in ["LOC", "LAT", "LON"]):
            raise ValueError(f"CSV file {csv_path} must have 'LOC', 'LAT', 'LON' columns (found {reader.fieldnames})")
        for row_num, row in enumerate(reader, start=2):
            loc = (row.get("LOC") or "").strip()
            lat_s = (row.get("LAT") or "").strip()
            lon_s = (row.get("LON") or "").strip()
            if not (loc and lat_s and lon_s):
                raise ValueError(f"Row {row_num} is missing LOC/LAT/LON: {row}")
            try:
                lat = float(lat_s)
                lon = float(lon_s)
            except ValueError as e:
                raise ValueError(f"Row {row_num} has invalid LAT/LON: {row}") from e
            points.append((loc, lat, lon))
    return points


@dataclass(frozen=True)
class Square:
    x: float
    y: float
    size: float
    direction: int  # 0=E,1=N,2=W,3=S (placement direction for this square)


def _fibonacci_sizes(n: int, base_size: float) -> List[float]:
    if n <= 0:
        return []
    if n == 1:
        return [base_size]
    sizes = [base_size, base_size]
    for _ in range(2, n):
        sizes.append(sizes[-1] + sizes[-2])
    return sizes


def _build_fib_squares(n: int, base_size: float, ccw: bool) -> List[Square]:
    if n <= 0:
        return []

    fib_sizes = _fibonacci_sizes(n, base_size)

    squares: List[Square] = []
    dir_idx = 0  # square0 direction
    minx = miny = 0.0
    maxx = maxy = fib_sizes[0]
    squares.append(Square(x=0.0, y=0.0, size=fib_sizes[0], direction=dir_idx))

    step = 1 if ccw else -1
    for i in range(1, n):
        dir_idx = (dir_idx + step) % 4
        s = fib_sizes[i]
        if dir_idx == 0:  # E
            x = maxx
            y = miny
            maxx += s
        elif dir_idx == 1:  # N
            x = minx
            y = maxy
            maxy += s
        elif dir_idx == 2:  # W
            x = minx - s
            y = miny
            minx -= s
        else:  # S
            x = minx
            y = miny - s
            miny -= s
        squares.append(Square(x=x, y=y, size=s, direction=dir_idx))

    return squares


def _arc_center_and_angles(square: Square, ccw: bool) -> Tuple[Tuple[float, float], float, float]:
    def minor_arc_ccw(a: float, b: float) -> Tuple[float, float]:
        a = a % 360.0
        b = b % 360.0
        return (b, a) if (b - a) % 360.0 > 180.0 else (a, b)

    x, y, s, d = square.x, square.y, square.size, square.direction
    if d == 0:  # E
        cx, cy = x + s, y  # lower-right
        th1, th2 = minor_arc_ccw(180.0, 90.0)
    elif d == 1:  # N
        cx, cy = x, y  # lower-left
        th1, th2 = minor_arc_ccw(90.0, 0.0)
    elif d == 2:  # W
        cx, cy = x, y + s  # upper-left
        th1, th2 = minor_arc_ccw(360.0, 270.0)
    else:  # S
        cx, cy = x + s, y + s  # upper-right
        th1, th2 = minor_arc_ccw(270.0, 180.0)

    if not ccw:
        th1, th2 = th2, th1
    return (cx, cy), th1, th2


def _rotate_xy(x: float, y: float, heading_deg: float) -> Tuple[float, float]:
    if heading_deg == 0.0:
        return x, y
    a = math.radians(heading_deg)
    ca = math.cos(a)
    sa = math.sin(a)
    # Rotate clockwise by heading_deg
    return (x * ca + y * sa, -x * sa + y * ca)


def _meters_per_unit(unit: str) -> float:
    u = unit.strip().lower()
    if u in ("m", "meter", "meters"):
        return 1.0
    if u in ("km", "kilometer", "kilometers"):
        return 1000.0
    if u in ("ft", "foot", "feet"):
        return 0.3048
    if u in ("mi", "mile", "miles"):
        return 1609.344
    raise ValueError(f"Unsupported unit: {unit!r} (use m, km, ft, mi)")


def _geodesic_fwd(lat: float, lon: float, az_deg: float, dist_m: float, ellipsoid: str) -> Tuple[float, float]:
    if Geod is not None and ellipsoid.strip().lower() != "sphere":
        g = Geod(ellps=ellipsoid.strip().upper())
        lon2, lat2, _ = g.fwd(lon, lat, az_deg, dist_m)
        return float(lat2), float(lon2)

    # Spherical fallback
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    az = math.radians(az_deg)
    sigma = dist_m / EARTH_RADIUS_M
    lat2 = math.asin(math.sin(lat1) * math.cos(sigma) + math.cos(lat1) * math.sin(sigma) * math.cos(az))
    lon2 = lon1 + math.atan2(
        math.sin(az) * math.sin(sigma) * math.cos(lat1),
        math.cos(sigma) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), ((math.degrees(lon2) + 540.0) % 360.0) - 180.0


def _offset_to_latlon(lat0: float, lon0: float, x_m: float, y_m: float, ellipsoid: str) -> Tuple[float, float]:
    dist_m = math.hypot(x_m, y_m)
    if dist_m == 0:
        return lat0, lon0
    bearing = (math.degrees(math.atan2(x_m, y_m)) + 360.0) % 360.0
    return _geodesic_fwd(lat0, lon0, bearing, dist_m, ellipsoid=ellipsoid)


def _sample_arc_xy(
    center: Tuple[float, float],
    radius: float,
    theta1: float,
    theta2: float,
    points: int,
) -> List[Tuple[float, float]]:
    if points < 4:
        raise ValueError("arc sample points must be >= 4")
    t1 = theta1 % 360.0
    t2 = theta2 % 360.0
    if t2 < t1:
        t2 += 360.0
    out: List[Tuple[float, float]] = []
    cx, cy = center
    for i in range(points + 1):
        t = math.radians(t1 + (t2 - t1) * (i / points))
        out.append((cx + radius * math.cos(t), cy + radius * math.sin(t)))
    return out


def _add_fib_spiral_to_folder(
    folder: "simplekml.Folder",
    *,
    lat: float,
    lon: float,
    terms: int = 10,
    base: float = 100.0,
    units: str = "m",
    ccw: bool = True,
    heading: float = 0.0,
    draw: str = "both",  # squares|arcs|both
    arc_points: int = 32,
    ellipsoid: str = "WGS84",
    name: str = "Fibonacci Spiral",
    include_anchor: bool = True,
) -> None:
    meters = _meters_per_unit(units) * base
    squares = _build_fib_squares(terms, base_size=meters, ccw=ccw)

    root_folder = folder.newfolder(name=name)
    if include_anchor:
        p = root_folder.newpoint(name=f"{name} anchor")
        p.coords = [(lon, lat)]
        p.altitudemode = simplekml.AltitudeMode.clamptoground
        p.style.iconstyle.icon.href = "http://maps.google.com/mapfiles/kml/paddle/wht-circle.png"
        p.style.iconstyle.scale = 0.8

    squares_folder = root_folder.newfolder(name="Squares")
    arcs_folder = root_folder.newfolder(name="Quarter Arcs")

    square_fill = [
        ("#f9c2c2", "#c27a7a"),
        ("#f9e0a8", "#c29a43"),
        ("#b3e6b3", "#5ba65b"),
        ("#a7c6ff", "#4a70c2"),
        ("#c2f1ff", "#5da3b5"),
        ("#e0c2ff", "#8f5ab8"),
    ]

    if draw in ("squares", "both"):
        for i, sq in enumerate(squares):
            light, dark = square_fill[i % len(square_fill)]
            corners_xy = [
                (sq.x, sq.y),
                (sq.x + sq.size, sq.y),
                (sq.x + sq.size, sq.y + sq.size),
                (sq.x, sq.y + sq.size),
                (sq.x, sq.y),
            ]
            coords_ll: List[Tuple[float, float]] = []
            for x, y in corners_xy:
                xr, yr = _rotate_xy(x, y, heading)
                plat, plon = _offset_to_latlon(lat, lon, xr, yr, ellipsoid=ellipsoid)
                coords_ll.append((plon, plat))

            poly = squares_folder.newpolygon(name=f"Square {i + 1} (size={sq.size / meters:.0f}×base)")
            poly.outerboundaryis.coords = coords_ll
            poly.altitudemode = simplekml.AltitudeMode.clamptoground
            poly.style.polystyle.color = _kml_color_from_rgb(light, 0x55)
            poly.style.linestyle.color = _kml_color_from_rgb(dark, 0xCC)
            poly.style.linestyle.width = 1.5

    if draw in ("arcs", "both"):
        for i, sq in enumerate(squares):
            _, dark = square_fill[i % len(square_fill)]
            center, th1, th2 = _arc_center_and_angles(sq, ccw=ccw)
            arc_xy = _sample_arc_xy(center, radius=sq.size, theta1=th1, theta2=th2, points=arc_points)
            arc_coords: List[Tuple[float, float]] = []
            for x, y in arc_xy:
                xr, yr = _rotate_xy(x, y, heading)
                plat, plon = _offset_to_latlon(lat, lon, xr, yr, ellipsoid=ellipsoid)
                arc_coords.append((plon, plat))

            ls = arcs_folder.newlinestring(name=f"Arc {i + 1}")
            ls.coords = arc_coords
            ls.altitudemode = simplekml.AltitudeMode.clamptoground
            ls.style.linestyle.color = _kml_color_from_rgb(dark, 0xFF)
            ls.style.linestyle.width = 2.5


def generate_fib_spiral_kml(
    kml_path: str,
    *,
    lat: float,
    lon: float,
    terms: int = 10,
    base: float = 100.0,
    units: str = "m",
    ccw: bool = True,
    heading: float = 0.0,
    draw: str = "both",  # squares|arcs|both
    arc_points: int = 32,
    ellipsoid: str = "WGS84",
) -> None:
    _require_simplekml()

    kml = simplekml.Kml()
    kml.document.name = f"Fibonacci Spiral ({terms} terms)"
    _add_fib_spiral_to_folder(
        kml,
        lat=lat,
        lon=lon,
        terms=terms,
        base=base,
        units=units,
        ccw=ccw,
        heading=heading,
        draw=draw,
        arc_points=arc_points,
        ellipsoid=ellipsoid,
        name="Fibonacci Spiral",
        include_anchor=True,
    )

    Path(kml_path).parent.mkdir(parents=True, exist_ok=True)
    kml.save(kml_path)
    print(f"KML file successfully generated: {kml_path}")


def generate_fib_spirals_from_csv(
    csv_path: str,
    kml_path: str,
    *,
    terms: int = 10,
    base: float = 100.0,
    units: str = "m",
    ccw: bool = True,
    heading: float = 0.0,
    draw: str = "both",
    arc_points: int = 32,
    ellipsoid: str = "WGS84",
    include_anchor: bool = True,
) -> None:
    _require_simplekml()

    points = _read_points_csv(csv_path)
    kml = simplekml.Kml()
    kml.document.name = f"Spirals from {os.path.basename(csv_path)}"

    root = kml.newfolder(name="Spirals")
    for loc, lat, lon in points:
        _add_fib_spiral_to_folder(
            root,
            lat=lat,
            lon=lon,
            terms=terms,
            base=base,
            units=units,
            ccw=ccw,
            heading=heading,
            draw=draw,
            arc_points=arc_points,
            ellipsoid=ellipsoid,
            name=str(loc),
            include_anchor=include_anchor,
        )

    Path(kml_path).parent.mkdir(parents=True, exist_ok=True)
    kml.save(kml_path)
    print(f"KML file successfully generated: {kml_path}")


if __name__ == "__main__":
    default_csv = str(DEFAULT_CSV)
    default_csv_out = str(DEFAULT_CSV.with_name("output_spirals.kml"))
    default_fib_out = str(DEFAULT_OUT_DIR / "fib_spiral.kml")
    default_fibcsv_out = str(DEFAULT_OUT_DIR / "fib_spirals_from_csv.kml")

    p = argparse.ArgumentParser(description="KML utilities (CSV connector + spiral drawer)")
    sub = p.add_subparsers(dest="cmd")

    c = sub.add_parser("connect", help="Connect (lat,lon) dots imported from a CSV file")
    c.add_argument("csv_path", nargs="?", default=default_csv, help="Input CSV file path")
    c.add_argument("kml_file", nargs="?", default=default_csv_out, help="Output KML file path")
    c.add_argument("--include-points", action="store_true", help="Also write point placemarks")
    c.add_argument("--line-alpha", type=_parse_alpha, default=0xAA, help="Line opacity (0-255, hex ok)")
    c.add_argument("--point-alpha", type=_parse_alpha, default=0xFF, help="Point/icon opacity (0-255, hex ok)")
    c.add_argument("--line-width", type=float, default=1.0, help="Line width")

    f = sub.add_parser("fib", help="Draw a Fibonacci square spiral as KML squares/arcs")
    f.add_argument("--lat", type=float, required=True, help="Anchor latitude (lower-left of the first square)")
    f.add_argument("--lon", type=float, required=True, help="Anchor longitude (lower-left of the first square)")
    f.add_argument("--terms", type=int, default=10, help="How many Fibonacci squares/arcs to draw")
    f.add_argument("--base", type=float, default=100.0, help="Side length of the first square, in --units")
    f.add_argument("--units", type=str, default="m", help="Units for --base: m, km, ft, mi")
    f.add_argument("--cw", action="store_true", help="Draw clockwise instead of counter-clockwise")
    f.add_argument("--heading", type=float, default=0.0, help="Rotate spiral clockwise from North (degrees)")
    f.add_argument("--draw", choices=["squares", "arcs", "both"], default="both", help="What to draw")
    f.add_argument("--arc-points", type=int, default=32, help="Points per quarter-arc")
    f.add_argument("--ellipsoid", type=str, default="WGS84", help="WGS84, NAD83, or sphere")
    f.add_argument("--out", type=str, default=default_fib_out, help="Output KML file path")

    fc = sub.add_parser("fibcsv", help="Draw one Fibonacci spiral per row of a CSV (LOC,LAT,LON)")
    fc.add_argument("csv_path", nargs="?", default=default_csv, help="Input CSV file path")
    fc.add_argument("--terms", type=int, default=10, help="How many Fibonacci squares/arcs to draw per point")
    fc.add_argument("--base", type=float, default=100.0, help="Side length of the first square, in --units")
    fc.add_argument("--units", type=str, default="m", help="Units for --base: m, km, ft, mi")
    fc.add_argument("--cw", action="store_true", help="Draw clockwise instead of counter-clockwise")
    fc.add_argument("--heading", type=float, default=0.0, help="Rotate spiral clockwise from North (degrees)")
    fc.add_argument("--draw", choices=["squares", "arcs", "both"], default="both", help="What to draw")
    fc.add_argument("--arc-points", type=int, default=32, help="Points per quarter-arc")
    fc.add_argument("--ellipsoid", type=str, default="WGS84", help="WGS84, NAD83, or sphere")
    fc.add_argument("--no-anchor", action="store_true", help="Do not add the anchor point placemark")
    fc.add_argument("--out", type=str, default=default_fibcsv_out, help="Output KML file path")

    args = p.parse_args()

    if args.cmd is None:
        print(f"Trying defaults...\tInput: {default_csv}\tOutput: {default_fibcsv_out}")
        generate_fib_spirals_from_csv(default_csv, default_fibcsv_out)
    elif args.cmd == "connect":
        print(f"Input: {args.csv_path}\tOutput: {args.kml_file}\tInclude Points: {args.include_points}")
        generate_kml_from_csv(
            args.csv_path,
            args.kml_file,
            include_points=args.include_points,
            line_alpha=args.line_alpha,
            point_alpha=args.point_alpha,
            line_width=args.line_width,
        )
    elif args.cmd == "fibcsv":
        generate_fib_spirals_from_csv(
            args.csv_path,
            args.out,
            terms=args.terms,
            base=args.base,
            units=args.units,
            ccw=not args.cw,
            heading=args.heading,
            draw=args.draw,
            arc_points=args.arc_points,
            ellipsoid=args.ellipsoid,
            include_anchor=not args.no_anchor,
        )
    elif args.cmd == "fib":
        generate_fib_spiral_kml(
            args.out,
            lat=args.lat,
            lon=args.lon,
            terms=args.terms,
            base=args.base,
            units=args.units,
            ccw=not args.cw,
            heading=args.heading,
            draw=args.draw,
            arc_points=args.arc_points,
            ellipsoid=args.ellipsoid,
        )
    else:
        p.print_help()
