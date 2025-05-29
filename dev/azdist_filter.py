"""
azdist_filter.py — FINAL, ALL-IN-ONE, FULLY ANNOTATED, REASON COLUMN, FLEXIBLE TENTH/HALF/WHOLE

- --tenth, --half, --whole apply to both Az and Dist unless --az-only or --dist-only specified.
- 'Reason' column shows which criteria/field was matched.
- Keeps all # comments.
"""

import pandas as pd
import numpy as np
import argparse
import math
from typing import List, Callable

# === Constants ===
PHI = (1 + 5 ** 0.5) / 2
PI = math.pi
SQRT2 = math.sqrt(2)
SQRT3 = math.sqrt(3)
TOL = 0.001

def load_pairs(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df['Az'] = pd.to_numeric(df['Az'], errors='coerce')
    df['Dist'] = pd.to_numeric(df['Dist'], errors='coerce')
    return df

def to_float_list(seq):
    if seq is None:
        return []
    out = []
    for x in seq:
        try:
            if x is not None and str(x).strip() != '':
                out.append(float(x))
        except Exception:
            continue
    return out

def within_tol(val: float, targets: list, tol: float) -> bool:
    return any(abs(val - float(t)) <= tol for t in targets)

def is_multiple(val: float, base: float, tol: float) -> bool:
    base = float(base)
    if base == 0: return False
    rem = val % base
    return min(rem, abs(base - rem)) <= tol

def is_factor(val: float, target: float, tol: float) -> bool:
    target = float(target)
    if val == 0:
        return False
    rem = target % val
    return min(rem, abs(val - rem)) <= tol

def mask_tenth(series, tol=TOL):
    decimals = np.round((series - np.floor(series)) * 10)
    return (np.abs(series - np.round(series * 10) / 10) <= tol) & (decimals == 1)

def mask_half(series, tol=TOL):
    decimals = np.round((series - np.floor(series)) * 10)
    return (np.abs(series - np.round(series * 2) / 2) <= tol) & (decimals == 5)

def mask_whole(series, tol=TOL):
    decimals = np.round((series - np.floor(series)) * 10)
    return (np.abs(series - np.round(series)) <= tol) & (decimals == 0)

def filter_pairs(
    df: pd.DataFrame,
    az_targets: list = None,
    az_tol: float = TOL,
    dist_targets: list = None,
    dist_tol: float = TOL,
    az_multiples: list = None,
    dist_multiples: list = None,
    factor_targets: list = None,
    factor_tol: float = TOL,
    tenth: bool = False,
    half: bool = False,
    whole: bool = False,
    az_only: bool = False,
    dist_only: bool = False,
    custom_funcs: list = []
) -> pd.DataFrame:
    """
    Flexible filtering: tenths/halves/wholes apply to both Az and Dist unless only one is requested.
    Reason column records all matches for each field.
    """
    N = len(df)
    reasons = [[] for _ in range(N)]
    keep = pd.Series([False]*N, index=df.index)

    # Azimuth close to a special value?
    if az_targets and len(az_targets) > 0:
        for i, x in enumerate(df['Az']):
            for t in az_targets:
                if abs(x - t) <= az_tol:
                    keep.iat[i] = True
                    reasons[i].append(f"Az target:{t}")
    # Distance close to a special value?
    if dist_targets and len(dist_targets) > 0:
        for i, x in enumerate(df['Dist']):
            for t in dist_targets:
                if abs(x - t) <= dist_tol:
                    keep.iat[i] = True
                    reasons[i].append(f"Dist target:{t}")
    # Azimuth is a multiple of some special value?
    if az_multiples and len(az_multiples) > 0:
        for m in az_multiples:
            for i, x in enumerate(df['Az']):
                if is_multiple(x, m, az_tol):
                    keep.iat[i] = True
                    reasons[i].append(f"Az multiple:{m}")
    # Distance is a multiple of some special value?
    if dist_multiples and len(dist_multiples) > 0:
        for m in dist_multiples:
            for i, x in enumerate(df['Dist']):
                if is_multiple(x, m, dist_tol):
                    keep.iat[i] = True
                    reasons[i].append(f"Dist multiple:{m}")
    # Distance is a factor of some special value?
    if factor_targets and len(factor_targets) > 0:
        for tgt in factor_targets:
            for i, x in enumerate(df['Dist']):
                if is_factor(x, tgt, factor_tol):
                    keep.iat[i] = True
                    reasons[i].append(f"Dist factor:{tgt}")
    # Tenths, halves, wholes filters (now test both Az and Dist by default)
    test_az = not dist_only
    test_dist = not az_only

    if tenth:
        if test_az:
            mask = mask_tenth(df['Az'], az_tol)
            for i, m in enumerate(mask):
                if m:
                    keep.iat[i] = True
                    reasons[i].append("Az Tenth")
        if test_dist:
            mask = mask_tenth(df['Dist'], dist_tol)
            for i, m in enumerate(mask):
                if m:
                    keep.iat[i] = True
                    reasons[i].append("Dist Tenth")
    if half:
        if test_az:
            mask = mask_half(df['Az'], az_tol)
            for i, m in enumerate(mask):
                if m:
                    keep.iat[i] = True
                    reasons[i].append("Az Half")
        if test_dist:
            mask = mask_half(df['Dist'], dist_tol)
            for i, m in enumerate(mask):
                if m:
                    keep.iat[i] = True
                    reasons[i].append("Dist Half")
    if whole:
        if test_az:
            mask = mask_whole(df['Az'], az_tol)
            for i, m in enumerate(mask):
                if m:
                    keep.iat[i] = True
                    reasons[i].append("Az Whole")
        if test_dist:
            mask = mask_whole(df['Dist'], dist_tol)
            for i, m in enumerate(mask):
                if m:
                    keep.iat[i] = True
                    reasons[i].append("Dist Whole")
    # User-supplied custom functions (advanced usage)
    for func in custom_funcs:
        mask = df.apply(func, axis=1)
        for i, m in enumerate(mask):
            if m:
                keep.iat[i] = True
                reasons[i].append("Custom")
    # Only keep if reason(s) exist
    filtered = df[keep].copy()
    filtered["Reason"] = [", ".join(r) if r else "" for r, k in zip(reasons, keep) if k]
    return filtered

def main():
    parser = argparse.ArgumentParser(description="Filter azimuth/distance pairs by modular criteria.")
    parser.add_argument("csv_path", help="CSV file of point pairs with Az, Dist columns")
    parser.add_argument("--out", help="CSV file for filtered pairs", default=None)
    parser.add_argument("--az-targets", nargs='*', type=float, default=None, help="Azimuths of interest (deg)")
    parser.add_argument("--az-tol", type=float, default=TOL, help="Tolerance for azimuth match [default: 0.001]")
    parser.add_argument("--dist-targets", nargs='*', type=float, default=None, help="Distances of interest (miles)")
    parser.add_argument("--dist-tol", type=float, default=TOL, help="Tolerance for distance match [default: 0.001]")
    parser.add_argument("--az-multiples", nargs='*', type=float, default=None, help="Azimuth multiples to match")
    parser.add_argument("--dist-multiples", nargs='*', type=float, default=None, help="Distance multiples to match")
    parser.add_argument("--factor-targets", nargs='*', type=float, default=None, help="Distance is a factor of each TARGET (within tolerance)")
    parser.add_argument("--factor-tol", type=float, default=TOL, help="Tolerance for --factor-targets checks [default: 0.001]")
    parser.add_argument("--tenth", action='store_true', help="Filter for distances or azimuths ending in .1 (exclusive)")
    parser.add_argument("--half", action='store_true', help="Filter for distances or azimuths ending in .5 (exclusive)")
    parser.add_argument("--whole", action='store_true', help="Filter for distances or azimuths ending in .0 (exclusive)")
    parser.add_argument("--az-only", action='store_true', help="Restrict --tenth/--half/--whole to Az only")
    parser.add_argument("--dist-only", action='store_true', help="Restrict --tenth/--half/--whole to Dist only")
    args = parser.parse_args()
    df = load_pairs(args.csv_path)
    filtered = filter_pairs(
        df,
        az_targets=to_float_list(args.az_targets),
        az_tol=args.az_tol,
        dist_targets=to_float_list(args.dist_targets),
        dist_tol=args.dist_tol,
        az_multiples=to_float_list(args.az_multiples),
        dist_multiples=to_float_list(args.dist_multiples),
        factor_targets=to_float_list(args.factor_targets),
        factor_tol=args.factor_tol,
        tenth=args.tenth,
        half=args.half,
        whole=args.whole,
        az_only=args.az_only,
        dist_only=args.dist_only
    )
    print(f"Selected {len(filtered)} out of {len(df)} pairs.")
    if args.out:
        filtered.to_csv(args.out, index=False)
        print(f"Filtered pairs written to {args.out}")
    else:
        print(filtered)

if __name__ == "__main__":
    main()
