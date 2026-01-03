from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple


def require_simplekml():
    try:
        import simplekml  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "This command requires 'simplekml'. Install it with: pip install simplekml"
        ) from e
    return simplekml


def kml_name_default(input_path: Path) -> str:
    return input_path.name.rsplit(".", 1)[0]


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def rgba_floats_to_kml_abgr(r: float, g: float, b: float, a: float = 1.0) -> str:
    r_i = int(_clamp01(r) * 255)
    g_i = int(_clamp01(g) * 255)
    b_i = int(_clamp01(b) * 255)
    a_i = int(_clamp01(a) * 255)
    return f"{a_i:02x}{b_i:02x}{g_i:02x}{r_i:02x}"


_HEX6_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")
_HEX8_RE = re.compile(r"^#?([0-9a-fA-F]{8})$")
_FLOAT_RE = re.compile(r"[-+]?\d*\.\d+|\d+")


def kml_color_from_any(value: Any, *, default: str) -> str:
    if value is None or value == "":
        return default

    if isinstance(value, str):
        s = value.strip()
        m8 = _HEX8_RE.match(s)
        if m8:
            return m8.group(1).lower()
        m6 = _HEX6_RE.match(s)
        if m6:
            rrggbb = m6.group(1).lower()
            rr = int(rrggbb[0:2], 16) / 255.0
            gg = int(rrggbb[2:4], 16) / 255.0
            bb = int(rrggbb[4:6], 16) / 255.0
            return rgba_floats_to_kml_abgr(rr, gg, bb, 1.0)

        nums = _FLOAT_RE.findall(s)
        if len(nums) == 4:
            r, g, b, a = (float(n) for n in nums)
            return rgba_floats_to_kml_abgr(r, g, b, a)
        return default

    if isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            r, g, b, a = (float(v) for v in value)
        except Exception:
            return default
        return rgba_floats_to_kml_abgr(r, g, b, a)

    return default


def write_kml_lines(
    *,
    parent,
    name: str,
    coords_groups: List[List[Tuple[float, float]]],
    kml_color: str,
    line_width: float,
) -> None:
    simplekml = require_simplekml()
    style = simplekml.Style()
    style.linestyle.color = kml_color
    style.linestyle.width = float(line_width)

    if len(coords_groups) == 1:
        pm = parent.newlinestring(name=name)
        pm.coords = coords_groups[0]
        pm.altitudemode = simplekml.AltitudeMode.clamptoground
        pm.style = style
        return

    pm = parent.newmultigeometry(name=name)
    pm.altitudemode = simplekml.AltitudeMode.clamptoground
    pm.style = style
    for coords in coords_groups:
        pm.geoms.append(simplekml.LineString(coords=coords))

