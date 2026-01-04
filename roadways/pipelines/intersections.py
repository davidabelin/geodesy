from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .._paths import out_dir
from ..kml import kml_color_from_any, require_simplekml
from ._common import require_geopandas


@dataclass(frozen=True)
class IntersectionPoint:
    a_name: str
    b_name: str
    x: float
    y: float


def _maybe_make_full_name(gdf, *, st_name_field: str, quadrant_field: str, out_field: str) -> None:
    if out_field in gdf.columns:
        return
    if st_name_field not in gdf.columns or quadrant_field not in gdf.columns:
        return
    quadrants = {"NW", "NE", "SW", "SE"}

    def full_name(row) -> str:
        base = str(row[st_name_field]).strip()
        quad = str(row[quadrant_field]).strip() if row[quadrant_field] is not None else ""
        if quad in quadrants:
            return f"{base} {quad}"
        return base

    gdf[out_field] = gdf.apply(full_name, axis=1)


def _filter_equals(gdf, field: Optional[str], value: Optional[str]):
    if not field or value is None:
        return gdf
    if field not in gdf.columns:
        raise ValueError(f"Filter field {field!r} not present in input.")
    s = gdf[field]
    want = str(value)
    if s.dtype.kind in {"O", "U", "S"}:
        return gdf[s.astype(str).str.casefold() == want.casefold()].copy()
    return gdf[s == value].copy()


def _choose_work_crs(a, b) -> Optional[object]:
    if a.crs is not None and b.crs is not None:
        return a.crs
    return "EPSG:3857"


def _extract_points(geom) -> List[Tuple[float, float]]:
    if geom is None or geom.is_empty:
        return []
    gt = geom.geom_type
    if gt == "Point":
        return [(float(geom.x), float(geom.y))]
    if gt == "MultiPoint":
        return [(float(p.x), float(p.y)) for p in geom.geoms]
    if gt == "GeometryCollection":
        pts: List[Tuple[float, float]] = []
        for g in geom.geoms:
            pts.extend(_extract_points(g))
        return pts
    return []


def _dedupe_points(
    points: Iterable[IntersectionPoint],
    *,
    grid_size: float,
) -> List[IntersectionPoint]:
    if grid_size <= 0:
        return list(points)
    seen = set()
    out: List[IntersectionPoint] = []
    for p in points:
        k = (
            p.a_name,
            p.b_name,
            int(round(p.x / grid_size)),
            int(round(p.y / grid_size)),
        )
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
    return out


def run_intersections(
    *,
    a_input: Path,
    b_input: Optional[Path],
    a_layer: Optional[str],
    b_layer: Optional[str],
    a_field: Optional[str],
    a_value: Optional[str],
    b_field: Optional[str],
    b_value: Optional[str],
    a_name_field: Optional[str],
    b_name_field: Optional[str],
    derive_full_name: bool,
    full_name_field: str,
    group_by: Optional[str],
    output_geojson: Path,
    output_csv: Optional[Path],
    output_kml: Optional[Path],
    kml_color: str,
    dedupe_grid_m: float,
) -> None:
    gpd = require_geopandas()
    a_input = Path(a_input)
    b_input = Path(b_input) if b_input is not None else a_input

    a_read_kwargs = {}
    if a_layer:
        a_read_kwargs["layer"] = a_layer
    b_read_kwargs = {}
    if b_layer:
        b_read_kwargs["layer"] = b_layer

    a = gpd.read_file(str(a_input), **a_read_kwargs)
    b = gpd.read_file(str(b_input), **b_read_kwargs)

    if derive_full_name:
        _maybe_make_full_name(a, st_name_field="ST_NAME", quadrant_field="QUADRANT", out_field=full_name_field)
        _maybe_make_full_name(b, st_name_field="ST_NAME", quadrant_field="QUADRANT", out_field=full_name_field)

    a = _filter_equals(a, a_field, a_value)
    b = _filter_equals(b, b_field, b_value)

    if a.empty or b.empty:
        raise ValueError("No rows remain after filtering for one or both inputs.")

    if group_by:
        if group_by not in a.columns:
            raise ValueError(f"--group-by {group_by!r} not present in A input.")
        if group_by not in b.columns:
            raise ValueError(f"--group-by {group_by!r} not present in B input.")
        a = a.dissolve(by=group_by).reset_index()
        b = b.dissolve(by=group_by).reset_index()

    a_name_field_eff = a_name_field or (group_by if group_by else None) or full_name_field
    b_name_field_eff = b_name_field or (group_by if group_by else None) or full_name_field
    if a_name_field_eff not in a.columns:
        a_name_field_eff = a.columns[0]
    if b_name_field_eff not in b.columns:
        b_name_field_eff = b.columns[0]

    work_crs = _choose_work_crs(a, b)
    if work_crs is not None:
        try:
            a_work = a.to_crs(work_crs)
        except Exception:
            a_work = a
        try:
            b_work = b.to_crs(a_work.crs)
        except Exception:
            b_work = b
    else:
        a_work, b_work = a, b

    a_geom = a_work[[a_name_field_eff, "geometry"]].copy()
    b_geom = b_work[[b_name_field_eff, "geometry"]].copy()
    joined = gpd.sjoin(
        b_geom,
        a_geom,
        how="inner",
        predicate="intersects",
    )
    b_idx = joined.index.to_numpy()
    a_idx = joined["index_right"].to_numpy()

    same_source = a_input.resolve() == b_input.resolve()
    same_filters = (
        same_source
        and (a_field or None) == (b_field or None)
        and (a_value or None) == (b_value or None)
    )

    points: List[IntersectionPoint] = []
    for a_i, b_i in zip(a_idx.tolist(), b_idx.tolist()):
        if same_filters:
            if a_i == b_i:
                continue
            if a_i > b_i:
                continue
        a_geom = a_work.geometry.iloc[a_i]
        b_geom = b_work.geometry.iloc[b_i]
        inter = a_geom.intersection(b_geom)
        for x, y in _extract_points(inter):
            points.append(
                IntersectionPoint(
                    a_name=str(a_work.iloc[a_i][a_name_field_eff]),
                    b_name=str(b_work.iloc[b_i][b_name_field_eff]),
                    x=float(x),
                    y=float(y),
                )
            )

    points = _dedupe_points(points, grid_size=float(dedupe_grid_m))
    if not points:
        raise ValueError("No point intersections found (note: overlaps are ignored).")

    out_dir().mkdir(parents=True, exist_ok=True)
    output_geojson = Path(output_geojson)
    output_geojson.parent.mkdir(parents=True, exist_ok=True)

    out_gdf = gpd.GeoDataFrame(
        [{"a": p.a_name, "b": p.b_name} for p in points],
        geometry=gpd.points_from_xy([p.x for p in points], [p.y for p in points]),
        crs=a_work.crs,
    )
    out_ll = out_gdf.to_crs(epsg=4326) if out_gdf.crs else out_gdf
    out_ll.to_file(output_geojson, driver="GeoJSON")

    if output_csv is not None:
        output_csv = Path(output_csv)
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        df = out_ll.copy()
        df["lon"] = df.geometry.x
        df["lat"] = df.geometry.y
        df.drop(columns=["geometry"]).to_csv(output_csv, index=False)

    if output_kml is not None:
        simplekml = require_simplekml()
        output_kml = Path(output_kml)
        output_kml.parent.mkdir(parents=True, exist_ok=True)
        kml = simplekml.Kml(name="Intersections")
        color = kml_color_from_any(kml_color, default="ff00ffff")
        style = simplekml.Style()
        style.iconstyle.color = color
        for _, row in out_ll.iterrows():
            pm = kml.newpoint(name=f"{row['a']} x {row['b']}")
            pm.coords = [(float(row.geometry.x), float(row.geometry.y))]
            pm.style = style
        kml.save(str(output_kml))
