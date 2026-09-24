"""Trace visible ink and propose, but never silently apply, gap repairs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from PIL import Image
from scipy.ndimage import binary_dilation, binary_propagation, label
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Point
from skimage.draw import line
from skimage.morphology import skeletonize

from .raster_io import (
    grid_profile,
    read_labels,
    read_rgb,
    source_window,
    write_json,
    write_raster,
)
from .report import evaluate_transects


def pixel_graph(skeleton):
    pixels = {tuple(map(int, p)) for p in np.argwhere(skeleton)}
    graph = {}
    for y, x in sorted(pixels):
        neighbors = []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                q = (y + dy, x + dx)
                if (not dx and not dy) or q not in pixels:
                    continue
                # Avoid diagonal shortcuts through an existing orthogonal corner.
                if dx and dy and ((y + dy, x) in pixels or (y, x + dx) in pixels):
                    continue
                neighbors.append(q)
        graph[(y, x)] = neighbors
    return graph


def trace_paths(graph):
    """Visit every graph edge once, splitting at endpoints/junctions and retaining rings."""
    used, paths = set(), []

    def walk(start, nxt):
        path = [start, nxt]
        used.add(tuple(sorted((start, nxt))))
        prev, current = start, nxt
        while len(graph[current]) == 2 and current != start:
            following = next(p for p in graph[current] if p != prev)
            edge = tuple(sorted((current, following)))
            if edge in used:
                break
            used.add(edge)
            path.append(following)
            prev, current = current, following
        return path

    for start in graph:
        if len(graph[start]) != 2:
            for nxt in graph[start]:
                if tuple(sorted((start, nxt))) not in used:
                    paths.append(walk(start, nxt))
    for start in graph:
        for nxt in graph[start]:
            if tuple(sorted((start, nxt))) not in used:
                paths.append(walk(start, nxt))
    return paths


def endpoint_tangent(graph, endpoint, length=8):
    prev, current = None, endpoint
    for _ in range(length):
        candidates = [p for p in graph[current] if p != prev]
        if len(candidates) != 1:
            break
        prev, current = current, candidates[0]
        if len(graph[current]) != 2:
            break
    direction = np.array(endpoint, dtype=float) - current
    norm = np.linalg.norm(direction)
    return direction / norm if norm >= 3 else None


def bridge_pixels(a, b):
    rr, cc = line(*a, *b)
    return list(zip(rr.tolist(), cc.tolist()))


def safe_bridge(pixels, a, b, skeleton, valid):
    rr, cc = np.array(pixels).T
    if not valid[rr, cc].all():
        return False
    # Inspect only a local box; never scan the whole image per proposal.
    y0, x0 = max(0, rr.min() - 1), max(0, cc.min() - 1)
    y1, x1 = min(skeleton.shape[0], rr.max() + 2), min(skeleton.shape[1], cc.max() + 2)
    occupied = skeleton[y0:y1, x0:x1].copy()
    yy, xx = np.mgrid[y0:y1, x0:x1]
    near_ends = ((yy - a[0]) ** 2 + (xx - a[1]) ** 2 <= 4) | (
        (yy - b[0]) ** 2 + (xx - b[1]) ** 2 <= 4
    )
    occupied[near_ends] = False
    occupied = binary_dilation(occupied, structure=np.ones((3, 3)))
    return not occupied[rr - y0, cc - x0].any()


def propose_repairs(skeleton, rgb, valid, max_gap=12, min_evidence=0.05):
    graph = pixel_graph(skeleton)
    endpoints = [p for p, ns in graph.items() if len(ns) == 1]
    if len(endpoints) < 2:
        return []
    tree = cKDTree(endpoints)
    tangents = {p: endpoint_tangent(graph, p) for p in endpoints}
    proposals = []
    cos_limit = np.cos(np.deg2rad(30))
    for i, j in sorted(tree.query_pairs(max_gap)):
        a, b = endpoints[i], endpoints[j]
        delta = np.array(b, dtype=float) - a
        distance = float(np.linalg.norm(delta))
        if distance <= 2 or tangents[a] is None or tangents[b] is None:
            continue
        alignment = min(
            float(tangents[a] @ (delta / distance)),
            float(tangents[b] @ (-delta / distance)),
        )
        if alignment < cos_limit:
            continue
        pixels = bridge_pixels(a, b)
        if not safe_bridge(pixels, a, b, skeleton, valid):
            continue
        rr, cc = np.array(pixels[1:-1]).T
        # Visible darkness is evidence only; printed clutter can also be dark.
        evidence = float(
            np.clip((235 - rgb[rr, cc, 0].astype(float)) / 160, 0, 1).mean()
        )
        if evidence < min_evidence:
            continue
        proposals.append(
            dict(
                id=f"gap-{a[0]}-{a[1]}-{b[0]}-{b[1]}",
                start=[a[1], a[0]],
                end=[b[1], b[0]],
                distance_px=distance,
                alignment=alignment,
                image_evidence=evidence,
            )
        )
    return proposals


def apply_repairs(skeleton, valid, proposals, accepted_ids):
    by_id = {p["id"]: p for p in proposals}
    if len(set(accepted_ids)) != len(accepted_ids) or set(accepted_ids) - set(by_id):
        raise ValueError(
            "Accepted repair IDs must be unique IDs from the current proposals"
        )
    result = skeleton.copy()
    inferred = np.zeros_like(skeleton)
    used = set()
    for ident in sorted(accepted_ids):
        rec = by_id[ident]
        a, b = tuple(reversed(rec["start"])), tuple(reversed(rec["end"]))
        if a in used or b in used:
            raise ValueError(
                "Accepted repairs conflict: an endpoint is used more than once"
            )
        pixels = bridge_pixels(a, b)
        if not safe_bridge(pixels, a, b, result, valid):
            raise ValueError("Accepted repairs intersect or touch other linework")
        rr, cc = np.array(pixels).T
        inferred[rr, cc] |= ~result[rr, cc]
        result[rr, cc] = True
        used.update((a, b))
    return result, inferred


def vector_frame(rows, crs, columns):
    if rows:
        return gpd.GeoDataFrame(rows, crs=crs)
    return gpd.GeoDataFrame({c: [] for c in columns}, geometry=[], crs=crs)


def trace(
    run_dir,
    out=None,
    labels=None,
    accepted=None,
    low=0.3,
    high=0.6,
    max_gap=12,
    min_evidence=0.05,
    transects=None,
):
    run_dir = Path(run_dir)
    out = Path(out) if out else run_dir / "trace"
    if not 0 <= low <= high <= 1 or max_gap < 3 or not 0 <= min_evidence <= 1:
        raise ValueError(
            "Require 0 <= low <= high <= 1, max-gap >= 3, and evidence in [0,1]"
        )
    with rasterio.open(run_dir / "source.tif") as src:
        area = source_window(src)
        profile = grid_profile(src, area)
        rgb, valid = read_rgb(src, area)
    classes = read_labels(labels or run_dir / "classes.tif", profile)
    valid &= classes != 255
    if labels:
        observed = (classes == 1) & valid
    else:
        with rasterio.open(run_dir / "probabilities.tif") as src:
            if (
                src.count != 4
                or src.crs != profile["crs"]
                or not src.transform.almost_equals(profile["transform"])
                or (src.height, src.width) != valid.shape
            ):
                raise ValueError("Probability grid differs from the run source")
            probability = src.read(2)
        weak = (probability >= low) & valid & np.isin(classes, [0, 1])
        observed = binary_propagation(
            (probability >= high) & weak, mask=weak, structure=np.ones((3, 3))
        )
    skeleton = skeletonize(observed)
    proposals = propose_repairs(skeleton, rgb, valid, max_gap, min_evidence)
    revision = hashlib.sha256(
        skeleton.tobytes()
        + valid.tobytes()
        + rgb.tobytes()
        + json.dumps(proposals, sort_keys=True).encode()
    ).hexdigest()
    accepted_ids = []
    if accepted:
        review = json.loads(Path(accepted).read_text(encoding="utf-8"))
        if review.get("revision") != revision:
            raise ValueError(
                "Repair review is stale: regenerate proposals and review the current extraction"
            )
        accepted_ids = review.get("accepted_ids", [])
        if not isinstance(accepted_ids, list) or not all(
            isinstance(i, str) for i in accepted_ids
        ):
            raise ValueError("accepted_ids must be a list of proposal IDs")
    final, inferred = apply_repairs(skeleton, valid, proposals, accepted_ids)
    graph = pixel_graph(final)
    paths = trace_paths(graph)
    endpoints = [p for p, ns in graph.items() if len(ns) == 1]
    junctions = [p for p, ns in graph.items() if len(ns) > 2]
    singletons = [p for p, ns in graph.items() if not ns]
    uncertain = np.zeros_like(final)
    for p in endpoints + junctions + singletons:
        uncertain[p] = True
    for p in proposals:
        rr, cc = np.array(
            bridge_pixels(tuple(reversed(p["start"])), tuple(reversed(p["end"])))
        ).T
        uncertain[rr, cc] = True
    uncertainty = binary_dilation(uncertain | inferred | ~valid, iterations=3)
    checks = (
        evaluate_transects(
            final,
            uncertainty,
            json.loads(Path(transects).read_text(encoding="utf-8")),
            paths,
        )
        if transects
        else []
    )
    out.mkdir(parents=True, exist_ok=True)
    for name, mask in (
        ("observed_ink", observed),
        ("observed_skeleton", skeleton),
        ("contour_mask", final),
        ("inferred_mask", inferred),
        ("ambiguity", uncertainty),
    ):
        write_raster(out / f"{name}.tif", mask.astype("uint8"), profile, valid=valid)
    transform = profile["transform"]

    def world(p):
        return transform * (p[1] + 0.5, p[0] + 0.5)

    rows = []
    junction_set = set(junctions)
    for i, path in enumerate(paths):
        rr, cc = np.array(path).T
        rows.append(
            dict(
                fragment_id=i,
                closed=path[0] == path[-1],
                repaired=bool(inferred[rr, cc].any()),
                junction=any(p in junction_set for p in (path[0], path[-1])),
                length_px=float(np.linalg.norm(np.diff(path, axis=0), axis=1).sum()),
                geometry=LineString([world(p) for p in path]),
            )
        )
    gpkg = out / "lines.gpkg"
    # Replace only this generated package; avoid leaving stale layers from prior tracing.
    if gpkg.exists():
        gpkg.unlink()
    vector_frame(
        rows,
        profile["crs"],
        ["fragment_id", "closed", "repaired", "junction", "length_px"],
    ).to_file(
        gpkg,
        layer="fragments",
        driver="GPKG",
        engine="pyogrio",
        geometry_type="LineString",
    )
    repairs = [
        dict(
            proposal_id=p["id"],
            accepted=p["id"] in accepted_ids,
            evidence=p["image_evidence"],
            geometry=LineString(
                [world(tuple(reversed(p["start"]))), world(tuple(reversed(p["end"])))]
            ),
        )
        for p in proposals
    ]
    vector_frame(
        repairs, profile["crs"], ["proposal_id", "accepted", "evidence"]
    ).to_file(
        gpkg,
        layer="repair_proposals",
        driver="GPKG",
        engine="pyogrio",
        geometry_type="LineString",
    )
    points = [
        dict(
            kind=kind,
            boundary=p[0] in (0, final.shape[0] - 1) or p[1] in (0, final.shape[1] - 1),
            geometry=Point(world(p)),
        )
        for kind, pixels in (
            ("endpoint", endpoints),
            ("junction", junctions),
            ("isolated", singletons),
        )
        for p in pixels
    ]
    vector_frame(points, profile["crs"], ["kind", "boundary"]).to_file(
        gpkg,
        layer="diagnostics",
        driver="GPKG",
        engine="pyogrio",
        geometry_type="Point",
    )
    overlay = rgb.copy()
    overlay[binary_dilation(skeleton)] = (20, 210, 225)
    overlay[binary_dilation(inferred)] = (255, 170, 0)
    overlay[binary_dilation(uncertain, iterations=2)] = (240, 40, 180)
    overlay[inferred] = (255, 170, 0)
    rgba = np.dstack([overlay, valid.astype("uint8") * 255])
    Image.fromarray(rgba).save(out / "qa_overlay.png")
    write_raster(out / "qa_overlay.tif", rgba.transpose(2, 0, 1), profile, rgba=True)
    write_json(
        out / "repair_proposals.json", dict(revision=revision, proposals=proposals)
    )
    write_json(
        out / "repair_review.template.json", dict(revision=revision, accepted_ids=[])
    )
    _, components = label(final, structure=np.ones((3, 3)))
    check_status = "not_run"
    if checks:
        check_status = (
            "failed"
            if any(c["passed"] is False for c in checks)
            else ("needs_review" if any(c["ambiguous"] for c in checks) else "passed")
        )
    report = dict(
        fragment_count=len(paths),
        connected_components=int(components),
        endpoints=len(endpoints),
        junction_pixels=len(junctions),
        isolated_pixels=len(singletons),
        proposed_repairs=len(proposals),
        accepted_repairs=len(accepted_ids),
        revision=revision,
        observed_pixels=int(skeleton.sum()),
        inferred_pixels=int(inferred.sum()),
        labels_source=str(Path(labels).resolve()) if labels else "model_predictions",
        accepted_ids=accepted_ids,
        transects=checks,
        transect_status=check_status,
        elevation_data_used=False,
        status="requires_review",
        contour_count=None,
        note="Fragment/component counts are not contour counts. Junctions may be merges. Heights and direction are unassigned.",
    )
    write_json(out / "report.json", report)
    return out
