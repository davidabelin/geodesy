from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple


def require_simplekml():
    try:
        import simplekml  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "This command requires 'simplekml'. Install it with: pip install simplekml"
        ) from e
    return simplekml


def generate_kml_from_csv(*, csv_path: Path, output_kml: Path, include_points: bool = False) -> None:
    simplekml = require_simplekml()

    grouped: Dict[str, List[Dict[str, object]]] = defaultdict(list)

    with Path(csv_path).open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not all(h in reader.fieldnames for h in ["LOC", "LAT", "LON"]):
            raise ValueError(f"CSV must have headers: LOC, LAT, LON (found: {reader.fieldnames})")

        auto_suffix: Dict[str, int] = defaultdict(int)
        for i, row in enumerate(reader, start=2):
            loc = (row.get("LOC") or "").strip()
            lat_s = (row.get("LAT") or "").strip()
            lon_s = (row.get("LON") or "").strip()
            if not loc or not lat_s or not lon_s:
                continue

            try:
                lat = float(lat_s)
                lon = float(lon_s)
            except ValueError:
                raise ValueError(f"Invalid lat/lon on row {i}: LAT={lat_s!r} LON={lon_s!r}") from None

            prefix: str
            suffix: int

            m = re.match(r"(.*)-(\d+)$", loc)
            if m:
                prefix = m.group(1)
                suffix = int(m.group(2))
            else:
                m = re.match(r"(.*?)(\d+)$", loc)
                if m:
                    prefix = m.group(1) or loc
                    suffix = int(m.group(2))
                else:
                    prefix = loc
                    suffix = auto_suffix[prefix]
                    auto_suffix[prefix] += 1

            grouped[prefix].append({"id": loc, "num": suffix, "lat": lat, "lon": lon})

    kml = simplekml.Kml(name=str(Path(csv_path).name))
    base_colors = [
        "0000FF",
        "00FF00",
        "FF0000",
        "00FFFF",
        "FF00FF",
        "AAAA00",
        "00A5FF",
        "500050",
        "2A2AA5",
        "32CD32",
        "A9A0DD",
        "505000",
        "005050",
    ]
    line_colors = [f"AA{c}" for c in base_colors]
    icon_colors = [f"FF{c}" for c in base_colors]

    group_names = sorted([k for k in grouped.keys() if k], key=str.casefold)
    for idx, prefix in enumerate(group_names):
        points = sorted(grouped[prefix], key=lambda p: int(p["num"]))  # type: ignore[arg-type]
        if not points:
            continue

        folder = kml.newfolder(name=str(prefix))
        line_color = line_colors[idx % len(line_colors)]
        icon_color = icon_colors[idx % len(icon_colors)]

        if include_points:
            pf = folder.newfolder(name="points")
            style = simplekml.Style()
            style.iconstyle.color = icon_color
            style.iconstyle.icon.href = "http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png"
            for p in points:
                pm = pf.newpoint(name=str(p["id"]))
                pm.coords = [(float(p["lon"]), float(p["lat"]))]
                pm.altitudemode = simplekml.AltitudeMode.clamptoground
                pm.style = style

        coords: List[Tuple[float, float]] = [(float(p["lon"]), float(p["lat"])) for p in points]
        if len(coords) >= 2:
            ls = folder.newlinestring(name="sequence")
            ls.coords = coords
            ls.altitudemode = simplekml.AltitudeMode.clamptoground
            ls.style.linestyle.color = line_color
            ls.style.linestyle.width = 1.0

    output_kml.parent.mkdir(parents=True, exist_ok=True)
    kml.save(str(output_kml))

