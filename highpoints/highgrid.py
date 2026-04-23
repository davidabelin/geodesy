from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from pyproj import CRS, Transformer
from rasterio.errors import WindowError
from rasterio.features import geometry_mask, geometry_window
from shapely.geometry import Point, Polygon, mapping
from shapely.ops import transform as shapely_transform

METERS_TO_FEET = 3.28084
CORNER_LABELS = ("W", "N", "E", "S")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_dem_path() -> Path:
    return repo_root() / "data" / "tif" / "dc_dem.tif"


def default_output_path() -> Path:
    return Path(__file__).resolve().parent / "out" / "highgrid.gpkg"


@dataclass(frozen=True)
class HighgridResult:
    output_path: Path
    cell_count: int
    peak_count: int
    grid_size: int


def parse_grid_size(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "grid size must be an integer from 1 to 100"
        ) from exc
    if parsed < 1 or parsed > 100:
        raise argparse.ArgumentTypeError("grid size must be an integer from 1 to 100")
    return parsed


def _coerce_crs(value: str | CRS) -> CRS:
    crs = CRS.from_user_input(value)
    if crs is None:
        raise ValueError(f"Invalid CRS: {value}")
    return crs


def _parse_corner_names(value: str | None) -> dict[str, str] | None:
    if value is None or value.strip().lower() in {"", "auto"}:
        return None
    names = [part.strip() for part in value.split(",")]
    if len(names) != 4 or any(not part for part in names):
        raise ValueError("--corner-names must be 'auto' or four comma-separated names")
    return dict(zip(CORNER_LABELS, names))


def _parse_inline_corners(corners_text: str) -> gpd.GeoDataFrame:
    parts = [part.strip() for part in corners_text.split(";") if part.strip()]
    if len(parts) != 4:
        raise ValueError("--corners must contain four lon,lat pairs in W,N,E,S order")

    records = []
    for label, part in zip(CORNER_LABELS, parts):
        values = [piece.strip() for piece in part.split(",")]
        if len(values) < 2:
            raise ValueError("--corners must contain lon,lat pairs")
        try:
            lon = float(values[0])
            lat = float(values[1])
        except ValueError as exc:
            raise ValueError("--corners must contain numeric lon,lat pairs") from exc
        records.append({"source_name": label, "geometry": Point(lon, lat)})

    return gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326")


def _candidate_name(row: pd.Series) -> str | None:
    for column in ("Name", "name", "LOC", "loc", "id", "ID"):
        if column not in row:
            continue
        value = row[column]
        if value is None or pd.isna(value):
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _geometry_points(geometry) -> Iterable[Point]:
    if geometry is None or geometry.is_empty:
        return

    geom_type = geometry.geom_type
    if geom_type == "Point":
        yield Point(geometry.x, geometry.y)
    elif geom_type == "MultiPoint":
        for item in geometry.geoms:
            yield from _geometry_points(item)
    elif geom_type in {"LineString", "LinearRing"}:
        for coord in geometry.coords:
            yield Point(coord[0], coord[1])
    elif geom_type == "Polygon":
        for coord in geometry.exterior.coords:
            yield Point(coord[0], coord[1])
    elif geom_type in {"MultiLineString", "MultiPolygon", "GeometryCollection"}:
        for item in geometry.geoms:
            yield from _geometry_points(item)


def _read_boundary_candidates(boundary_path: Path) -> gpd.GeoDataFrame:
    if not boundary_path.exists():
        raise ValueError(f"Boundary file does not exist: {boundary_path}")

    source = gpd.read_file(boundary_path)
    if source.empty:
        raise ValueError(f"Boundary file has no features: {boundary_path}")
    if source.crs is None:
        source = source.set_crs("EPSG:4326")

    records = []
    for _, row in source.iterrows():
        source_name = _candidate_name(row)
        for point in _geometry_points(row.geometry):
            records.append({"source_name": source_name, "geometry": point})

    if not records:
        raise ValueError(
            f"Boundary file has no usable point coordinates: {boundary_path}"
        )

    return gpd.GeoDataFrame(records, geometry="geometry", crs=source.crs)


def _dedupe_points(gdf: gpd.GeoDataFrame, precision: int = 6) -> gpd.GeoDataFrame:
    seen: set[tuple[float, float]] = set()
    indices = []
    for index, geometry in gdf.geometry.items():
        key = (round(float(geometry.x), precision), round(float(geometry.y), precision))
        if key in seen:
            continue
        seen.add(key)
        indices.append(index)
    unique = gdf.loc[indices].copy()
    unique["_candidate_index"] = indices
    return unique.reset_index(drop=True)


def _name_matches(actual: str | None, expected: str) -> bool:
    if actual is None:
        return False
    return actual.strip().casefold() == expected.strip().casefold()


def _auto_label(name: str | None) -> str | None:
    if name is None:
        return None
    text = name.strip().upper()
    if not text:
        return None
    first = text[0]
    return first if first in CORNER_LABELS else None


def _choose_by_explicit_names(
    candidates: gpd.GeoDataFrame, names: dict[str, str]
) -> dict[str, int]:
    chosen = {}
    for label, expected in names.items():
        matches = [
            idx
            for idx, row in candidates.iterrows()
            if _name_matches(row.get("source_name"), expected)
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Expected exactly one boundary point named {expected!r} for {label}; "
                f"found {len(matches)}"
            )
        chosen[label] = matches[0]
    return chosen


def _choose_by_auto_names(candidates: gpd.GeoDataFrame) -> dict[str, int] | None:
    grouped: dict[str, list[int]] = {label: [] for label in CORNER_LABELS}
    for idx, row in candidates.iterrows():
        label = _auto_label(row.get("source_name"))
        if label is not None:
            grouped[label].append(idx)

    if all(len(grouped[label]) == 1 for label in CORNER_LABELS):
        return {label: grouped[label][0] for label in CORNER_LABELS}
    return None


def _choose_from_four_points(candidates: gpd.GeoDataFrame) -> dict[str, int]:
    points = list(candidates.geometry)
    center_x = sum(point.x for point in points) / 4.0
    center_y = sum(point.y for point in points) / 4.0
    ordered = sorted(
        range(4),
        key=lambda idx: math.atan2(points[idx].y - center_y, points[idx].x - center_x),
    )

    west = min(range(4), key=lambda idx: (points[idx].x, points[idx].y))
    west_pos = ordered.index(west)
    east = ordered[(west_pos + 2) % 4]
    adjacent = [ordered[(west_pos - 1) % 4], ordered[(west_pos + 1) % 4]]
    north = max(adjacent, key=lambda idx: points[idx].y)
    south = min(adjacent, key=lambda idx: points[idx].y)
    return {"W": west, "N": north, "E": east, "S": south}


def _choose_by_extrema(candidates: gpd.GeoDataFrame) -> dict[str, int]:
    points = list(candidates.geometry)
    extrema = {
        "W": min(range(len(points)), key=lambda idx: (points[idx].x, points[idx].y)),
        "N": max(range(len(points)), key=lambda idx: (points[idx].y, -points[idx].x)),
        "E": max(range(len(points)), key=lambda idx: (points[idx].x, points[idx].y)),
        "S": min(range(len(points)), key=lambda idx: (points[idx].y, points[idx].x)),
    }
    if len(set(extrema.values())) != 4:
        raise ValueError(
            "Could not infer four distinct W,N,E,S corners; pass --corner-names "
            "or --corners"
        )
    return extrema


def _select_corner_indices(
    candidates: gpd.GeoDataFrame, corner_names: dict[str, str] | None
) -> dict[str, int]:
    if corner_names is not None:
        return _choose_by_explicit_names(candidates, corner_names)

    auto = _choose_by_auto_names(candidates)
    if auto is not None:
        return auto

    unique = _dedupe_points(candidates)
    if len(unique) == 4:
        chosen = _choose_from_four_points(unique)
        return {
            label: int(unique.iloc[position]["_candidate_index"])
            for label, position in chosen.items()
        }

    return _choose_by_extrema(candidates)


def load_corners(
    *,
    boundary_path: Path | None,
    corners_text: str | None,
    work_crs: CRS,
    corner_names: str | None = None,
) -> gpd.GeoDataFrame:
    if boundary_path is None and corners_text is None:
        raise ValueError("Provide either --boundary or --corners")
    if boundary_path is not None and corners_text is not None:
        raise ValueError("Provide only one of --boundary or --corners")

    if corners_text is not None:
        source = _parse_inline_corners(corners_text)
        explicit_names = dict(zip(CORNER_LABELS, CORNER_LABELS))
    else:
        source = _read_boundary_candidates(Path(boundary_path))
        explicit_names = _parse_corner_names(corner_names)

    source_wgs = source.to_crs("EPSG:4326")
    work = source.to_crs(work_crs)
    chosen = _select_corner_indices(work, explicit_names)

    records = []
    for label in CORNER_LABELS:
        idx = chosen[label]
        point = work.geometry.iloc[idx]
        source_point = source_wgs.geometry.iloc[idx]
        records.append(
            {
                "corner": label,
                "source_name": work.iloc[idx].get("source_name"),
                "lon": float(source_point.x),
                "lat": float(source_point.y),
                "x": float(point.x),
                "y": float(point.y),
                "geometry": Point(float(point.x), float(point.y)),
            }
        )

    return gpd.GeoDataFrame(records, geometry="geometry", crs=work_crs)


def _corner_point(corners: gpd.GeoDataFrame, label: str) -> Point:
    matches = corners[corners["corner"] == label]
    if len(matches) != 1:
        raise ValueError(f"Missing corner {label}")
    return matches.geometry.iloc[0]


def validate_parallelogram(corners: gpd.GeoDataFrame, tolerance_m: float) -> float:
    if tolerance_m < 0:
        raise ValueError("--parallelogram-tolerance-m must be non-negative")
    west = _corner_point(corners, "W")
    north = _corner_point(corners, "N")
    east = _corner_point(corners, "E")
    south = _corner_point(corners, "S")

    expected_x = north.x + south.x - west.x
    expected_y = north.y + south.y - west.y
    residual_m = math.hypot(east.x - expected_x, east.y - expected_y)
    if residual_m > tolerance_m:
        raise ValueError(
            "Boundary corners are not a parallelogram within tolerance: "
            f"residual {residual_m:.3f} m > {tolerance_m:.3f} m"
        )
    return residual_m


def build_boundary_layer(
    corners: gpd.GeoDataFrame,
    *,
    grid_size: int,
    dem_path: Path,
    boundary_path: Path | None,
    residual_m: float,
) -> gpd.GeoDataFrame:
    polygon = Polygon(
        [
            _corner_point(corners, "W"),
            _corner_point(corners, "N"),
            _corner_point(corners, "E"),
            _corner_point(corners, "S"),
            _corner_point(corners, "W"),
        ]
    )
    row = {
        "grid_size": grid_size,
        "dem_path": str(dem_path),
        "boundary_path": str(boundary_path) if boundary_path is not None else None,
        "residual_m": residual_m,
        "geometry": polygon,
    }
    return gpd.GeoDataFrame([row], geometry="geometry", crs=corners.crs)


def build_grid_cells(corners: gpd.GeoDataFrame, grid_size: int) -> gpd.GeoDataFrame:
    west = _corner_point(corners, "W")
    north = _corner_point(corners, "N")
    south = _corner_point(corners, "S")
    u_vec = (north.x - west.x, north.y - west.y)
    v_vec = (south.x - west.x, south.y - west.y)
    width = max(2, len(str(grid_size)))

    def affine_point(u_scale: float, v_scale: float) -> Point:
        return Point(
            west.x + u_vec[0] * u_scale + v_vec[0] * v_scale,
            west.y + u_vec[1] * u_scale + v_vec[1] * v_scale,
        )

    rows = []
    cell_id = 1
    for u_idx in range(grid_size):
        for v_idx in range(grid_size):
            u0 = u_idx / grid_size
            u1 = (u_idx + 1) / grid_size
            v0 = v_idx / grid_size
            v1 = (v_idx + 1) / grid_size
            ring = [
                affine_point(u0, v0),
                affine_point(u1, v0),
                affine_point(u1, v1),
                affine_point(u0, v1),
            ]
            rows.append(
                {
                    "cell_id": cell_id,
                    "cell_name": f"U{u_idx + 1:0{width}d}_V{v_idx + 1:0{width}d}",
                    "u_index": u_idx + 1,
                    "v_index": v_idx + 1,
                    "grid_size": grid_size,
                    "geometry": Polygon([point.coords[0] for point in ring]),
                }
            )
            cell_id += 1

    return gpd.GeoDataFrame(rows, geometry="geometry", crs=corners.crs)


def _transform_geometry(geometry, src_crs: CRS, dst_crs: CRS):
    transformer = Transformer.from_crs(src_crs, dst_crs, always_xy=True)

    def _xy(x, y, z=None):
        return transformer.transform(x, y)

    return shapely_transform(_xy, geometry)


def _peak_for_cell(
    *,
    dataset,
    cell_geometry,
    cell_crs: CRS,
    dem_crs: CRS,
    all_touched: bool,
) -> dict[str, object]:
    cell_dem = _transform_geometry(cell_geometry, cell_crs, dem_crs)
    try:
        window = geometry_window(dataset, [mapping(cell_dem)])
    except WindowError:
        return {"status": "no_dem_overlap", "valid_px": 0}

    if window.width <= 0 or window.height <= 0:
        return {"status": "no_dem_overlap", "valid_px": 0}

    data = dataset.read(1, window=window, masked=False)
    if data.size == 0:
        return {"status": "no_dem_overlap", "valid_px": 0}

    window_transform = dataset.window_transform(window)
    inside = geometry_mask(
        [mapping(cell_dem)],
        out_shape=data.shape,
        transform=window_transform,
        invert=True,
        all_touched=all_touched,
    )
    valid = inside & np.isfinite(data)
    if dataset.nodata is not None:
        valid &= data != dataset.nodata

    valid_count = int(np.count_nonzero(valid))
    if valid_count == 0:
        return {"status": "no_valid_dem_pixels", "valid_px": 0}

    max_elevation = float(np.max(data[valid]))
    candidate_rows, candidate_cols = np.where(valid & (data == max_elevation))

    xs, ys = rasterio.transform.xy(
        window_transform, candidate_rows, candidate_cols, offset="center"
    )
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)

    dem_to_work = Transformer.from_crs(dem_crs, cell_crs, always_xy=True)
    work_xs, work_ys = dem_to_work.transform(xs, ys)
    work_xs = np.asarray(work_xs, dtype=float)
    work_ys = np.asarray(work_ys, dtype=float)

    centroid = cell_geometry.centroid
    dist2 = (work_xs - centroid.x) ** 2 + (work_ys - centroid.y) ** 2
    global_rows = candidate_rows + int(window.row_off)
    global_cols = candidate_cols + int(window.col_off)
    order = np.lexsort((global_cols, global_rows, dist2))
    selected = int(order[0])

    dem_x = float(xs[selected])
    dem_y = float(ys[selected])
    peak_x = float(work_xs[selected])
    peak_y = float(work_ys[selected])
    dem_to_wgs84 = Transformer.from_crs(dem_crs, "EPSG:4326", always_xy=True)
    peak_lon, peak_lat = dem_to_wgs84.transform(dem_x, dem_y)

    return {
        "status": "ok",
        "valid_px": valid_count,
        "peak_elev_m": max_elevation,
        "peak_elev_ft": max_elevation * METERS_TO_FEET,
        "peak_lon": float(peak_lon),
        "peak_lat": float(peak_lat),
        "peak_x": peak_x,
        "peak_y": peak_y,
        "dem_row": int(global_rows[selected]),
        "dem_col": int(global_cols[selected]),
    }


def attach_peak_attributes(
    cells: gpd.GeoDataFrame,
    *,
    dem_path: Path,
    all_touched: bool,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    if not dem_path.exists():
        raise ValueError(f"DEM file does not exist: {dem_path}")

    cell_crs = _coerce_crs(cells.crs)
    records = []
    with rasterio.open(dem_path) as dataset:
        if dataset.crs is None:
            raise ValueError(f"DEM has no CRS: {dem_path}")
        dem_crs = _coerce_crs(dataset.crs)
        for _, row in cells.iterrows():
            peak = _peak_for_cell(
                dataset=dataset,
                cell_geometry=row.geometry,
                cell_crs=cell_crs,
                dem_crs=dem_crs,
                all_touched=all_touched,
            )
            records.append(peak)

    peak_frame = pd.DataFrame(records)
    output_cells = cells.reset_index(drop=True).join(peak_frame)

    peak_rows = output_cells[output_cells["status"] == "ok"].copy()
    peak_geometry = [
        Point(float(row.peak_x), float(row.peak_y)) for row in peak_rows.itertuples()
    ]
    peaks = gpd.GeoDataFrame(
        peak_rows.drop(columns="geometry"),
        geometry=peak_geometry,
        crs=cells.crs,
    )
    return output_cells, peaks


def _remove_output(path: Path) -> None:
    for candidate in (
        path,
        path.with_name(path.name + "-wal"),
        path.with_name(path.name + "-shm"),
    ):
        if candidate.exists():
            candidate.unlink()


def write_geopackage(
    output_path: Path,
    *,
    cells: gpd.GeoDataFrame,
    peaks: gpd.GeoDataFrame,
    boundary: gpd.GeoDataFrame,
    corners: gpd.GeoDataFrame,
    overwrite: bool,
) -> None:
    if output_path.exists():
        if not overwrite:
            raise ValueError(f"Output already exists; pass --overwrite: {output_path}")
        _remove_output(output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    layers = [
        ("highgrid_cells", cells),
        ("highgrid_peaks", peaks),
        ("highgrid_boundary", boundary),
        ("highgrid_corners", corners),
    ]
    for layer_name, gdf in layers:
        gdf.to_file(output_path, layer=layer_name, driver="GPKG", engine="pyogrio")


def run_highgrid(
    *,
    dem_path: Path | str = default_dem_path(),
    output_path: Path | str = default_output_path(),
    grid_size: int,
    boundary_path: Path | str | None = None,
    corners_text: str | None = None,
    work_crs: str | CRS = "EPSG:26985",
    corner_names: str | None = "auto",
    parallelogram_tolerance_m: float = 30.0,
    all_touched: bool = False,
    overwrite: bool = False,
) -> HighgridResult:
    if grid_size < 1 or grid_size > 100:
        raise ValueError("grid_size must be an integer from 1 to 100")

    dem = Path(dem_path)
    output = Path(output_path)
    boundary = Path(boundary_path) if boundary_path is not None else None
    resolved_work_crs = _coerce_crs(work_crs)

    corners = load_corners(
        boundary_path=boundary,
        corners_text=corners_text,
        work_crs=resolved_work_crs,
        corner_names=corner_names,
    )
    residual = validate_parallelogram(corners, parallelogram_tolerance_m)
    cells = build_grid_cells(corners, grid_size)
    cells, peaks = attach_peak_attributes(cells, dem_path=dem, all_touched=all_touched)
    boundary_layer = build_boundary_layer(
        corners,
        grid_size=grid_size,
        dem_path=dem,
        boundary_path=boundary,
        residual_m=residual,
    )
    write_geopackage(
        output,
        cells=cells,
        peaks=peaks,
        boundary=boundary_layer,
        corners=corners,
        overwrite=overwrite,
    )

    return HighgridResult(
        output_path=output,
        cell_count=len(cells),
        peak_count=len(peaks),
        grid_size=grid_size,
    )


def run_from_namespace(args: argparse.Namespace) -> HighgridResult:
    return run_highgrid(
        dem_path=Path(args.dem),
        output_path=Path(args.output),
        grid_size=args.grid_size,
        boundary_path=Path(args.boundary) if args.boundary else None,
        corners_text=args.corners,
        work_crs=args.work_crs,
        corner_names=args.corner_names,
        parallelogram_tolerance_m=float(args.parallelogram_tolerance_m),
        all_touched=bool(args.all_touched),
        overwrite=bool(args.overwrite),
    )


def build_parser(prog: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Find the highest DEM point in each cell of a parallelogram grid.",
    )
    boundary = parser.add_mutually_exclusive_group(required=True)
    boundary.add_argument(
        "--boundary", help="Vector file containing four boundary corners."
    )
    boundary.add_argument(
        "--corners",
        help="Inline W,N,E,S corners as 'lon,lat;lon,lat;lon,lat;lon,lat'.",
    )
    parser.add_argument("--grid-size", required=True, type=parse_grid_size)
    parser.add_argument(
        "--dem", default=str(default_dem_path()), help="DEM GeoTIFF path."
    )
    parser.add_argument(
        "--output",
        default=str(default_output_path()),
        help="Output GeoPackage path.",
    )
    parser.add_argument("--work-crs", default="EPSG:26985", help="Projected work CRS.")
    parser.add_argument(
        "--corner-names",
        default="auto",
        help="auto, or four comma-separated names in W,N,E,S order.",
    )
    parser.add_argument(
        "--parallelogram-tolerance-m",
        default=30.0,
        type=float,
        help="Maximum W+E/N+S parallelogram residual in work CRS units.",
    )
    parser.add_argument(
        "--all-touched",
        action="store_true",
        help="Include DEM pixels touched by a cell, not just center-in-cell pixels.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing output GeoPackage.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        result = run_from_namespace(args)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(result.output_path)
    print(f"cells: {result.cell_count}")
    print(f"peaks: {result.peak_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
