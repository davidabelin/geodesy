from __future__ import annotations

from pathlib import Path

from .._paths import data_dir
from ._common import require_geopandas


def build_centerlines_gpkg(
    *,
    input_path: Path,
    output_path: Path,
    layer: str = "centerlines",
    overwrite: bool = False,
) -> Path:
    """
    Convert Street Centerlines input (SHP) to a canonical GeoPackage.
    """
    gpd = require_geopandas()
    input_path = Path(input_path)
    output_path = Path(output_path)

    if output_path.exists() and not overwrite:
        raise ValueError(f"Output already exists: {output_path} (use --overwrite)")

    gdf = gpd.read_file(str(input_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(str(output_path), layer=layer, driver="GPKG")
    return output_path


def default_centerlines_shp() -> Path:
    return data_dir() / "DC_Street_Centerlines/Street_Centerlines_2013.shp"


def default_centerlines_gpkg() -> Path:
    return data_dir() / "centerlines.gpkg"

