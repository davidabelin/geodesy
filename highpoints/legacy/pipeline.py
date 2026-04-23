from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional


def _require_geopandas():
    try:
        import geopandas as gpd  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError("highpoints requires geopandas.") from e
    return gpd


def _require_shapely():
    try:
        from shapely.geometry import Point  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError("highpoints requires shapely.") from e
    return Point


def run_hidrant_peaks(
    *,
    dem_path: Path,
    buffer_m: float,
    separation_m: float,
    output_kml: Path,
    output_csv: Optional[Path],
) -> None:
    from .highpoints import get_top_ten_peaks_quadrant

    gpd = _require_geopandas()
    Point = _require_shapely()

    triads = [
        ["N", "W", "S"],  # W hidrant
        ["W", "S", "E"],  # S hidrant
        ["S", "E", "N"],  # E hidrant
        ["E", "N", "W"],  # N hidrant
    ]
    triad_to_sector = {
        "NWS": "We",
        "WSE": "So",
        "SEN": "Ea",
        "ENW": "No",
    }
    marker_colors = {
        "We": "#FF0000",
        "So": "#0000FF",
        "Ea": "#00FF00",
        "No": "#800080",
    }

    features: List[dict] = []
    for triad in triads:
        triad_key = "".join(triad)
        sector = triad_to_sector.get(triad_key, triad_key)
        peaks = get_top_ten_peaks_quadrant(
            str(dem_path), triad, buffer_m=float(buffer_m), separation_m=float(separation_m)
        )
        if not peaks:
            continue
        for peak in peaks:
            point_geom = Point(float(peak["lon"]), float(peak["lat"]))
            point_name = f"{sector}{int(peak['rank'])}"
            description = (
                f"Sector: {sector}\n"
                f"Rank: {int(peak['rank'])}\n"
                f"Elevation (m): {float(peak['alt']):.2f}\n"
                f"Elevation (ft): {float(peak['alt']) * 3.28084:.2f}"
            )
            props = {
                "name": point_name,
                "description": description,
                "sector": sector,
                "rank": int(peak["rank"]),
                "elevation_m": float(peak["alt"]),
                "marker-color": marker_colors.get(sector, "#000000"),
            }
            features.append({"geometry": point_geom, "properties": props})

    if not features:
        raise ValueError("No peaks found (no features to export).")

    gdf = gpd.GeoDataFrame(
        [f["properties"] for f in features],
        geometry=[f["geometry"] for f in features],
        crs="EPSG:4326",
    )
    output_kml.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(str(output_kml), driver="KML")

    if output_csv is not None:
        df = gdf.copy()
        df["Lat"] = df.geometry.y
        df["Lon"] = df.geometry.x
        df["Alt_ft"] = df["elevation_m"] * 3.28084
        out_cols = ["name", "Lat", "Lon", "Alt_ft", "sector", "rank", "elevation_m"]
        df.drop(columns=["geometry"])[out_cols].to_csv(str(output_csv), index=False)

