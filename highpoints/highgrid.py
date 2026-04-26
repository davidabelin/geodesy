"""Build a parallelogram grid and extract per-cell DEM representative points.

The highgrid workflow is the new QGIS-ready highpoints path for this package.
It intentionally avoids PyQGIS runtime state: all geometry work uses
GeoPandas/Shapely, all raster sampling uses Rasterio, and the final products
are a GeoPackage plus a flat CSV.

The grid is defined by four corners labeled ``W``, ``N``, ``E``, and ``S``.
Those labels are compass-ish names, but they are also basis-vector names:
``W`` is the origin, ``W -> N`` is the ``u`` axis, and ``W -> S`` is the
``v`` axis. Cell names follow that basis as ``U##_V##``. Before cells are
built, the input parallelogram can be rotated by ``--offset-angle``; that angle
is clockwise-positive because this tool is user-facing and map-oriented, even
though the underlying math uses the usual counterclockwise-positive convention.

For every valid cell, the DEM pass chooses three representative pixels:
highest, lowest, and "avg" meaning the pixel whose elevation is closest to the
cell mean. Ties are resolved deterministically by selecting the tied pixel
closest to the cell centroid, then by DEM row and column.
"""

from __future__ import annotations

import argparse
import html
import math
import os
import re
import sys
import time
import zipfile
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
EXPENSIVE_CELL_CONFIRMATION_THRESHOLD = 5_000


def repo_root() -> Path:
    """Return the repository root inferred from this package file."""
    return Path(__file__).resolve().parents[1]


def default_dem_path() -> Path:
    """Return the default DEM used by the local DC highpoints workflow."""
    return repo_root() / "data" / "tif" / "dc_dem.tif"


def default_corner_path() -> Path:
    """Return the default NAD83 corner CSV for the working parallelogram."""
    return Path(__file__).resolve().parent / "crnrpoints.csv"


def default_output_path() -> Path:
    """Return the default GeoPackage output path."""
    return Path(__file__).resolve().parent / "out" / "highgrid.gpkg"


def default_csv_output_path(output_path: Path | str | None = None) -> Path:
    """Return the default long-form CSV path.

    When a GeoPackage path is supplied, the CSV is placed beside it and named
    ``highgrid_out.csv``. That keeps scripted runs predictable while avoiding
    a second required CLI argument.
    """
    if output_path is not None:
        return Path(output_path).with_name("highgrid_out.csv")
    return Path(__file__).resolve().parent / "out" / "highgrid_out.csv"


def default_project_output_path(output_path: Path | str | None = None) -> Path:
    """Return the default QGIS project archive path for a GeoPackage output."""
    if output_path is not None:
        return Path(output_path).with_suffix(".qgz")
    return Path(__file__).resolve().parent / "out" / "highgrid.qgz"


def default_project_template_path() -> Path:
    """Return the bundled QGIS project template for highgrid outputs."""
    return Path(__file__).resolve().parent / "templates" / "highgrid_template.qgz"


@dataclass(frozen=True)
class HighgridResult:
    """Small return object for callers that invoke the workflow as Python.

    The package CLI uses this to print a stable summary. ``peak_count`` remains
    as a compatibility alias for the high-point count from the first highgrid
    version, where only high points existed.
    """

    output_path: Path
    csv_output_path: Path
    project_output_path: Path | None
    cell_count: int
    hi_point_count: int
    lo_point_count: int
    avg_point_count: int
    grid_size: int

    @property
    def peak_count(self) -> int:
        """Compatibility alias for older callers that used ``peak_count``."""
        return self.hi_point_count


def parse_grid_size(value: str) -> int:
    """Parse and validate the public ``--grid-size`` option."""
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
    """Normalize user CRS input to ``pyproj.CRS`` with a clear failure mode."""
    crs = CRS.from_user_input(value)
    if crs is None:
        raise ValueError(f"Invalid CRS: {value}")
    return crs


def _crs_authid(value: str | CRS) -> str:
    """Return a compact authority id for output metadata."""
    crs = _coerce_crs(value)
    authority = crs.to_authority()
    if authority:
        return f"{authority[0]}:{authority[1]}"
    return crs.to_string()


def _parse_corner_names(value: str | None) -> dict[str, str] | None:
    """Parse an explicit ``W,N,E,S`` name mapping or return auto-detect mode."""
    if value is None or value.strip().lower() in {"", "auto"}:
        return None
    names = [part.strip() for part in value.split(",")]
    if len(names) != 4 or any(not part for part in names):
        raise ValueError("--corner-names must be 'auto' or four comma-separated names")
    return dict(zip(CORNER_LABELS, names))


def _parse_inline_corners(corners_text: str, crs: CRS) -> gpd.GeoDataFrame:
    """Parse ``--corners`` text into points in required W,N,E,S order."""
    parts = [part.strip() for part in corners_text.split(";") if part.strip()]
    if len(parts) != 4:
        raise ValueError("--corners must contain four x,y pairs in W,N,E,S order")

    records = []
    for label, part in zip(CORNER_LABELS, parts):
        values = [piece.strip() for piece in part.split(",")]
        if len(values) < 2:
            raise ValueError("--corners must contain x,y pairs")
        try:
            lon = float(values[0])
            lat = float(values[1])
        except ValueError as exc:
            raise ValueError("--corners must contain numeric x,y pairs") from exc
        records.append({"source_name": label, "geometry": Point(lon, lat)})

    return gpd.GeoDataFrame(records, geometry="geometry", crs=crs)


def _candidate_name(row: pd.Series) -> str | None:
    """Return the best available human-readable name from a vector row."""
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


def _candidate_alt_m(row: pd.Series) -> float | None:
    """Return an optional source altitude in meters when a row provides one."""
    for column in ("Alt", "alt", "ALT", "alt_m", "elev_m", "Elevation"):
        if column not in row:
            continue
        value = row[column]
        if value is None or pd.isna(value):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _find_column(columns: Iterable[str], options: Sequence[str]) -> str | None:
    """Find a column by case-insensitive name from a preference list."""
    by_name = {str(column).strip().casefold(): str(column) for column in columns}
    for option in options:
        found = by_name.get(option.casefold())
        if found is not None:
            return found
    return None


def _read_csv_boundary_candidates(
    boundary_path: Path, *, default_crs: CRS
) -> gpd.GeoDataFrame:
    """Read the default-style corner CSV as points in the caller's input CRS.

    The CSV contract is deliberately small: ``Name,Lat,Lon,Alt``. ``Alt`` is
    optional metadata for the corner layer; the DEM remains authoritative for
    cell elevations.
    """
    source = pd.read_csv(boundary_path)
    if source.empty:
        raise ValueError(f"Boundary CSV has no rows: {boundary_path}")

    name_col = _find_column(source.columns, ("Name", "name", "corner", "label", "LOC"))
    lat_col = _find_column(source.columns, ("Lat", "lat", "latitude", "y"))
    lon_col = _find_column(source.columns, ("Lon", "lon", "longitude", "long", "x"))
    alt_col = _find_column(source.columns, ("Alt", "alt", "alt_m", "elev_m"))

    if lat_col is None or lon_col is None:
        raise ValueError(
            f"Boundary CSV must include Lat and Lon columns: {boundary_path}"
        )

    records = []
    for row_index, row in source.iterrows():
        try:
            lat = float(row[lat_col])
            lon = float(row[lon_col])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Boundary CSV row {row_index + 2} has non-numeric Lat/Lon"
            ) from exc
        if not math.isfinite(lat) or not math.isfinite(lon):
            raise ValueError(f"Boundary CSV row {row_index + 2} has invalid Lat/Lon")

        source_name = None
        if name_col is not None and not pd.isna(row[name_col]):
            source_name = str(row[name_col]).strip() or None

        alt_m = None
        if alt_col is not None and not pd.isna(row[alt_col]):
            try:
                alt_m = float(row[alt_col])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Boundary CSV row {row_index + 2} has non-numeric Alt"
                ) from exc

        records.append(
            {
                "source_name": source_name,
                "alt_m": alt_m,
                "geometry": Point(lon, lat),
            }
        )

    return gpd.GeoDataFrame(records, geometry="geometry", crs=default_crs)


def _geometry_points(geometry) -> Iterable[Point]:
    """Yield point coordinates from common vector geometry shapes.

    Boundary inputs may be four points, a line, a polygon, or multipart
    variants. The downstream selection code only needs candidate point
    coordinates, so this flattens those shapes without assuming a specific
    source format.
    """
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


def _read_boundary_candidates(
    boundary_path: Path, *, default_crs: CRS
) -> gpd.GeoDataFrame:
    """Read a boundary file and return all candidate corner coordinates."""
    if not boundary_path.exists():
        raise ValueError(f"Boundary file does not exist: {boundary_path}")

    if boundary_path.suffix.casefold() == ".csv":
        return _read_csv_boundary_candidates(boundary_path, default_crs=default_crs)

    source = gpd.read_file(boundary_path)
    if source.empty:
        raise ValueError(f"Boundary file has no features: {boundary_path}")
    if source.crs is None:
        source = source.set_crs(default_crs)

    records = []
    for _, row in source.iterrows():
        source_name = _candidate_name(row)
        alt_m = _candidate_alt_m(row)
        for point in _geometry_points(row.geometry):
            records.append(
                {"source_name": source_name, "alt_m": alt_m, "geometry": point}
            )

    if not records:
        raise ValueError(
            f"Boundary file has no usable point coordinates: {boundary_path}"
        )

    return gpd.GeoDataFrame(records, geometry="geometry", crs=source.crs)


def _dedupe_points(gdf: gpd.GeoDataFrame, precision: int = 6) -> gpd.GeoDataFrame:
    """Remove duplicate candidate coordinates while preserving source indices."""
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
    """Compare source names using the same loose matching as the CLI."""
    if actual is None:
        return False
    return actual.strip().casefold() == expected.strip().casefold()


def _auto_label(name: str | None) -> str | None:
    """Infer one of W,N,E,S from the first character of a candidate name."""
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
    """Select corner rows using a caller-supplied W,N,E,S name mapping."""
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
    """Select corners when each source name begins with a unique W,N,E,S."""
    grouped: dict[str, list[int]] = {label: [] for label in CORNER_LABELS}
    for idx, row in candidates.iterrows():
        label = _auto_label(row.get("source_name"))
        if label is not None:
            grouped[label].append(idx)

    if all(len(grouped[label]) == 1 for label in CORNER_LABELS):
        return {label: grouped[label][0] for label in CORNER_LABELS}
    return None


def _choose_from_four_points(candidates: gpd.GeoDataFrame) -> dict[str, int]:
    """Infer W,N,E,S from exactly four unnamed points.

    This fallback is for simple four-corner files. It chooses the western point
    as ``W``, the opposite point as ``E``, and assigns the adjacent points as
    ``N`` and ``S`` by y-coordinate.
    """
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
    """Infer W,N,E,S from coordinate extrema when more candidates are present."""
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
    """Choose one source row for each corner using explicit, auto, then fallback."""
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
    target_crs: CRS,
    input_crs: CRS | None = None,
    corner_names: str | None = None,
) -> gpd.GeoDataFrame:
    """Load, label, and project the four grid-defining corners.

    Returned corners are always ordered as ``W,N,E,S`` and expressed in
    ``target_crs`` geometry. The longitude/latitude fields are kept for easy
    inspection in QGIS and CSV-like attribute views.
    """
    if boundary_path is None and corners_text is None:
        raise ValueError("Provide either --boundary or --corners")
    if boundary_path is not None and corners_text is not None:
        raise ValueError("Provide only one of --boundary or --corners")

    source_crs = input_crs if input_crs is not None else target_crs
    if corners_text is not None:
        source = _parse_inline_corners(corners_text, source_crs)
        explicit_names = dict(zip(CORNER_LABELS, CORNER_LABELS))
    else:
        source = _read_boundary_candidates(Path(boundary_path), default_crs=source_crs)
        explicit_names = _parse_corner_names(corner_names)

    source_wgs = source.to_crs("EPSG:4326")
    work = source.to_crs(target_crs)
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
                "alt_m": work.iloc[idx].get("alt_m"),
                "geometry": Point(float(point.x), float(point.y)),
            }
        )

    return gpd.GeoDataFrame(records, geometry="geometry", crs=target_crs)


def _corner_point(corners: gpd.GeoDataFrame, label: str) -> Point:
    """Return the single point geometry for a labeled corner."""
    matches = corners[corners["corner"] == label]
    if len(matches) != 1:
        raise ValueError(f"Missing corner {label}")
    return matches.geometry.iloc[0]


def validate_parallelogram(corners: gpd.GeoDataFrame, tolerance_m: float) -> float:
    """Validate that W,N,E,S describe a parallelogram within ``tolerance_m``.

    The expected opposite corner is ``N + S - W`` in the DEM CRS.
    The returned residual is useful output metadata because real source files
    may be close to, but not exactly, a mathematical parallelogram.
    """
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


def _rotate_point_around(
    point: Point, *, center_x: float, center_y: float, offset_angle_deg: float
) -> Point:
    """Rotate a work-CRS point around a center using clockwise-positive degrees."""
    radians = math.radians(-offset_angle_deg)
    cos_angle = math.cos(radians)
    sin_angle = math.sin(radians)
    dx = point.x - center_x
    dy = point.y - center_y
    return Point(
        center_x + dx * cos_angle - dy * sin_angle,
        center_y + dx * sin_angle + dy * cos_angle,
    )


def apply_offset_angle(
    corners: gpd.GeoDataFrame, offset_angle_deg: float
) -> gpd.GeoDataFrame:
    """Apply the user-facing grid angle offset to the corner geometries.

    The source corner coordinates are preserved in ``input_*`` fields. The
    active ``x/y/lon/lat`` fields and geometry become the rotated working
    boundary, which is what cells, displays, and DEM sampling use.
    """
    if not math.isfinite(offset_angle_deg):
        raise ValueError("--offset-angle must be a finite number of degrees")

    west = _corner_point(corners, "W")
    east = _corner_point(corners, "E")
    center_x = (west.x + east.x) / 2.0
    center_y = (west.y + east.y) / 2.0
    to_wgs84 = Transformer.from_crs(corners.crs, "EPSG:4326", always_xy=True)

    rows = []
    for row in corners.itertuples():
        source_point = row.geometry
        rotated = _rotate_point_around(
            source_point,
            center_x=center_x,
            center_y=center_y,
            offset_angle_deg=offset_angle_deg,
        )
        lon, lat = to_wgs84.transform(rotated.x, rotated.y)
        data = row._asdict()
        data.pop("Index", None)
        data["input_lon"] = data.get("lon")
        data["input_lat"] = data.get("lat")
        data["input_x"] = data.get("x")
        data["input_y"] = data.get("y")
        data["lon"] = float(lon)
        data["lat"] = float(lat)
        data["x"] = float(rotated.x)
        data["y"] = float(rotated.y)
        data["offset_angle_deg"] = offset_angle_deg
        data["geometry"] = rotated
        rows.append(data)

    return gpd.GeoDataFrame(rows, geometry="geometry", crs=corners.crs)


def build_boundary_layer(
    corners: gpd.GeoDataFrame,
    *,
    grid_size: int,
    dem_path: Path,
    boundary_path: Path | None,
    residual_m: float,
    offset_angle_deg: float,
) -> gpd.GeoDataFrame:
    """Build the single-polygon boundary layer written to the GeoPackage."""
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
        "offset_angle_deg": offset_angle_deg,
        "geometry": polygon,
    }
    return gpd.GeoDataFrame([row], geometry="geometry", crs=corners.crs)


def build_grid_cells(
    corners: gpd.GeoDataFrame, grid_size: int, *, offset_angle_deg: float = 0.0
) -> gpd.GeoDataFrame:
    """Split the active parallelogram into a stable U/V cell grid.

    ``W`` is the affine origin. Moving toward ``N`` increments ``u_index`` and
    moving toward ``S`` increments ``v_index``. ``cell_id`` increments with
    ``u`` outermost and ``v`` innermost so names and ids remain deterministic.
    """
    west = _corner_point(corners, "W")
    north = _corner_point(corners, "N")
    south = _corner_point(corners, "S")
    u_vec = (north.x - west.x, north.y - west.y)
    v_vec = (south.x - west.x, south.y - west.y)
    width = max(2, len(str(grid_size)))

    def affine_point(u_scale: float, v_scale: float) -> Point:
        """Evaluate the W-origin parallelogram basis at fractional u/v scales."""
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
                    "offset_angle_deg": offset_angle_deg,
                    "geometry": Polygon([point.coords[0] for point in ring]),
                }
            )
            cell_id += 1

    return gpd.GeoDataFrame(rows, geometry="geometry", crs=corners.crs)


def _transform_geometry(geometry, src_crs: CRS, dst_crs: CRS):
    """Transform a Shapely geometry between coordinate reference systems."""
    transformer = Transformer.from_crs(src_crs, dst_crs, always_xy=True)

    def _xy(x, y, z=None):
        """Coordinate callback used by Shapely's transform helper."""
        return transformer.transform(x, y)

    return shapely_transform(_xy, geometry)


def _select_candidate_pixel(
    *,
    prefix: str,
    data: np.ndarray,
    candidate_mask: np.ndarray,
    window,
    window_transform,
    cell_geometry,
    cell_crs: CRS,
    dem_crs: CRS,
) -> dict[str, object]:
    """Choose one pixel from a candidate mask using the standard tie policy.

    ``prefix`` becomes the output field prefix, for example ``hi_elev_m`` or
    ``avg_dem_row``. All candidate masks are resolved the same way: closest to
    the cell centroid in the output CRS, then lowest DEM row, then lowest DEM
    column.
    """
    candidate_rows, candidate_cols = np.where(candidate_mask)
    if len(candidate_rows) == 0:
        raise ValueError(f"No candidate DEM pixels for {prefix}")

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
    point_x = float(work_xs[selected])
    point_y = float(work_ys[selected])
    elevation = float(data[candidate_rows[selected], candidate_cols[selected]])
    dem_to_wgs84 = Transformer.from_crs(dem_crs, "EPSG:4326", always_xy=True)
    lon, lat = dem_to_wgs84.transform(dem_x, dem_y)

    return {
        f"{prefix}_elev_m": elevation,
        f"{prefix}_elev_ft": elevation * METERS_TO_FEET,
        f"{prefix}_lon": float(lon),
        f"{prefix}_lat": float(lat),
        f"{prefix}_x": point_x,
        f"{prefix}_y": point_y,
        f"{prefix}_dem_row": int(global_rows[selected]),
        f"{prefix}_dem_col": int(global_cols[selected]),
    }


def _extrema_for_cell(
    *,
    dataset,
    cell_geometry,
    cell_crs: CRS,
    dem_crs: CRS,
    all_touched: bool,
) -> dict[str, object]:
    """Extract high, low, and mean-nearest DEM pixels for one grid cell.

    The raster read is scoped to the cell window, then masked to the cell
    polygon in DEM CRS. Nodata and non-finite values are excluded. The ``avg``
    point is not a synthetic coordinate; it is an actual DEM pixel center whose
    value is closest to the valid-pixel mean for that cell.
    """
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

    hi_elevation = float(np.max(data[valid]))
    lo_elevation = float(np.min(data[valid]))
    mean_elevation = float(np.mean(data[valid]))
    mean_delta = np.full(data.shape, np.inf, dtype=float)
    mean_delta[valid] = np.abs(data[valid] - mean_elevation)
    min_mean_delta = float(np.min(mean_delta[valid]))

    result = {
        "status": "ok",
        "valid_px": valid_count,
        "mean_elev_m": mean_elevation,
        "mean_elev_ft": mean_elevation * METERS_TO_FEET,
    }
    result.update(
        _select_candidate_pixel(
            prefix="hi",
            data=data,
            candidate_mask=valid & (data == hi_elevation),
            window=window,
            window_transform=window_transform,
            cell_geometry=cell_geometry,
            cell_crs=cell_crs,
            dem_crs=dem_crs,
        )
    )
    result.update(
        _select_candidate_pixel(
            prefix="lo",
            data=data,
            candidate_mask=valid & (data == lo_elevation),
            window=window,
            window_transform=window_transform,
            cell_geometry=cell_geometry,
            cell_crs=cell_crs,
            dem_crs=dem_crs,
        )
    )
    result.update(
        _select_candidate_pixel(
            prefix="avg",
            data=data,
            candidate_mask=valid & (mean_delta == min_mean_delta),
            window=window,
            window_transform=window_transform,
            cell_geometry=cell_geometry,
            cell_crs=cell_crs,
            dem_crs=dem_crs,
        )
    )
    avg_delta_m = abs(float(result["avg_elev_m"]) - mean_elevation)
    result["avg_delta_m"] = avg_delta_m
    result["avg_delta_ft"] = avg_delta_m * METERS_TO_FEET
    return result


def _point_layer(
    output_cells: gpd.GeoDataFrame, *, prefix: str, point_type: str
) -> gpd.GeoDataFrame:
    """Create one point layer from the prefixed cell attributes."""
    point_rows = output_cells[output_cells["status"] == "ok"].copy()
    point_geometry = [
        Point(float(getattr(row, f"{prefix}_x")), float(getattr(row, f"{prefix}_y")))
        for row in point_rows.itertuples()
    ]
    points = gpd.GeoDataFrame(
        point_rows.drop(columns="geometry"),
        geometry=point_geometry,
        crs=output_cells.crs,
    )
    for column in (
        f"{prefix}_elev_m",
        f"{prefix}_elev_ft",
        f"{prefix}_lon",
        f"{prefix}_lat",
        f"{prefix}_x",
        f"{prefix}_y",
        f"{prefix}_dem_row",
        f"{prefix}_dem_col",
    ):
        if column not in points:
            points[column] = pd.Series(dtype="float64")
    points["point_type"] = point_type
    points["elev_m"] = points[f"{prefix}_elev_m"]
    points["elev_ft"] = points[f"{prefix}_elev_ft"]
    points["lon"] = points[f"{prefix}_lon"]
    points["lat"] = points[f"{prefix}_lat"]
    points["x"] = points[f"{prefix}_x"]
    points["y"] = points[f"{prefix}_y"]
    points["dem_row"] = points[f"{prefix}_dem_row"]
    points["dem_col"] = points[f"{prefix}_dem_col"]
    points["crs_authid"] = _crs_authid(output_cells.crs)
    return points


def _format_duration(seconds: float) -> str:
    """Return a compact elapsed/ETA duration for progress messages."""
    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


def _print_dem_progress(
    *,
    completed: int,
    total: int,
    ok_count: int,
    start_time: float,
    final: bool = False,
) -> None:
    """Print a low-frequency DEM progress line to stderr."""
    elapsed = time.monotonic() - start_time
    percent = (completed / total * 100.0) if total else 100.0
    eta_text = "--"
    if completed > 0 and completed < total:
        remaining = elapsed / completed * (total - completed)
        eta_text = _format_duration(remaining)
    label = "DEM complete" if final else "DEM progress"
    print(
        f"{label}: {completed}/{total} cells ({percent:.1f}%), "
        f"ok={ok_count}, elapsed={_format_duration(elapsed)}, eta={eta_text}",
        file=sys.stderr,
        flush=True,
    )


def attach_extreme_attributes(
    cells: gpd.GeoDataFrame,
    *,
    dem_path: Path,
    all_touched: bool,
    progress: bool = False,
    progress_interval_s: float = 10.0,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Join DEM-derived attributes to cells and build point layers.

    This is the main raster loop. It returns the full cell layer plus separate
    point layers so QGIS users can style high, low, and avg representatives
    independently.
    """
    if not dem_path.exists():
        raise ValueError(f"DEM file does not exist: {dem_path}")

    cell_crs = _coerce_crs(cells.crs)
    records = []
    total_cells = len(cells)
    ok_count = 0
    start_time = time.monotonic()
    next_progress_time = start_time
    if progress:
        _print_dem_progress(
            completed=0,
            total=total_cells,
            ok_count=0,
            start_time=start_time,
        )
    with rasterio.open(dem_path) as dataset:
        if dataset.crs is None:
            raise ValueError(f"DEM has no CRS: {dem_path}")
        dem_crs = _coerce_crs(dataset.crs)
        for completed, row in enumerate(cells.itertuples(), start=1):
            extrema = _extrema_for_cell(
                dataset=dataset,
                cell_geometry=row.geometry,
                cell_crs=cell_crs,
                dem_crs=dem_crs,
                all_touched=all_touched,
            )
            records.append(extrema)
            if extrema.get("status") == "ok":
                ok_count += 1
            now = time.monotonic()
            if progress and (completed == total_cells or now >= next_progress_time):
                _print_dem_progress(
                    completed=completed,
                    total=total_cells,
                    ok_count=ok_count,
                    start_time=start_time,
                    final=completed == total_cells,
                )
                next_progress_time = now + max(1.0, progress_interval_s)

    extrema_frame = pd.DataFrame(records)
    output_cells = cells.reset_index(drop=True).join(extrema_frame)

    hi_points = _point_layer(output_cells, prefix="hi", point_type="hi")
    lo_points = _point_layer(output_cells, prefix="lo", point_type="lo")
    avg_points = _point_layer(output_cells, prefix="avg", point_type="avg")
    return output_cells, hi_points, lo_points, avg_points


def _remove_output(path: Path) -> None:
    """Remove a GeoPackage and sidecar SQLite files before rewriting it."""
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
    hi_points: gpd.GeoDataFrame,
    lo_points: gpd.GeoDataFrame,
    avg_points: gpd.GeoDataFrame,
    boundary: gpd.GeoDataFrame,
    corners: gpd.GeoDataFrame,
    overwrite: bool,
) -> None:
    """Write the full QGIS bundle to a GeoPackage.

    Layer names intentionally use hyphenated ``hi-points``, ``lo-points``, and
    ``avg-points`` because those are presentation-oriented layers, while the
    administrative layers keep the ``highgrid_*`` prefix.
    """
    if output_path.exists():
        if not overwrite:
            raise ValueError(f"Output already exists; pass --overwrite: {output_path}")
        _remove_output(output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    layers = [
        ("highgrid_cells", cells),
        ("hi-points", hi_points),
        ("lo-points", lo_points),
        ("avg-points", avg_points),
        ("highgrid_boundary", boundary),
        ("highgrid_corners", corners),
    ]
    for layer_name, gdf in layers:
        gdf.to_file(output_path, layer=layer_name, driver="GPKG", engine="pyogrio")


def _check_output_paths(
    *,
    output_path: Path,
    csv_output_path: Path,
    project_output_path: Path | None,
    overwrite: bool,
) -> None:
    """Validate output collisions before any expensive raster work begins."""
    if output_path.suffix.casefold() != ".gpkg":
        raise ValueError(f"--output must end in .gpkg: {output_path}")
    if (
        project_output_path is not None
        and project_output_path.suffix.casefold() != ".qgz"
    ):
        raise ValueError(f"--project-output must end in .qgz: {project_output_path}")
    for label, path in (
        ("--output", output_path),
        ("--csv-output", csv_output_path),
        ("--project-output", project_output_path),
    ):
        if path is None:
            continue
        parent = path.parent
        if parent == Path("."):
            continue
        if parent.exists() and not parent.is_dir():
            raise ValueError(f"{label} parent exists but is not a directory: {parent}")
    labeled_paths = [
        ("--output", output_path),
        ("--csv-output", csv_output_path),
    ]
    if project_output_path is not None:
        labeled_paths.append(("--project-output", project_output_path))
    resolved_paths = [
        (label, path.resolve(strict=False)) for label, path in labeled_paths
    ]
    for index, (left_label, left_path) in enumerate(resolved_paths):
        for right_label, right_path in resolved_paths[index + 1 :]:
            if left_path == right_path:
                raise ValueError(
                    f"{left_label} and {right_label} must be different paths"
                )
    for label, path in labeled_paths:
        if path.exists() and not overwrite:
            raise ValueError(f"{label} already exists; pass --overwrite: {path}")


def _point_csv_frame(
    hi_points: gpd.GeoDataFrame,
    lo_points: gpd.GeoDataFrame,
    avg_points: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """Return the long-form CSV table with one row per output point.

    The CSV intentionally repeats cell metadata for each ``point_type``. That
    makes it easy to filter or pivot in spreadsheet tools without needing GIS
    software.
    """
    columns = [
        "cell_id",
        "cell_name",
        "u_index",
        "v_index",
        "grid_size",
        "offset_angle_deg",
        "point_type",
        "status",
        "valid_px",
        "elev_m",
        "elev_ft",
        "mean_elev_m",
        "mean_elev_ft",
        "avg_delta_m",
        "avg_delta_ft",
        "lon",
        "lat",
        "x",
        "y",
        "crs_authid",
        "dem_row",
        "dem_col",
    ]
    frames = []
    for points in (hi_points, lo_points, avg_points):
        frame = pd.DataFrame(points.drop(columns="geometry"))
        for column in columns:
            if column not in frame:
                frame[column] = pd.NA
        frames.append(frame[columns])
    if not frames:
        return pd.DataFrame(columns=columns)
    return pd.concat(frames, ignore_index=True)


def write_csv_output(
    csv_output_path: Path,
    *,
    hi_points: gpd.GeoDataFrame,
    lo_points: gpd.GeoDataFrame,
    avg_points: gpd.GeoDataFrame,
    overwrite: bool,
) -> None:
    """Write ``highgrid_out.csv`` or a caller-specified CSV output path."""
    if csv_output_path.exists():
        if not overwrite:
            raise ValueError(
                f"CSV output already exists; pass --overwrite: {csv_output_path}"
            )
        csv_output_path.unlink()

    csv_output_path.parent.mkdir(parents=True, exist_ok=True)
    frame = _point_csv_frame(hi_points, lo_points, avg_points)
    frame.to_csv(csv_output_path, index=False)


def _relative_qgis_path(path: Path, *, base_dir: Path) -> str:
    """Return a QGIS-friendly relative path with forward slashes."""
    relative = os.path.relpath(
        path.resolve(strict=False), base_dir.resolve(strict=False)
    )
    qgis_path = Path(relative).as_posix()
    if not qgis_path.startswith("../") and "/" not in qgis_path:
        return f"./{qgis_path}"
    return qgis_path


def _read_dem_crs(dem_path: Path) -> CRS:
    """Read and validate the DEM CRS."""
    if not dem_path.exists():
        raise ValueError(f"DEM file does not exist: {dem_path}")
    with rasterio.open(dem_path) as dataset:
        if dataset.crs is None:
            raise ValueError(f"DEM has no CRS: {dem_path}")
        return _coerce_crs(dataset.crs)


def _spatialrefsys_xml(crs: CRS) -> str:
    """Return a QGIS spatialrefsys XML fragment for a CRS."""
    authority = crs.to_authority()
    authid = f"{authority[0]}:{authority[1]}" if authority else crs.to_string()
    try:
        srid = int(authority[1]) if authority and authority[0].upper() == "EPSG" else 0
    except (TypeError, ValueError):
        srid = 0
    proj4 = ""
    projection = (
        crs.coordinate_operation.method_name if crs.coordinate_operation else ""
    )
    ellipsoid = crs.ellipsoid.name if crs.ellipsoid else ""
    return (
        '<spatialrefsys nativeFormat="Wkt">\n'
        f"      <wkt>{html.escape(crs.to_wkt(), quote=False)}</wkt>\n"
        f"      <proj4>{html.escape(proj4, quote=False)}</proj4>\n"
        f"      <srsid>{srid}</srsid>\n"
        f"      <srid>{srid}</srid>\n"
        f"      <authid>{html.escape(authid)}</authid>\n"
        f"      <description>{html.escape(crs.name or authid)}</description>\n"
        f"      <projectionacronym>{html.escape(projection)}</projectionacronym>\n"
        f"      <ellipsoidacronym>{html.escape(ellipsoid)}</ellipsoidacronym>\n"
        f"      <geographicflag>{str(bool(crs.is_geographic)).lower()}</geographicflag>\n"
        "    </spatialrefsys>"
    )


def _project_crs_xml(crs: CRS) -> str:
    """Return a QGIS project CRS block."""
    return f"<projectCrs>\n    {_spatialrefsys_xml(crs)}\n  </projectCrs>"


def _layer_srs_xml(crs: CRS) -> str:
    """Return a QGIS layer SRS block."""
    return f"<srs>\n    {_spatialrefsys_xml(crs)}\n  </srs>"


def _renderer_from_qml(path: Path) -> str:
    """Extract the renderer block from a QGIS QML style file."""
    text = path.read_text(encoding="utf-8")
    match = re.search(r"<renderer-v2\b.*?</renderer-v2>", text, flags=re.S)
    if match is None:
        raise ValueError(f"QML style has no renderer-v2 block: {path}")
    return match.group(0)


def _replace_layer_renderer(
    qgs_text: str, *, layer_name: str, renderer_xml: str
) -> str:
    """Replace a maplayer renderer for one GeoPackage layer."""
    pattern = re.compile(
        rf"(<maplayer\b(?:(?!</maplayer>).)*?<datasource>[^<]*\|layername={re.escape(layer_name)}</datasource>"
        rf"(?:(?!</maplayer>).)*?)(<renderer-v2\b.*?</renderer-v2>)",
        flags=re.S,
    )
    return pattern.sub(lambda match: match.group(1) + renderer_xml, qgs_text)


def _apply_generic_qml_renderers(qgs_text: str) -> str:
    """Apply bundled generic layer renderers to the QGIS project XML."""
    template_dir = default_project_template_path().parent
    style_by_layer = {
        "hi-points": template_dir / "hipnts.qml",
        "lo-points": template_dir / "lopnts.qml",
        "avg-points": template_dir / "avgpnts.qml",
        "highgrid_cells": template_dir / "grid_cells.qml",
    }
    for layer_name, style_path in style_by_layer.items():
        if not style_path.exists():
            raise ValueError(f"QGIS layer style template does not exist: {style_path}")
        qgs_text = _replace_layer_renderer(
            qgs_text,
            layer_name=layer_name,
            renderer_xml=_renderer_from_qml(style_path),
        )
    return qgs_text


def write_qgis_project(
    project_output_path: Path,
    *,
    gpkg_path: Path,
    dem_path: Path,
    dem_crs: CRS,
    overwrite: bool,
    template_path: Path | None = None,
) -> None:
    """Write a QGIS project archive that opens the generated highgrid layers."""
    if project_output_path.exists():
        if not overwrite:
            raise ValueError(
                f"QGIS project output already exists; pass --overwrite: {project_output_path}"
            )
        project_output_path.unlink()

    template = (
        template_path if template_path is not None else default_project_template_path()
    )
    if not template.exists():
        raise ValueError(f"QGIS project template does not exist: {template}")

    project_output_path.parent.mkdir(parents=True, exist_ok=True)
    gpkg_relative = _relative_qgis_path(gpkg_path, base_dir=project_output_path.parent)
    datasource_prefix = html.escape(gpkg_relative, quote=True)
    dem_relative = html.escape(
        _relative_qgis_path(dem_path, base_dir=project_output_path.parent),
        quote=True,
    )
    project_stem = project_output_path.stem
    gpkg_stem = gpkg_path.stem

    with zipfile.ZipFile(template, "r") as source_archive:
        qgs_names = [
            name for name in source_archive.namelist() if name.endswith(".qgs")
        ]
        if len(qgs_names) != 1:
            raise ValueError(
                f"QGIS project template must contain exactly one .qgs: {template}"
            )
        template_qgs_name = qgs_names[0]
        qgs_text = source_archive.read(template_qgs_name).decode("utf-8")
        qgs_text = re.sub(
            r"<projectCrs>.*?</projectCrs>",
            _project_crs_xml(dem_crs),
            qgs_text,
            flags=re.S,
        )
        qgs_text = re.sub(
            r"<srs>.*?</srs>", _layer_srs_xml(dem_crs), qgs_text, flags=re.S
        )
        for layer_name in (
            "lo-points",
            "hi-points",
            "avg-points",
            "highgrid_cells",
            "highgrid_corners",
            "highgrid_boundary",
        ):
            qgs_text = re.sub(
                rf'source="[^"]*?\.gpkg\|layername={re.escape(layer_name)}"',
                f'source="{datasource_prefix}|layername={layer_name}"',
                qgs_text,
            )
            qgs_text = re.sub(
                rf"<datasource>[^<]*?\.gpkg\|layername={re.escape(layer_name)}</datasource>",
                f"<datasource>{datasource_prefix}|layername={layer_name}</datasource>",
                qgs_text,
            )
        qgs_text = re.sub(
            r'source="[^"]*?\.(?:tif|tiff)"', f'source="{dem_relative}"', qgs_text
        )
        qgs_text = re.sub(
            r"<datasource>[^<]*?\.(?:tif|tiff)</datasource>",
            f"<datasource>{dem_relative}</datasource>",
            qgs_text,
        )
        qgs_text = qgs_text.replace("lenfgrid —", f"{gpkg_stem} —")
        qgs_text = qgs_text.replace("grid3x3 —", f"{gpkg_stem} —")
        qgs_text = qgs_text.replace("lenfgrid", gpkg_stem)
        qgs_text = qgs_text.replace("grid3x3", gpkg_stem)
        qgs_text = _apply_generic_qml_renderers(qgs_text)

        with zipfile.ZipFile(
            project_output_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as out_archive:
            out_archive.writestr(f"{project_stem}.qgs", qgs_text)
            for name in source_archive.namelist():
                if name == template_qgs_name:
                    continue
                out_archive.writestr(name, source_archive.read(name))


def run_highgrid(
    *,
    dem_path: Path | str = default_dem_path(),
    output_path: Path | str = default_output_path(),
    csv_output_path: Path | str | None = None,
    project_output_path: Path | str | None = None,
    write_project: bool = True,
    grid_size: int,
    boundary_path: Path | str | None = None,
    corners_text: str | None = None,
    input_crs: str | CRS | None = None,
    corner_names: str | None = "auto",
    parallelogram_tolerance_m: float = 30.0,
    offset_angle_deg: float = 0.0,
    all_touched: bool = False,
    overwrite: bool = False,
    progress: bool = False,
    progress_interval_s: float = 10.0,
) -> HighgridResult:
    """Run the complete highgrid workflow.

    This is the importable core used by both ``highpoints/highgrid.py`` and
    ``python -m highpoints high-grid``. It resolves defaults, loads corners,
    validates the unrotated parallelogram, applies any offset rotation, builds
    cells, samples the DEM, and writes both GeoPackage and CSV outputs.
    """
    if grid_size < 1 or grid_size > 100:
        raise ValueError("grid_size must be an integer from 1 to 100")

    dem = Path(dem_path)
    output = Path(output_path)
    csv_output = (
        Path(csv_output_path)
        if csv_output_path is not None
        else default_csv_output_path(output)
    )
    project_output = (
        Path(project_output_path)
        if project_output_path is not None
        else default_project_output_path(output)
    )
    if not write_project:
        project_output = None
    _check_output_paths(
        output_path=output,
        csv_output_path=csv_output,
        project_output_path=project_output,
        overwrite=overwrite,
    )
    if boundary_path is not None:
        boundary = Path(boundary_path)
    elif corners_text is None:
        boundary = default_corner_path()
    else:
        boundary = None
    dem_crs = _read_dem_crs(dem)
    resolved_input_crs = _coerce_crs(input_crs) if input_crs is not None else None

    corners = load_corners(
        boundary_path=boundary,
        corners_text=corners_text,
        target_crs=dem_crs,
        input_crs=resolved_input_crs,
        corner_names=corner_names,
    )
    residual = validate_parallelogram(corners, parallelogram_tolerance_m)
    corners = apply_offset_angle(corners, offset_angle_deg)
    cells = build_grid_cells(
        corners, grid_size, offset_angle_deg=float(offset_angle_deg)
    )
    cells, hi_points, lo_points, avg_points = attach_extreme_attributes(
        cells,
        dem_path=dem,
        all_touched=all_touched,
        progress=progress,
        progress_interval_s=progress_interval_s,
    )
    boundary_layer = build_boundary_layer(
        corners,
        grid_size=grid_size,
        dem_path=dem,
        boundary_path=boundary,
        residual_m=residual,
        offset_angle_deg=float(offset_angle_deg),
    )
    write_geopackage(
        output,
        cells=cells,
        hi_points=hi_points,
        lo_points=lo_points,
        avg_points=avg_points,
        boundary=boundary_layer,
        corners=corners,
        overwrite=overwrite,
    )
    write_csv_output(
        csv_output,
        hi_points=hi_points,
        lo_points=lo_points,
        avg_points=avg_points,
        overwrite=overwrite,
    )
    if project_output is not None:
        write_qgis_project(
            project_output,
            gpkg_path=output,
            dem_path=dem,
            dem_crs=dem_crs,
            overwrite=overwrite,
        )

    return HighgridResult(
        output_path=output,
        csv_output_path=csv_output,
        project_output_path=project_output,
        cell_count=len(cells),
        hi_point_count=len(hi_points),
        lo_point_count=len(lo_points),
        avg_point_count=len(avg_points),
        grid_size=grid_size,
    )


def run_from_namespace(args: argparse.Namespace) -> HighgridResult:
    """Adapter from argparse namespaces to the importable core function."""
    _confirm_expensive_run(
        grid_size=args.grid_size,
        assume_yes=bool(getattr(args, "yes", False)),
    )
    return run_highgrid(
        dem_path=Path(args.dem),
        output_path=Path(args.output),
        csv_output_path=Path(args.csv_output) if args.csv_output else None,
        project_output_path=Path(args.project_output) if args.project_output else None,
        write_project=not bool(args.no_project),
        grid_size=args.grid_size,
        boundary_path=Path(args.boundary) if args.boundary else None,
        corners_text=args.corners,
        input_crs=args.input_crs,
        corner_names=args.corner_names,
        parallelogram_tolerance_m=float(args.parallelogram_tolerance_m),
        offset_angle_deg=float(args.offset_angle),
        all_touched=bool(args.all_touched),
        overwrite=bool(args.overwrite),
        progress=not bool(getattr(args, "quiet", False)),
        progress_interval_s=float(getattr(args, "progress_interval", 10.0)),
    )


def _confirm_expensive_run(*, grid_size: int, assume_yes: bool) -> None:
    """Ask for confirmation before a very large CLI raster pass."""
    cell_count = grid_size * grid_size
    if assume_yes or cell_count < EXPENSIVE_CELL_CONFIRMATION_THRESHOLD:
        return

    message = (
        f"This run will process {cell_count:,} DEM cells. On this system that "
        "can take more than 5 minutes for large grids.\n"
        "Continue? Type 'yes' to start: "
    )
    if not sys.stdin.isatty():
        raise ValueError(
            f"Refusing expensive non-interactive run for {cell_count:,} cells; "
            "pass --yes to confirm."
        )
    try:
        answer = input(message)
    except EOFError as exc:
        raise ValueError(
            f"Refusing expensive run for {cell_count:,} cells without confirmation; "
            "pass --yes to confirm."
        ) from exc
    if answer.strip().casefold() != "yes":
        raise ValueError("Cancelled expensive highgrid run")


def build_parser(prog: str | None = None) -> argparse.ArgumentParser:
    """Build the standalone ``highgrid.py`` command-line parser."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "Find the highest, lowest, and mean-nearest DEM points in each "
            "cell of a parallelogram grid."
        ),
    )
    boundary = parser.add_mutually_exclusive_group()
    boundary.add_argument(
        "--boundary",
        help=(
            "Vector or CSV file containing four boundary corners "
            f"(default: {default_corner_path()})."
        ),
    )
    boundary.add_argument(
        "--corners",
        help="Inline W,N,E,S corners as 'x,y;x,y;x,y;x,y' in --input-crs.",
    )
    parser.add_argument("--grid-size", required=True, type=parse_grid_size)
    parser.add_argument(
        "--dem", default=str(default_dem_path()), help="DEM GeoTIFF path."
    )
    parser.add_argument(
        "--output",
        default=str(default_output_path()),
        help="Output GeoPackage path. Layers are written in the DEM CRS.",
    )
    parser.add_argument(
        "--csv-output",
        default=None,
        help="Output CSV path (default: highgrid_out.csv beside --output).",
    )
    parser.add_argument(
        "--project-output",
        default=None,
        help="Output QGIS project archive path (default: .qgz beside --output).",
    )
    parser.add_argument(
        "--no-project",
        action="store_true",
        help="Do not write a QGIS .qgz project archive.",
    )
    parser.add_argument(
        "--input-crs",
        default=None,
        help=(
            "CRS for CRS-less boundary CSV/inline coordinates. Defaults to "
            "the DEM CRS; vector files with their own CRS keep using it."
        ),
    )
    parser.add_argument(
        "--corner-names",
        default="auto",
        help="auto, or four comma-separated names in W,N,E,S order.",
    )
    parser.add_argument(
        "--parallelogram-tolerance-m",
        default=30.0,
        type=float,
        help="Maximum W+E/N+S parallelogram residual in DEM CRS units.",
    )
    parser.add_argument(
        "--offset-angle",
        default=0.0,
        type=float,
        help=(
            "Clockwise-positive degrees to rotate the working grid around "
            "the input boundary center."
        ),
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
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm very expensive grid runs without an interactive prompt.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress DEM progress messages.",
    )
    parser.add_argument(
        "--progress-interval",
        default=10.0,
        type=float,
        help="Seconds between DEM progress messages (default: 10).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point for direct script execution."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        result = run_from_namespace(args)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(result.output_path)
    print(f"csv: {result.csv_output_path}")
    if result.project_output_path is not None:
        print(f"qgis: {result.project_output_path}")
    print(f"cells: {result.cell_count}")
    print(f"hi-points: {result.hi_point_count}")
    print(f"lo-points: {result.lo_point_count}")
    print(f"avg-points: {result.avg_point_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
