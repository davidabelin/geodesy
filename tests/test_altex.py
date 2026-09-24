"""Image-grounded extraction tests, with no terrain/elevation fixtures."""

from __future__ import annotations

import json

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from PIL import Image
from rasterio.transform import from_origin
from skimage.draw import circle_perimeter

from altex.cli import main, parser
from altex.heuristics import save_labels
from altex.raster_io import read_labels, write_json, write_raster
from altex.report import confusion, evaluate_transects, metrics
from altex.synth import make_tile
from altex.tiles import export_tiles
from altex.topology import (
    apply_repairs,
    pixel_graph,
    propose_repairs,
    trace,
    trace_paths,
)


@pytest.fixture
def cpu_torch():
    torch = pytest.importorskip("torch")
    original = torch.get_num_threads()
    torch.set_num_threads(1)
    yield torch
    torch.set_num_threads(original)


def image_file(path, width=128, height=128, transparent=False):
    rgba = np.zeros((4, height, width), dtype="uint8")
    rgba[:3] = np.array([249, 192, 122])[:, None, None]
    rgba[3] = 255
    if transparent:
        rgba[3, :5, :] = 0
    profile = dict(
        width=width,
        height=height,
        crs="EPSG:26985",
        transform=from_origin(100, 500, 1.3, 1.3),
    )
    write_raster(path, rgba, profile, rgba=True)
    return profile


def run_fixture(tmp_path, mask):
    run = tmp_path / "run"
    run.mkdir()
    profile = image_file(run / "source.tif", width=mask.shape[1], height=mask.shape[0])
    write_raster(run / "classes.tif", mask.astype("uint8"), profile, nodata=255)
    probs = np.zeros((4, *mask.shape), dtype="float32")
    probs[0] = ~mask
    probs[1] = mask
    write_raster(run / "probabilities.tif", probs, profile)
    return run, profile


def test_nested_rings_remain_separate():
    mask = np.zeros((100, 100), dtype=bool)
    for radius in (12, 24, 36):
        rr, cc = circle_perimeter(50, 50, radius)
        mask[rr, cc] = True
    paths = trace_paths(pixel_graph(mask))
    assert len(paths) == 3
    assert all(p[0] == p[-1] for p in paths)


def test_parallel_lines_do_not_merge_or_propose_cross_connections():
    mask = np.zeros((50, 60), dtype=bool)
    mask[20, 8:51] = mask[24, 8:51] = True
    assert len(trace_paths(pixel_graph(mask))) == 2
    assert (
        propose_repairs(mask, np.zeros((50, 60, 3), dtype="uint8"), np.ones_like(mask))
        == []
    )


def test_gap_is_only_repaired_by_explicit_id():
    mask = np.zeros((50, 60), dtype=bool)
    mask[25, 5:26] = True
    mask[25, 31:55] = True
    rgb, valid = np.zeros((50, 60, 3), dtype="uint8"), np.ones_like(mask)
    proposals = propose_repairs(mask, rgb, valid)
    assert len(proposals) == 1
    raw, inferred = apply_repairs(mask, valid, proposals, [])
    assert np.array_equal(raw, mask) and not inferred.any()
    repaired, inferred = apply_repairs(mask, valid, proposals, [proposals[0]["id"]])
    assert len(trace_paths(pixel_graph(repaired))) == 1
    assert inferred.sum() == 5


def test_bridge_cannot_cross_or_touch_another_line():
    mask = np.zeros((60, 60), dtype=bool)
    mask[25, 5:26] = mask[25, 33:55] = True
    mask[15:36, 29] = True
    valid = np.ones_like(mask)
    assert not propose_repairs(mask, np.zeros((60, 60, 3), dtype="uint8"), valid)
    proposal = dict(id="unsafe", start=[25, 25], end=[33, 25])
    with pytest.raises(ValueError, match="intersect"):
        apply_repairs(mask, valid, [proposal], ["unsafe"])


def test_bridge_does_not_cross_transparency():
    mask = np.zeros((50, 60), dtype=bool)
    mask[25, 5:26] = mask[25, 31:55] = True
    valid = np.ones_like(mask)
    valid[:, 28] = False
    assert not propose_repairs(mask, np.zeros((50, 60, 3), dtype="uint8"), valid)


def test_shared_endpoint_repairs_rejected():
    mask = np.zeros((50, 60), dtype=bool)
    mask[25, 5:26] = mask[25, 31:55] = True
    proposals = [
        dict(id="a", start=[25, 25], end=[31, 25]),
        dict(id="b", start=[25, 25], end=[32, 26]),
    ]
    with pytest.raises(ValueError, match="endpoint"):
        apply_repairs(mask, np.ones_like(mask), proposals, ["a", "b"])


def test_junctions_split_paths():
    mask = np.zeros((40, 40), dtype=bool)
    mask[20, 5:36] = True
    mask[5:21, 20] = True
    graph = pixel_graph(mask)
    assert len(graph[(20, 20)]) == 3
    assert len(trace_paths(graph)) == 3


def test_trace_exports_grid_fragments_and_crossing_diagnostics(tmp_path):
    mask = np.zeros((64, 64), dtype=bool)
    mask[20, 5:59] = mask[30, 5:59] = True
    run, profile = run_fixture(tmp_path, mask)
    transects = tmp_path / "transects.json"
    write_json(
        transects,
        [
            dict(id="clear", start=[32, 10], end=[32, 40], expected=2, readable=True),
            dict(
                id="unreadable",
                start=[32, 10],
                end=[32, 40],
                expected=2,
                readable=False,
            ),
        ],
    )
    result = trace(run, transects=transects)
    report = json.loads((result / "report.json").read_text())
    assert report["fragment_count"] == 2 and report["contour_count"] is None
    assert report["transects"][0]["passed"] is True
    assert report["transects"][1]["passed"] is None
    lines = gpd.read_file(result / "lines.gpkg", layer="fragments")
    assert len(lines) == 2 and lines.crs == profile["crs"]
    assert lines.geometry.iloc[0].coords[0] == pytest.approx(
        profile["transform"] * (5.5, 20.5)
    )
    with rasterio.open(result / "contour_mask.tif") as src:
        assert src.transform == profile["transform"]
        assert np.array_equal(src.read(1), mask)


def test_corrected_labels_override_prediction_and_reject_shifted_grid(tmp_path):
    mask = np.zeros((64, 64), dtype=bool)
    run, profile = run_fixture(tmp_path, mask)
    corrected = np.zeros((64, 64), dtype="uint8")
    corrected[20, 5:59] = 1
    corrected[30, 5:59] = 3  # A stream is deliberately excluded.
    path = tmp_path / "corrected.tif"
    write_raster(path, corrected, profile)
    result = trace(run, labels=path)
    assert json.loads((result / "report.json").read_text())["fragment_count"] == 1
    profile["transform"] = from_origin(200, 400, 1, 1)
    write_raster(path, corrected, profile)
    with pytest.raises(ValueError, match="match the run"):
        trace(run, labels=path)


def test_stale_repair_review_rejected(tmp_path):
    run, _ = run_fixture(tmp_path, np.zeros((64, 64), dtype=bool))
    review = tmp_path / "review.json"
    write_json(review, dict(revision="old", accepted_ids=[]))
    with pytest.raises(ValueError, match="stale"):
        trace(run, accepted=review)


def test_palette_round_trip_and_rgb_labels_rejected(tmp_path):
    labels = np.array([[0, 1, 2, 3, 255]], dtype="uint8")
    path = tmp_path / "label.png"
    save_labels(path, labels)
    assert np.array_equal(read_labels(path), labels)
    Image.new("RGB", (5, 1)).save(path)
    with pytest.raises(ValueError, match="indexed"):
        read_labels(path)


def test_procedural_labels_are_reproducible_and_include_clutter():
    first, labels = make_tile(128, 3)
    second, labels2 = make_tile(128, 3)
    assert np.array_equal(first, second) and np.array_equal(labels, labels2)
    assert set(np.unique(labels)) >= {0, 1, 3}
    assert not np.array_equal(first, make_tile(128, 4)[0])


def test_geographic_split_and_review_flags(tmp_path):
    from altex.data import read_manifest

    source = tmp_path / "map.tif"
    image_file(source, 256, 128)
    result = export_tiles(source, tmp_path / "real", count=8, size=64)
    records = read_manifest(result)
    assert len(records) == 8
    assert not any(r["reviewed"] for r in records)
    manifest = json.loads((result / "manifest.json").read_text())
    train = next(r for r in manifest["tiles"] if r["split"] == "train")
    val = next(r for r in manifest["tiles"] if r["split"] == "validation")
    val["window"] = train["window"]
    write_json(result / "manifest.json", manifest)
    with pytest.raises(ValueError, match="overlap"):
        read_manifest(result)


def test_metrics_ignore_unknown_and_count_separation():
    truth = np.array([[0, 1, 1, 255]], dtype="uint8")
    pred = np.array([[0, 1, 0, 1]], dtype="uint8")
    result = metrics(confusion(pred, truth))
    assert result["evaluated_pixels"] == 3
    assert result["contour_precision"] == 1
    assert result["contour_recall"] == 0.5


def test_cli_no_elevation_arguments_and_errors(tmp_path):
    for command in ("synth", "tiles", "train", "segment", "trace"):
        with pytest.raises(SystemExit) as exc:
            parser().parse_args([command, "--help"])
        assert exc.value.code == 0
    with pytest.raises(SystemExit):
        parser().parse_args(["train", "--dem", "modern.tif"])
    assert main(["trace", "--run-dir", str(tmp_path / "missing")]) == 2


def test_overlap_blending_transparency_and_aoi(tmp_path, monkeypatch, cpu_torch):
    torch = cpu_torch
    from altex import infer

    class Pointwise(torch.nn.Module):
        def forward(self, x):
            return torch.cat([x, x[:, :1] * 0], dim=1)

    monkeypatch.setattr(infer, "load_model", lambda *args: Pointwise())
    source = tmp_path / "map.tif"
    profile = image_file(source, 100, 80, transparent=True)
    out = infer.segment(
        source,
        "unused",
        tmp_path / "seg",
        aoi=(7, 0, 85, 73),
        tile=32,
        overlap=9,
        device="cpu",
    )
    with rasterio.open(out / "probabilities.tif") as src:
        data = src.read()
        assert src.transform == profile["transform"] * rasterio.Affine.translation(7, 0)
        assert not src.dataset_mask()[:5].any()
        assert np.allclose(data[:, 6:, :], data[:, 6:7, :1], atol=1e-6)
        assert np.allclose(data[:, 6:].sum(0), 1, atol=1e-6)
    with rasterio.open(out / "classes.tif") as src:
        assert np.all(src.read(1)[:5] == 255)


def test_training_and_inference_smoke(tmp_path):
    torch = pytest.importorskip("torch")
    from altex.train import train
    from altex.infer import segment

    original_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        model = train(
            tmp_path / "model",
            epochs=1,
            tiles_per_epoch=2,
            size=32,
            batch=2,
            width=2,
            device="cpu",
        )
        report = json.loads((model / "training.json").read_text())
        assert report["validation_status"] == "NO_REVIEWED_HAWKINS_VALIDATION"
        assert (model / "best.pt").is_file()
        source = tmp_path / "map.tif"
        image_file(source, 40, 40)
        run = segment(
            source,
            model / "best.pt",
            tmp_path / "segment",
            tile=32,
            overlap=8,
            device="cpu",
        )
        result = trace(run)
        assert (result / "report.json").is_file()
    finally:
        torch.set_num_threads(original_threads)


def test_diagonal_transect_counts_crossing_between_pixel_centers():
    mask = np.zeros((8, 8), dtype=bool)
    mask[2, 2] = mask[3, 3] = True
    result = evaluate_transects(
        mask,
        np.zeros_like(mask),
        [dict(start=[2, 3], end=[3, 2], expected=1, readable=True)],
    )
    assert result[0]["observed"] == 1 and result[0]["passed"]


def test_along_line_transect_is_ambiguous():
    mask = np.zeros((40, 40), dtype=bool)
    mask[20, 5:35] = True
    result = evaluate_transects(
        mask,
        np.zeros_like(mask),
        [dict(start=[10, 20], end=[30, 20], expected=1, readable=True)],
    )
    assert result[0]["ambiguous"] and result[0]["passed"] is None


def test_tangent_transect_is_ambiguous():
    mask = np.zeros((40, 40), dtype=bool)
    path = [(20, 10), (10, 20), (20, 30)]
    result = evaluate_transects(
        mask,
        np.zeros_like(mask),
        [dict(start=[5, 10], end=[35, 10], expected=1, readable=True)],
        paths=[path],
    )
    assert result[0]["observed"] == 1
    assert result[0]["ambiguous"] and result[0]["passed"] is None


def test_repaired_ring_and_exported_provenance(tmp_path):
    mask = np.zeros((64, 64), dtype=bool)
    mask[15, 10:54] = mask[49, 10:54] = True
    mask[15:50, 10] = mask[15:50, 53] = True
    mask[15, 28:33] = False
    run, _ = run_fixture(tmp_path, mask)
    result = trace(run, min_evidence=0)
    proposals = json.loads((result / "repair_proposals.json").read_text())
    assert len(proposals["proposals"]) == 1
    review = tmp_path / "accepted.json"
    write_json(
        review,
        dict(
            revision=proposals["revision"],
            accepted_ids=[proposals["proposals"][0]["id"]],
        ),
    )
    result = trace(run, min_evidence=0, accepted=review)
    report = json.loads((result / "report.json").read_text())
    assert report["accepted_repairs"] == 1 and report["inferred_pixels"] == 5
    rows = gpd.read_file(result / "lines.gpkg", layer="fragments")
    assert len(rows) == 1 and rows.iloc[0]["closed"] and rows.iloc[0]["repaired"]
