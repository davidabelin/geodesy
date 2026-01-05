from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .kml import kml_color_from_any, kml_name_default, require_simplekml, write_kml_lines


@dataclass(frozen=True)
class GeoJsonLineFeature:
    name: str
    coords_groups: List[List[Tuple[float, float]]]
    properties: Dict[str, Any]


def _extract_lines(feature: Dict[str, Any]) -> Optional[List[List[Tuple[float, float]]]]:
    geom = feature.get("geometry") or {}
    geom_type = geom.get("type")
    coords = geom.get("coordinates")
    if not geom_type or coords is None:
        return None
    if geom_type == "LineString":
        return [[(float(c[0]), float(c[1])) for c in coords]]
    if geom_type == "MultiLineString":
        groups: List[List[Tuple[float, float]]] = []
        for line in coords:
            groups.append([(float(c[0]), float(c[1])) for c in line])
        return groups
    return None


def _pick_name(props: Dict[str, Any], name_field: str) -> str:
    if name_field in props and props.get(name_field):
        return str(props[name_field])
    if "ROUTENAME" in props and props.get("ROUTENAME"):
        return str(props["ROUTENAME"])
    return "Feature"


def export_geojson_to_kml(
    geojson_path: Path,
    kml_path: Path,
    *,
    name_field: str = "route",
    color_field: str = "color",
    default_color: str = "ff0000ff",
    line_width: float = 1.0,
    doc_name: Optional[str] = None,
    group_by: Optional[str] = None,
) -> None:
    simplekml = require_simplekml()
    geojson_path = Path(geojson_path)
    kml_path = Path(kml_path)

    with geojson_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    parsed: List[GeoJsonLineFeature] = []
    for feat in data.get("features", []):
        props = feat.get("properties") or {}
        lines = _extract_lines(feat)
        if not lines:
            continue
        parsed.append(
            GeoJsonLineFeature(
                name=_pick_name(props, name_field=name_field),
                coords_groups=lines,
                properties=dict(props),
            )
        )

    kml_doc_name = doc_name or kml_name_default(geojson_path)
    kml = simplekml.Kml(name=kml_doc_name)

    groups: Dict[str, List[GeoJsonLineFeature]] = defaultdict(list)
    if group_by:
        for ftr in parsed:
            key = str(ftr.properties.get(group_by) or ftr.name)
            groups[key].append(ftr)
    else:
        groups[""] = parsed

    for group_name, feats in groups.items():
        parent = kml.newfolder(name=group_name) if group_name else kml
        for ftr in feats:
            raw_color = ftr.properties.get(color_field)
            color = kml_color_from_any(raw_color, default=default_color)
            write_kml_lines(
                parent=parent,
                name=ftr.name,
                coords_groups=ftr.coords_groups,
                kml_color=color,
                line_width=line_width,
            )

    kml_path.parent.mkdir(parents=True, exist_ok=True)
    kml.save(str(kml_path))

