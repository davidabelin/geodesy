#!/usr/bin/env python3

import argparse
import csv
import sys
from typing import Iterable, List, Tuple, Optional
from pyproj import Geod


def sail_to_target(lat0: float, lon0: float, lat1: float, lon1: float,
                   step_dist: float, ellipsoid: str = 'WGS84') -> List[Tuple[float, float, Optional[float]]]:
    """
    Calculates a path from a starting point to a target point by repeatedly
    calculating the azimuth to the target and moving a fixed distance.

    Returns a list of (lat, lon, fwd_azimuth_used_for_this_step).
    The endpoint itself is included exactly once as the final row of the forward leg,
    and we do NOT add an extra 'TARGET' marker row.
    """
    geod = Geod(ellps=ellipsoid)
    path: List[Tuple[float, float, Optional[float]]] = []

    current_lat, current_lon = lat0, lon0

    while True:
        # Azimuth and distance from current point to the target
        fwd_az, back_az, dist_to_target = geod.inv(current_lon, current_lat, lon1, lat1)

        if dist_to_target <= step_dist:
            # Final hop: land exactly on the endpoint; record that endpoint once.
            # We keep fwd_az as the azimuth used for the final hop for consistent output.
            path.append((lat1, lon1, fwd_az))
            break

        # Record current point before stepping
        path.append((current_lat, current_lon, fwd_az))

        # Step forward by step_dist toward the target
        next_lon, next_lat, _ = geod.fwd(current_lon, current_lat, fwd_az, step_dist)
        current_lat, current_lon = next_lat, next_lon

    return path


def _print_path_rows(path: Iterable[Tuple[float, float, Optional[float]]],
                     from_loc: str, to_loc: str, label_fmt: str = "{from_loc} {to_loc} {step}",
                     start_step: int = 0) -> None:
    """
    Print one leg's steps with labels like 'S N 0', 'S N 1', ...
    """
    for i, (lat, lon, az) in enumerate(path):
        label = label_fmt.format(from_loc=from_loc, to_loc=to_loc, step=start_step + i)
        # az will always be a float under current logic
        print(f"{label}, {i}, {lat:.9f}, {lon:.9f}, {az:.4f}")


def _process_one_run(loc0: str, lat0: float, lon0: float,
                     loc1: str, lat1: float, lon1: float,
                     step_dist: float, ellipsoid: str,
                     one_way: bool, label_fmt: str) -> None:
    # Forward
    forward = sail_to_target(lat0, lon0, lat1, lon1, step_dist, ellipsoid=ellipsoid)
    _print_path_rows(forward, loc0, loc1, label_fmt=label_fmt)

    # Backward (default on)
    if not one_way:
        backward = sail_to_target(lat1, lon1, lat0, lon0, step_dist, ellipsoid=ellipsoid)
        _print_path_rows(backward, loc1, loc0, label_fmt=label_fmt)


def _as_float(name: str, v: str) -> float:
    try:
        return float(v)
    except Exception:
        raise ValueError(f"Could not parse numeric value for {name!r}: {v!r}")


def _process_csv(path: str, ellipsoid: str, one_way: bool, label_fmt: str) -> None:
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        required = ["LOC0", "LAT0", "LON0", "LOC1", "LAT1", "LON1", "STEP_DIST"]
        missing = [h for h in required if h not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"Input CSV is missing required header(s): {', '.join(missing)}")

        print("Label, Step, Latitude, Longitude, Azimuth_To_Target")
        for rownum, row in enumerate(reader, start=2):  # start=2 for human ref (header is line 1)
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
                             step_dist, ellipsoid, one_way, label_fmt)


def main():
    parser = argparse.ArgumentParser(
        description="Calculate sail paths to targets; supports single args or a CSV batch.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    # Batch mode
    parser.add_argument("--input-csv", help="CSV with headers: LOC0,LAT0,LON0,LOC1,LAT1,LON1,STEP_DIST")

    # Single-run mode (backward-compatible)
    parser.add_argument("--lat0", type=float, help="Starting latitude.")
    parser.add_argument("--lon0", type=float, help="Starting longitude.")
    parser.add_argument("--lat1", type=float, help="Target latitude.")
    parser.add_argument("--lon1", type=float, help="Target longitude.")
    parser.add_argument("--step-dist", type=float, help="Step distance in meters.")

    # Shared options
    parser.add_argument("--ellipsoid", default="WGS84",
                        help="Ellipsoid for geodesic calculations (default: WGS84).")
    parser.add_argument("--one-way", action="store_true",
                        help="Run only LOC0→LOC1 (no return leg). Default is roundtrip.")
    parser.add_argument("--label-format", default="{from_loc} {to_loc} {step}",
                        help="Python format for label; keys: from_loc, to_loc, step. "
                             "Default: '{from_loc} {to_loc} {step}'")

    # Optional names for single-run labeling
    parser.add_argument("--loc0", default="A", help="Name for startpoint in single-run mode (default: A).")
    parser.add_argument("--loc1", default="B", help="Name for endpoint in single-run mode (default: B).")

    args = parser.parse_args()

    # Batch mode
    if args.input_csv:
        _process_csv(args.input_csv, args.ellipsoid, args.one_way, args.label_format)
        return

    # Single-run mode (original behavior, now with labels & optional roundtrip)
    required = ["lat0", "lon0", "lat1", "lon1", "step_dist"]
    missing = [r for r in required if getattr(args, r) is None]
    if missing:
        parser.error(f"Missing required args for single-run mode: {', '.join(missing)} "
                     f"(or provide --input-csv).")

    print("Label, Step, Latitude, Longitude, Azimuth_To_Target")
    _process_one_run(args.loc0, args.lat0, args.lon0,
                     args.loc1, args.lat1, args.lon1,
                     args.step_dist, args.ellipsoid, args.one_way, args.label_format)


if __name__ == "__main__":
    main()
