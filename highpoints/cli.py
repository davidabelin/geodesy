from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from ._paths import out_dir
from .pipeline import run_hidrant_peaks


def _resolve_out_path(path: Path) -> Path:
    return path if path.is_absolute() else (out_dir() / path)


def _cmd_hidrant(args: argparse.Namespace) -> int:
    out_kml = _resolve_out_path(Path(args.output_kml))
    out_csv = _resolve_out_path(Path(args.output_csv)) if args.output_csv else None
    out_kml.parent.mkdir(parents=True, exist_ok=True)
    if out_csv:
        out_csv.parent.mkdir(parents=True, exist_ok=True)

    run_hidrant_peaks(
        dem_path=Path(args.dem),
        buffer_m=float(args.buffer_m),
        separation_m=float(args.separation_m),
        output_kml=out_kml,
        output_csv=out_csv,
    )
    print(out_kml)
    if out_csv:
        print(out_csv)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m highpoints", description="Highpoints utilities.")
    sub = p.add_subparsers(dest="cmd", required=True)

    hp = sub.add_parser("hidrant-peaks", help="Find top peaks per hidrant sector and export.")
    hp.add_argument("--dem", required=True, help="DEM GeoTIFF path.")
    hp.add_argument("--buffer-m", type=float, default=1000.0, help="Buffer distance in meters.")
    hp.add_argument("--separation-m", type=float, default=100.0, help="Minimum separation distance in meters.")
    hp.add_argument(
        "--output-kml",
        default="hidrant_peaks.kml",
        help="Output KML path (relative goes under highpoints/out/).",
    )
    hp.add_argument(
        "--output-csv",
        default=None,
        help="Optional output CSV path (relative goes under highpoints/out/).",
    )
    hp.set_defaults(func=_cmd_hidrant)

    return p


def main(argv: Sequence[str] | None = None) -> int:
    p = build_parser()
    ns = p.parse_args(list(argv) if argv is not None else None)
    try:
        return int(ns.func(ns))
    except (ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

