from __future__ import annotations


def require_geopandas():
    try:
        import geopandas as gpd  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "This command requires 'geopandas' (+ shapely). Install geopandas to run it."
        ) from e
    return gpd


def require_shapely_ops():
    try:
        from shapely.geometry import GeometryCollection, LineString, MultiLineString  # type: ignore
        from shapely.ops import linemerge  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError("This command requires 'shapely'.") from e
    return GeometryCollection, LineString, MultiLineString, linemerge


def require_pyproj_geod():
    try:
        from pyproj import Geod  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError("This command requires 'pyproj'.") from e
    return Geod


def try_matplotlib():
    try:
        import matplotlib.cm as cm  # type: ignore
        import matplotlib.colors as mcolors  # type: ignore
    except Exception:
        return None
    return cm, mcolors

