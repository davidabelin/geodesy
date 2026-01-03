from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .._paths import out_dir
from ..kml import require_simplekml, write_kml_lines


def _require_geopandas():
    try:
        import geopandas as gpd  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "This command requires 'geopandas' (+ shapely). Install geopandas to run it."
        ) from e
    return gpd


def _require_shapely_ops():
    try:
        from shapely.geometry import GeometryCollection, LineString, MultiLineString  # type: ignore
        from shapely.ops import linemerge  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError("This command requires 'shapely'.") from e
    return GeometryCollection, LineString, MultiLineString, linemerge


def _require_pyproj():
    try:
        from pyproj import Geod  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError("This command requires 'pyproj'.") from e
    return Geod


def _try_matplotlib():
    try:
        import matplotlib.cm as cm  # type: ignore
        import matplotlib.colors as mcolors  # type: ignore
    except Exception:
        return None
    return cm, mcolors


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

    mpl = _try_matplotlib()
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


def _azimuth_deg_from_line(coords: List[Tuple[float, float]]) -> float:
    Geod = _require_pyproj()
    geod = Geod(ellps="WGS84")
    (lon0, lat0), (lon1, lat1) = coords[0], coords[-1]
    az12, _, _ = geod.inv(lon0, lat0, lon1, lat1)
    return float(az12) % 360.0


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


def _dist_ft_componentwise(
    p1: Tuple[float, float], p2: Tuple[float, float], classification: str
) -> float:
    Geod = _require_pyproj()
    geod = Geod(ellps="WGS84")
    lon1, lat1 = p1
    lon2, lat2 = p2
    if classification == "EW":
        _, _, dist_m = geod.inv(lon1, lat1, lon1, lat2)
    else:
        _, _, dist_m = geod.inv(lon1, lat1, lon2, lat1)
    return float(dist_m) * 3.28084


def run_dc_roadways_pipeline(
    *,
    geojson_path: Path,
    output_kml_path: Path,
    output_ew_csv_path: Path,
    output_ns_csv_path: Path,
    road_type: str = "ST",
    name_field: str = "ROUTENAME",
    type_field: str = "STREETTYPE",
    style_scheme: str = "dc-street-v1",
    keep_other: bool = False,
) -> None:
    gpd = _require_geopandas()
    GeometryCollection, LineString, MultiLineString, linemerge = _require_shapely_ops()
    require_simplekml()

    geojson_path = Path(geojson_path)
    output_kml_path = Path(output_kml_path)
    output_ew_csv_path = Path(output_ew_csv_path)
    output_ns_csv_path = Path(output_ns_csv_path)

    out_dir().mkdir(parents=True, exist_ok=True)
    output_kml_path.parent.mkdir(parents=True, exist_ok=True)
    output_ew_csv_path.parent.mkdir(parents=True, exist_ok=True)
    output_ns_csv_path.parent.mkdir(parents=True, exist_ok=True)

    gdf = gpd.read_file(str(geojson_path))
    if type_field not in gdf.columns or name_field not in gdf.columns:
        raise ValueError(
            f"Missing required columns: {type_field!r} and/or {name_field!r}"
        )

    gdf = gdf[gdf[type_field] == road_type].copy()

    merged_rows: List[Dict[str, object]] = []
    for name, group in gdf.groupby(name_field):
        merged = linemerge(MultiLineString(list(group.geometry)))
        if isinstance(merged, LineString):
            merged_rows.append({"original_name": name, "geometry": merged})
        elif isinstance(merged, MultiLineString):
            for i, part in enumerate(merged.geoms):
                merged_rows.append(
                    {"original_name": name, "part": int(i), "geometry": part}
                )
        elif isinstance(merged, GeometryCollection):
            i = 0
            for part in merged.geoms:
                if isinstance(part, LineString):
                    merged_rows.append(
                        {"original_name": name, "part": int(i), "geometry": part}
                    )
                    i += 1
        else:
            raise TypeError(f"Unexpected merged geometry type: {merged.geom_type!r}")

    merged_gdf = gpd.GeoDataFrame(merged_rows, geometry="geometry", crs=gdf.crs)
    merged_gdf["name"] = merged_gdf.apply(
        lambda row: f"{row['original_name']} Part-{row.get('part', 0)}",
        axis=1,
    )
    merged_gdf["centroid"] = merged_gdf.geometry.centroid
    merged_gdf["azimuth"] = merged_gdf.geometry.apply(
        lambda line: _azimuth_deg_from_line(list(line.coords))
    )
    merged_gdf["classification"] = merged_gdf["azimuth"].apply(_classify_azimuth)

    if not keep_other:
        merged_gdf = merged_gdf[merged_gdf["classification"].isin(["EW", "NS"])].copy()

    if style_scheme == "none":
        merged_gdf["kml_color"] = "ff808080"
    else:
        import re

        all_names = list(merged_gdf["original_name"].dropna().unique())
        other = sorted(
            [
                str(n)
                for n in all_names
                if n
                and not re.match(r"^(\d+)", str(n))
                and not re.match(r"^([A-Z])\s", str(n))
            ]
        )
        merged_gdf["kml_color"] = merged_gdf["original_name"].apply(
            lambda n: _kml_color_dc_street_v1(str(n), other)
        )

    _write_distances_csv(
        merged_gdf[merged_gdf["classification"] == "EW"].copy(),
        output_ew_csv_path,
    )
    _write_distances_csv(
        merged_gdf[merged_gdf["classification"] == "NS"].copy(),
        output_ns_csv_path,
    )
    _write_classified_kml(merged_gdf, output_kml_path)


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
    for quadrant, group in sorted_gdf.groupby(lambda i: sorted_gdf.loc[i, "sort_key"][0]):
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


def _write_classified_kml(gdf, output_path: Path) -> None:
    simplekml = require_simplekml()
    kml = simplekml.Kml(name="DC Roadways (classified)")
    for street_name, group in gdf.groupby("original_name"):
        folder = kml.newfolder(name=str(street_name))
        for _, row in group.iterrows():
            coords = [(float(x), float(y)) for x, y in row.geometry.coords]
            write_kml_lines(
                parent=folder,
                name=str(row["name"]),
                coords_groups=[coords],
                kml_color=str(row["kml_color"]),
                line_width=3.0,
            )
    kml.save(str(output_path))
