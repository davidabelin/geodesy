"""Image-grounded metrics; fragment totals never mean contour totals."""

import numpy as np
from shapely.geometry import LineString
from shapely.ops import unary_union
from skimage.draw import line


def confusion(predicted, reference):
    valid = reference != 255
    return np.bincount(
        (reference[valid] * 4 + predicted[valid]).astype(int), minlength=16
    ).reshape(4, 4)


def metrics(matrix):
    matrix = np.asarray(matrix)
    tp = np.diag(matrix)
    union = matrix.sum(0) + matrix.sum(1) - tp
    pden, rden = int(matrix[:, 1].sum()), int(matrix[1].sum())
    precision = float(tp[1] / pden) if pden else 0.0
    recall = float(tp[1] / rden) if rden else 0.0
    return dict(
        contour_precision=precision,
        contour_recall=recall,
        contour_f1=(
            2 * precision * recall / (precision + recall) if precision + recall else 0.0
        ),
        class_iou=[float(t / u) if u else None for t, u in zip(tp, union)],
        evaluated_pixels=int(matrix.sum()),
    )


def evaluate_transects(mask, ambiguity, transects, paths=None):
    """Count geometric crossings of traced pixel-center line segments.

    Coordinates are local to the run, not the uncropped source. Tangencies,
    endpoints, junctions, unknown labels and repairs are excluded from readable
    acceptance. Counts are encounters, not globally distinct contours.
    """
    if paths is None:
        from .topology import pixel_graph, trace_paths

        paths = trace_paths(pixel_graph(mask))
    curves = [LineString([(x, y) for y, x in path]) for path in paths]
    results = []
    h, w = mask.shape
    for item in transects:
        start, end = item["start"], item["end"]
        if (
            len(start) != 2
            or len(end) != 2
            or any(type(v) is not int for v in start + end)
        ):
            raise ValueError("Transect coordinates must be integer [x,y] pixel centers")
        if (
            any(not (0 <= x < w and 0 <= y < h) for x, y in (start, end))
            or start == end
        ):
            raise ValueError("Transect endpoints must be distinct and inside the run")
        if (
            type(item.get("expected")) is not int
            or item["expected"] < 0
            or type(item.get("readable")) is not bool
        ):
            raise ValueError(
                "Transects require nonnegative expected count and boolean readable"
            )
        rr, cc = line(start[1], start[0], end[1], end[0])
        segment = LineString([start, end])
        parts = []
        tangent = False

        def hit_points(geometry):
            if geometry.is_empty:
                return []
            if geometry.geom_type == "Point":
                return [geometry]
            if hasattr(geometry, "geoms"):
                return [
                    point for child in geometry.geoms for point in hit_points(child)
                ]
            return []

        for curve in curves:
            if not curve.intersects(segment):
                continue
            hit = curve.intersection(segment)
            parts.append(hit)
            for point in hit_points(hit):
                distance = curve.project(point)
                before, after = distance - 0.25, distance + 0.25
                if curve.is_ring:
                    before, after = before % curve.length, after % curve.length
                else:
                    before, after = max(0, before), min(curve.length, after)
                a, b = curve.interpolate(before), curve.interpolate(after)
                dx, dy = end[0] - start[0], end[1] - start[1]
                side_a = (a.x - start[0]) * dy - (a.y - start[1]) * dx
                side_b = (b.x - start[0]) * dy - (b.y - start[1]) * dx
                tangent |= side_a * side_b >= -1e-10
        hits = unary_union(parts)

        def intersections(geometry):
            if geometry.is_empty:
                return 0, False
            if geometry.geom_type == "Point":
                return 1, False
            if geometry.geom_type in ("LineString", "LinearRing"):
                return 0, True
            count, overlaps = 0, False
            for child in geometry.geoms:
                n, along_line = intersections(child)
                count += n
                overlaps |= along_line
            return count, overlaps

        count, overlaps = intersections(hits)
        ambiguous = not item["readable"] or bool(ambiguity[rr, cc].any())
        ambiguous |= overlaps or tangent
        results.append(
            dict(
                id=item.get("id", len(results)),
                expected=item["expected"],
                observed=count,
                ambiguous=ambiguous,
                passed=(count == item["expected"]) if not ambiguous else None,
            )
        )
    return results
