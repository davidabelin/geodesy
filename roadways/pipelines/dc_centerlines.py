from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .._paths import out_dir
from ..kml import require_simplekml, write_kml_lines
from ._common import require_geopandas, require_pyproj_geod, require_shapely_ops, try_matplotlib


def _normalize_streettype(value: str) -> str:
    s = str(value or "").strip().lower()
    if not s:
        return ""
    s = s.replace(".", "").replace("-", " ").strip()
    alias = {
        "avenue": "AVE",
        "ave": "AVE",
        "street": "ST",
        "st": "ST",
        "road": "RD",
        "rd": "RD",
        "place": "PL",
        "pl": "PL",
        "drive": "DR",
        "dr": "DR",
        "boulevard": "BLVD",
        "blvd": "BLVD",
        "circle": "CIR",
        "cir": "CIR",
        "court": "CT",
        "ct": "CT",
        "lane": "LN",
        "ln": "LN",
        "terrace": "TER",
        "ter": "TER",
        "parkway": "PKWY",
        "pkwy": "PKWY",
        "way": "WAY",
        "square": "SQ",
        "sq": "SQ",
        "alley": "ALY",
        "aly": "ALY",
    }
    if s in alias:
        return alias[s]
    return s.upper()


def _street_sort_key(street_name: str) -> Tuple[str, int, object]:
    import re

    quadrant_match = re.search(r"\s(NW|NE|SW|SE)$", street_name)
    quadrant = quadrant_match.group(1) if quadrant_match else "ZZ"

    num_match = re.match(r"^(\d+)", street_name)
    if num_match:
        return (quadrant, 1, int(num_match.group(1)))

    letter_match = re.match(r"^([A-Z])\s", street_name)
    if letter_match:
        letter = letter_match.group(1)
        alpha_pos = ord(letter) - ord("A")
        if letter > "J":
            alpha_pos -= 1
        return (quadrant, 2, alpha_pos)

    return (quadrant, 3, street_name)


def _kml_color_dc_street_v1(street_name: str, other_sorted: List[str]) -> str:
    import re

    mpl = try_matplotlib()
    if mpl is None:
        return "ff808080"
    cm, mcolors = mpl

    hex_color = "#808080"
    num_match = re.match(r"^(\d+)", street_name)
    if num_match:
        number = int(num_match.group(1))
        norm = mcolors.Normalize(vmin=1, vmax=60)
        cmap = cm.plasma
        hex_color = mcolors.to_hex(cmap(norm(number)))
    else:
        letter_match = re.match(r"^([A-Z])\s", street_name)
        if letter_match:
            letter = letter_match.group(1)
            norm = mcolors.Normalize(vmin=0, vmax=25)
            cmap = cm.viridis
            hex_color = mcolors.to_hex(cmap(norm(ord(letter) - ord("A"))))
        elif street_name in other_sorted:
            index = other_sorted.index(street_name)
            norm = mcolors.Normalize(vmin=0, vmax=max(1, len(other_sorted) - 1))
            cmap = cm.cividis
            hex_color = mcolors.to_hex(cmap(norm(index)))

    return "ff" + hex_color[5:7] + hex_color[3:5] + hex_color[1:3]


def _classify_azimuth(azimuth: float) -> str:
    ew_ranges = ((84, 96), (264, 276))
    ns_ranges = ((0, 6), (174, 186), (354, 360))
    for lo, hi in ew_ranges:
        if lo <= azimuth <= hi:
            return "EW"
    for lo, hi in ns_ranges:
        if lo <= azimuth <= hi:
            return "NS"
    return "Other"


def _azimuth_deg(coords_lonlat: List[Tuple[float, float]]) -> float:
    Geod = require_pyproj_geod()
    geod = Geod(ellps="WGS84")
    (lon0, lat0), (lon1, lat1) = coords_lonlat[0], coords_lonlat[-1]
    az12, _, _ = geod.inv(lon0, lat0, lon1, lat1)
    return float(az12) % 360.0


def _dist_ft_componentwise(
    p1: Tuple[float, float], p2: Tuple[float, float], classification: str
) -> float:
    Geod = require_pyproj_geod()
    geod = Geod(ellps="WGS84")
    lon1, lat1 = p1
    lon2, lat2 = p2
    if classification == "EW":
        _, _, dist_m = geod.inv(lon1, lat1, lon1, lat2)
    else:
        _, _, dist_m = geod.inv(lon1, lat1, lon2, lat1)
    return float(dist_m) * 3.28084


def run_dc_centerlines_pipeline(
    *,
    input_path: Path,
    output_kml_path: Path,
    output_ew_csv_path: Path,
    output_ns_csv_path: Path,
    roadtype: Optional[str] = "Street",
    streettype: str = "",
    style_scheme: str = "dc-street-v1",
    keep_other: bool = False,
    line_width: float = 1.0,
    snap_gap_m: float = 0.0,
    snap_gap_frac: float = 0.0,
) -> None:
    gpd = require_geopandas()
    GeometryCollection, LineString, MultiLineString, linemerge = require_shapely_ops()
    require_simplekml()

    input_path = Path(input_path)
    output_kml_path = Path(output_kml_path)
    output_ew_csv_path = Path(output_ew_csv_path)
    output_ns_csv_path = Path(output_ns_csv_path)

    out_dir().mkdir(parents=True, exist_ok=True)
    output_kml_path.parent.mkdir(parents=True, exist_ok=True)
    output_ew_csv_path.parent.mkdir(parents=True, exist_ok=True)
    output_ns_csv_path.parent.mkdir(parents=True, exist_ok=True)

    gdf = gpd.read_file(str(input_path))
    required = ["ST_NAME", "QUADRANT", "ROADTYPE"]
    missing = [c for c in required if c not in gdf.columns]
    if missing:
        raise ValueError(f"Missing required centerlines fields: {missing}")

    if isinstance(roadtype, str) and roadtype.strip() == "":
        roadtype = None

    streettype = "" if str(streettype or "").strip().lower() == "none" else streettype
    streettype = _normalize_streettype(streettype)
    if streettype and "STREETTYPE" not in gdf.columns:
        raise ValueError("Requested --streettype but input has no 'STREETTYPE' column.")

    if roadtype:
        gdf = gdf[gdf["ROADTYPE"].astype(str).str.casefold() == str(roadtype).casefold()].copy()
    if streettype:
        gdf = gdf[gdf["STREETTYPE"].astype(str).str.upper() == streettype].copy()

    if gdf.empty:
        roadtypes = (
            gpd.read_file(str(input_path), columns=["ROADTYPE"])["ROADTYPE"]
            .dropna()
            .astype(str)
            .value_counts()
            .head(10)
            .index.to_list()
        )
        streettypes = []
        try:
            streettypes = (
                gpd.read_file(str(input_path), columns=["STREETTYPE"])["STREETTYPE"]
                .dropna()
                .astype(str)
                .value_counts()
                .head(15)
                .index.to_list()
            )
        except Exception:
            pass
        raise ValueError(
            "No rows remain after filtering. "
            f"ROADTYPE examples: {roadtypes}. "
            f"STREETTYPE examples: {streettypes}. "
            "Note: 'Avenue' is typically STREETTYPE='AVE', not ROADTYPE."
        )

    quadrants = {"NW", "NE", "SW", "SE"}

    def full_name(row) -> str:
        base = str(row["ST_NAME"]).strip()
        quad = str(row["QUADRANT"]).strip() if row["QUADRANT"] is not None else ""
        if quad in quadrants:
            return f"{base} {quad}"
        return base

    gdf["FULL_NAME"] = gdf.apply(full_name, axis=1)

    def flatten_lines(geom) -> List[LineString]:
        if geom is None:
            return []
        if isinstance(geom, LineString):
            return [geom]
        if isinstance(geom, MultiLineString):
            return [g for g in geom.geoms if isinstance(g, LineString)]
        if isinstance(geom, GeometryCollection):
            out: List[LineString] = []
            for g in geom.geoms:
                out.extend(flatten_lines(g))
            return out
        return []

    def _kml_color_for_name(name: str, other_sorted: List[str], idx: int, n: int) -> str:
        from ..kml import kml_color_from_any

        spec = str(style_scheme or "dc-street-v1")
        if spec == "none":
            return "ff808080"
        if spec == "dc-street-v1":
            return _kml_color_dc_street_v1(name, other_sorted)
        if spec.startswith("solid:"):
            return kml_color_from_any(spec.split(":", 1)[1], default="ff808080")
        if spec.startswith("cmap:"):
            mpl = try_matplotlib()
            if mpl is None:
                return "ff808080"
            cm, mcolors = mpl
            cmap_name = spec.split(":", 1)[1].strip() or "viridis"
            cmap = cm.get_cmap(cmap_name)
            norm = mcolors.Normalize(vmin=0, vmax=max(1, n - 1))
            hex_color = mcolors.to_hex(cmap(norm(idx)))
            return "ff" + hex_color[5:7] + hex_color[3:5] + hex_color[1:3]
        raise ValueError(
            f"Unknown style scheme {style_scheme!r}. "
            "Use: dc-street-v1 | none | cmap:<matplotlib_cmap> | solid:<kml_hex>."
        )

    merged_rows: List[Dict[str, object]] = []
    for name, group in gdf.groupby("FULL_NAME"):
        lines: List[LineString] = []
        for geom in group.geometry:
            lines.extend(flatten_lines(geom))
        coord_seqs = [list(line.coords) for line in lines if len(line.coords) >= 2]
        if not coord_seqs:
            continue
        total_len = float(sum(LineString(coords).length for coords in coord_seqs))
        tol = max(float(snap_gap_m), float(snap_gap_frac) * total_len)
        if tol > 0:
            from shapely.geometry import Point  # type: ignore

            endpoints: List[Tuple[int, int, Point]] = []
            for i, coords in enumerate(coord_seqs):
                endpoints.append((i, 0, Point(coords[0])))
                endpoints.append((i, 1, Point(coords[-1])))

            used = set()
            connectors: List[List[Tuple[float, float]]] = []
            while True:
                best = None
                best_d = None
                for a in range(len(endpoints)):
                    ia, ea, pa = endpoints[a]
                    if (ia, ea) in used:
                        continue
                    for b in range(a + 1, len(endpoints)):
                        ib, eb, pb = endpoints[b]
                        if ia == ib or (ib, eb) in used:
                            continue
                        d = float(pa.distance(pb))
                        if d <= 0.0 or d > tol:
                            continue
                        if best_d is None or d < best_d:
                            best_d = d
                            best = (ia, ea, pa, ib, eb, pb)
                if best is None:
                    break
                ia, ea, pa, ib, eb, pb = best
                used.add((ia, ea))
                used.add((ib, eb))
                connectors.append([(float(pa.x), float(pa.y)), (float(pb.x), float(pb.y))])
            coord_seqs.extend(connectors)

        merged = linemerge(MultiLineString(coord_seqs))
        if isinstance(merged, LineString):
            merged_rows.append({"original_name": name, "geometry": merged})
        elif isinstance(merged, MultiLineString):
            for i, part in enumerate(merged.geoms):
                merged_rows.append({"original_name": name, "part": int(i), "geometry": part})
        elif isinstance(merged, GeometryCollection):
            i = 0
            for part in merged.geoms:
                if isinstance(part, LineString):
                    merged_rows.append({"original_name": name, "part": int(i), "geometry": part})
                    i += 1
        else:
            raise TypeError(f"Unexpected merged geometry type: {merged.geom_type!r}")

    merged_gdf = gpd.GeoDataFrame(merged_rows, geometry="geometry", crs=gdf.crs)
    if merged_gdf.empty:
        raise ValueError(
            "No merged road geometries were produced (all groups had empty/invalid geometry)."
        )
    merged_gdf["name"] = merged_gdf.apply(
        lambda row: f"{row['original_name']} Part-{row.get('part', 0)}",
        axis=1,
    )

    merged_ll = merged_gdf.to_crs(epsg=4326)
    centroids_ll = gpd.GeoSeries(merged_gdf.geometry.centroid, crs=merged_gdf.crs).to_crs(epsg=4326)
    merged_ll["centroid"] = centroids_ll
    merged_ll["azimuth"] = merged_ll.geometry.apply(
        lambda line: _azimuth_deg([(float(c[0]), float(c[1])) for c in line.coords])
    )
    merged_ll["classification"] = merged_ll["azimuth"].apply(_classify_azimuth)

    if not keep_other:
        merged_ll = merged_ll[merged_ll["classification"].isin(["EW", "NS"])].copy()

    import re

    all_names = [str(n) for n in merged_ll["original_name"].dropna().unique()]
    all_names_sorted = sorted(all_names)
    idx_map = {n: i for i, n in enumerate(all_names_sorted)}
    other = sorted(
        [
            n
            for n in all_names_sorted
            if n and not re.match(r"^(\d+)", n) and not re.match(r"^([A-Z])\s", n)
        ]
    )
    merged_ll["kml_color"] = merged_ll["original_name"].apply(
        lambda n: _kml_color_for_name(str(n), other, idx_map.get(str(n), 0), len(all_names_sorted))
    )

    _write_distances_csv(
        merged_ll[merged_ll["classification"] == "EW"].copy(),
        output_ew_csv_path,
    )
    _write_distances_csv(
        merged_ll[merged_ll["classification"] == "NS"].copy(),
        output_ns_csv_path,
    )
    _write_classified_kml(merged_ll, output_kml_path, line_width=float(line_width))


def _write_distances_csv(gdf, output_path: Path) -> None:
    import pandas as pd

    if gdf.empty:
        pd.DataFrame(
            columns=[
                "StreetA",
                "LatA",
                "LonA",
                "StreetB",
                "LatB",
                "LonB",
                "Distance",
            ]
        ).to_csv(output_path, index=False)
        return

    gdf["sort_key"] = gdf["original_name"].apply(lambda s: _street_sort_key(str(s)))
    sorted_gdf = gdf.sort_values(by="sort_key").reset_index(drop=True)

    classification = str(sorted_gdf.iloc[0]["classification"])
    records: List[Dict[str, object]] = []
    for _, group in sorted_gdf.groupby(lambda i: sorted_gdf.loc[i, "sort_key"][0]):
        if len(group) < 2:
            continue
        group = group.reset_index(drop=True)
        for i in range(len(group) - 1):
            a = group.iloc[i]
            b = group.iloc[i + 1]
            p1 = a["centroid"]
            p2 = b["centroid"]
            dist_ft = _dist_ft_componentwise((p1.x, p1.y), (p2.x, p2.y), classification)
            records.append(
                {
                    "StreetA": a["original_name"],
                    "LatA": float(p1.y),
                    "LonA": float(p1.x),
                    "StreetB": b["original_name"],
                    "LatB": float(p2.y),
                    "LonB": float(p2.x),
                    "Distance": round(dist_ft, 2),
                }
            )

    pd.DataFrame.from_records(records).to_csv(output_path, index=False)


def _write_classified_kml(gdf, output_path: Path, line_width: float) -> None:
    simplekml = require_simplekml()
    kml = simplekml.Kml(name="DC Street Centerlines (classified)")
    for street_name, group in gdf.groupby("original_name"):
        folder = kml.newfolder(name=str(street_name))
        for _, row in group.iterrows():
            coords = [(float(c[0]), float(c[1])) for c in row.geometry.coords]
            write_kml_lines(
                parent=folder,
                name=str(row["name"]),
                coords_groups=[coords],
                kml_color=str(row["kml_color"]),
                line_width=float(line_width),
            )
    kml.save(str(output_path))
