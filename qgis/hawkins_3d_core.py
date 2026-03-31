"""Core Hawkins terrain-reconstruction workflow for QGIS Desktop.

This module is intentionally opinionated about the runtime and the current data
sources in this repository:

- the authoritative Hawkins source is ``Hawkins_Topography.img``
- execution is expected through the OSGeo4W / QGIS Desktop Python runtime
- pilot mode works in a smaller AOI and now produces auto-extracted candidate
  contour lines for review
- full mode is still conservative and does not auto-extract across the entire
  map extent because the current heuristics are meant for pilot-scale review

The pipeline output is a work package GeoPackage plus one or more QGIS project
files. The GeoPackage separates editable/manual layers from auto-generated guide
layers so that candidate extraction can improve without overwriting vetted work.
"""

from __future__ import annotations

import json
import math
import re
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy import ndimage as ndi

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from qgis_runtime import configure_proj_env, find_repo_root, init_qgis_app, shutdown_qgis_app

proj_data_dir = configure_proj_env(CURRENT_DIR)

from osgeo import gdal, ogr, osr
import qgis._core as qcore
from qgis import _3d as q3d
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtXml import QDomDocument
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFillSymbol,
    QgsLineSymbol,
    QgsProject,
    QgsRasterLayer,
    QgsReadWriteContext,
    QgsRectangle,
    QgsSingleSymbolRenderer,
    QgsVector3D,
    QgsVectorLayer,
)

gdal.UseExceptions()
ogr.UseExceptions()

if proj_data_dir:
    gdal.SetConfigOption("PROJ_DATA", str(proj_data_dir))
    gdal.SetConfigOption("PROJ_LIB", str(proj_data_dir))
    try:
        osr.SetPROJSearchPaths([str(proj_data_dir)])
    except AttributeError:
        pass


REQUIRED_CONTOUR_FIELDS = [
    ("elev_src", ogr.OFTString),
    ("elev_unit", ogr.OFTString),
    ("elev_m", ogr.OFTReal),
    ("is_index", ogr.OFTInteger),
    ("confidence", ogr.OFTString),
    ("source", ogr.OFTString),
]

AUTO_CANDIDATE_FIELDS = [
    ("candidate_id", ogr.OFTInteger),
    ("aoi_name", ogr.OFTString),
    ("mode", ogr.OFTString),
    ("elev_src", ogr.OFTString),
    ("elev_unit", ogr.OFTString),
    ("elev_m", ogr.OFTReal),
    ("is_index", ogr.OFTInteger),
    ("confidence", ogr.OFTString),
    ("source", ogr.OFTString),
    ("note", ogr.OFTString),
    ("mask_px", ogr.OFTInteger),
    ("skel_px", ogr.OFTInteger),
    ("stroke_w", ogr.OFTReal),
    ("length_m", ogr.OFTReal),
    ("is_closed", ogr.OFTInteger),
    ("modern_dem", ogr.OFTString),
    ("modern_samples", ogr.OFTInteger),
    ("modern_min_m", ogr.OFTReal),
    ("modern_med_m", ogr.OFTReal),
    ("modern_max_m", ogr.OFTReal),
    ("modern_range_m", ogr.OFTReal),
    ("modern_rank", ogr.OFTInteger),
]

AUTO_CANDIDATE_MAX_PIXELS = 4_500_000
AUTO_CANDIDATE_MIN_COMPONENT_PIXELS = 20
AUTO_CANDIDATE_MIN_LENGTH_M = 18.0
AUTO_CANDIDATE_SAMPLE_SPACING_M = 10.0


@dataclass
class Hawkins3DConfig:
    """Configuration for one audit/pilot/full pipeline run."""
    mode: str
    source_raster: Path
    fallback_raster: Path | None
    output_dir: Path
    aoi: str = "auto"
    contour_interval: str = "auto"
    z_unit: str = "auto"
    manual_package: Path | None = None
    sample_spacing: float = 12.0
    preview_size: int = 1400


def default_config(
    mode: str = "audit",
    source_raster: Path | None = None,
    output_dir: Path | None = None,
    aoi: str = "auto",
    contour_interval: str = "auto",
    z_unit: str = "auto",
) -> Hawkins3DConfig:
    """Return the repo-default Hawkins pipeline configuration."""
    repo_root = find_repo_root(CURRENT_DIR)
    hawkins_dir = repo_root / "qgis/Hawkins"
    resolved_output_dir = output_dir or (hawkins_dir / "3D map")
    resolved_source = source_raster or (
        hawkins_dir / "Hawkins_Topography_unzip/Hawkins_Topography.img"
    )
    return Hawkins3DConfig(
        mode=mode,
        source_raster=resolved_source,
        fallback_raster=None,
        output_dir=resolved_output_dir,
        aoi=aoi,
        contour_interval=contour_interval,
        z_unit=z_unit,
        manual_package=resolved_output_dir / "hawkins_work.gpkg",
    )


def run_pipeline(config: Hawkins3DConfig) -> dict[str, Any]:
    """Run the Hawkins pipeline inside a managed QGIS application context."""
    app, created = init_qgis_app(gui=False)
    try:
        return _run_pipeline(config)
    finally:
        shutdown_qgis_app(app, created)


def _run_pipeline(config: Hawkins3DConfig) -> dict[str, Any]:
    """Execute the end-to-end workflow and return a JSON-serializable report."""
    source_path = _resolve_source_raster(config)
    output_dir = config.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    ds = gdal.Open(str(source_path))
    if ds is None:
        raise FileNotFoundError(f"Could not open Hawkins source raster: {source_path}")

    report_paths = _output_paths(config.mode, output_dir)
    preview_rgb = _read_preview_rgb(ds, config.preview_size)
    preview_masks = _compute_color_masks(preview_rgb)
    audit_result = _audit_source(ds, preview_masks)

    _write_preview_png(source_path, report_paths["source_preview_png"])
    _write_mask_png(report_paths["preview_contour_png"], preview_masks["contour"])
    _write_mask_png(report_paths["preview_index_png"], preview_masks["index"])

    report: dict[str, Any] = {
        "status": "ok",
        "mode": config.mode,
        "source_raster": str(source_path),
        "output_dir": str(output_dir),
        "audit": audit_result,
        "artifacts": {
            "source_preview_png": str(report_paths["source_preview_png"]),
            "preview_contour_png": str(report_paths["preview_contour_png"]),
            "preview_index_png": str(report_paths["preview_index_png"]),
        },
        "notes": [],
    }

    if config.mode == "audit":
        _write_json(report_paths["report_json"], report)
        report["artifacts"]["report_json"] = str(report_paths["report_json"])
        return report

    aoi_info = _resolve_aoi(config, ds, audit_result)
    report["aoi"] = aoi_info

    work_package = _ensure_work_package(
        ds=ds,
        work_package_path=config.manual_package or report_paths["work_package_gpkg"],
        aoi_info=aoi_info,
        config=config,
    )
    report["artifacts"]["work_package_gpkg"] = str(work_package)

    if config.mode == "pilot":
        pilot_window = aoi_info["pixel_window"]
        pilot_gt = _window_geotransform(ds.GetGeoTransform(), pilot_window[0], pilot_window[1])
        pilot_rgb = ds.ReadAsArray(
            pilot_window[0],
            pilot_window[1],
            pilot_window[2],
            pilot_window[3],
        )[:3]
        pilot_masks = _compute_color_masks(pilot_rgb)
        _write_mask_raster(report_paths["contour_mask_tif"], pilot_masks["contour"], pilot_gt, ds.GetProjection())
        _write_mask_raster(report_paths["index_mask_tif"], pilot_masks["index"], pilot_gt, ds.GetProjection())
        _write_crop_png(source_path, report_paths["crop_preview_png"], pilot_window)
        report["artifacts"]["crop_preview_png"] = str(report_paths["crop_preview_png"])
        report["artifacts"]["contour_mask_tif"] = str(report_paths["contour_mask_tif"])
        report["artifacts"]["index_mask_tif"] = str(report_paths["index_mask_tif"])
    else:
        report["notes"].append(
            "Full mode skips a full-resolution contour mask until manual contours are attributed."
        )

    candidate_summary = _auto_extract_candidate_contours(
        ds=ds,
        work_package_path=config.manual_package or report_paths["work_package_gpkg"],
        aoi_info=aoi_info,
        config=config,
        candidate_mask_path=report_paths["candidate_mask_tif"],
    )
    report["candidate_contours"] = candidate_summary
    candidate_artifacts = candidate_summary.get("artifacts") or {}
    if candidate_artifacts.get("candidate_mask_tif"):
        report["artifacts"]["candidate_mask_tif"] = str(candidate_artifacts["candidate_mask_tif"])
    if candidate_summary.get("status") == "ok" and candidate_summary.get("feature_count", 0) > 0:
        report["notes"].append(
            "Auto-generated contour candidates were written to hawkins_contours_candidates for review and copy/paste into hawkins_contours_manual."
        )
    elif candidate_summary.get("status") == "skipped":
        report["notes"].append(
            "Automatic contour candidate extraction was skipped for this AOI size; use pilot mode or a smaller manual AOI for auto-generated guides."
        )

    workbench_path = _build_qgis_project(
        project_path=report_paths["workbench_qgs"],
        title=f"Hawkins {config.mode.title()} Workbench",
        source_raster=source_path,
        contour_gpkg=config.manual_package or report_paths["work_package_gpkg"],
        aoi_gpkg=config.manual_package or report_paths["work_package_gpkg"],
        aoi_layer_name="hawkins_aoi",
        dem_path=None,
        qa_gpkg=None,
        include_3d=False,
        extra_rasters=[
            report_paths["contour_mask_tif"] if report_paths["contour_mask_tif"].exists() else None,
            report_paths["index_mask_tif"] if report_paths["index_mask_tif"].exists() else None,
            report_paths["candidate_mask_tif"] if report_paths["candidate_mask_tif"].exists() else None,
        ],
    )
    report["artifacts"]["workbench_qgs"] = str(workbench_path)

    contour_summary = _manual_contour_summary(
        config.manual_package or report_paths["work_package_gpkg"],
        aoi_info["bbox"],
        config,
    )
    report["manual_contours"] = contour_summary

    if not contour_summary["ready_for_dem"]:
        report["status"] = "manual_input_required"
        if candidate_summary.get("status") == "ok" and candidate_summary.get("feature_count", 0) > 0:
            report["notes"].extend(
                [
                    "The work package is ready for contour review and label attribution in QGIS Desktop.",
                    "Copy the usable features from hawkins_contours_candidates into hawkins_contours_manual, then populate hawkins_contours_manual.elev_m and rerun pilot/full mode.",
                ]
            )
        else:
            report["notes"].extend(
                [
                    "The work package is ready for contour review and label attribution in QGIS Desktop.",
                    "Populate hawkins_contours_manual.elev_m for traced contours, or switch to pilot mode to generate auto-extracted contour candidates first.",
                ]
            )
        _write_json(report_paths["report_json"], report)
        report["artifacts"]["report_json"] = str(report_paths["report_json"])
        return report

    contours_gpkg = _build_vetted_contours_package(
        source_package=config.manual_package or report_paths["work_package_gpkg"],
        output_path=report_paths["contours_gpkg"],
        aoi_bbox=aoi_info["bbox"],
        config=config,
    )
    report["artifacts"]["contours_gpkg"] = str(contours_gpkg)

    contour_interval_m = _resolve_contour_interval(contour_summary, config.contour_interval)
    report["contour_interval_m"] = contour_interval_m

    dem_path = _build_dem_from_contours(
        contour_gpkg=contours_gpkg,
        output_dem=report_paths["dem_tif"],
        aoi_bbox=aoi_info["bbox"],
        source_ds=ds,
        sample_spacing=config.sample_spacing,
    )
    report["artifacts"]["dem_tif"] = str(dem_path)

    qa_gpkg = _derive_qa_contours(
        dem_path=dem_path,
        output_path=report_paths["qa_gpkg"],
        contour_interval=contour_interval_m,
    )
    report["artifacts"]["qa_gpkg"] = str(qa_gpkg)

    if config.mode == "pilot" and report_paths["contour_mask_tif"].exists():
        report["qa_alignment"] = _qa_alignment_score(
            dem_path=dem_path,
            contour_interval=contour_interval_m,
            mask_path=report_paths["contour_mask_tif"],
        )

    scene_path = _build_qgis_project(
        project_path=report_paths["scene_qgs"],
        title=f"Hawkins {config.mode.title()} 3D Scene",
        source_raster=source_path,
        contour_gpkg=contours_gpkg,
        aoi_gpkg=config.manual_package or report_paths["work_package_gpkg"],
        aoi_layer_name="hawkins_aoi",
        dem_path=dem_path,
        qa_gpkg=qa_gpkg,
        include_3d=True,
        extra_rasters=[],
    )
    report["artifacts"]["scene_qgs"] = str(scene_path)

    _write_json(report_paths["report_json"], report)
    report["artifacts"]["report_json"] = str(report_paths["report_json"])
    return report


def _resolve_source_raster(config: Hawkins3DConfig) -> Path:
    """Resolve the first existing raster from the configured source candidates."""
    candidates: list[Path] = []
    for candidate in (config.source_raster, config.fallback_raster):
        if candidate is None:
            continue
        if candidate in candidates:
            continue
        candidates.append(candidate)
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError(
        "None of the configured Hawkins raster sources exist: "
        + ", ".join(str(candidate) for candidate in candidates)
    )


def _output_paths(mode: str, output_dir: Path) -> dict[str, Path]:
    """Return the standard artifact paths for a given pipeline mode."""
    prefix = f"hawkins_{mode}"
    return {
        "report_json": output_dir / f"{prefix}_report.json",
        "source_preview_png": output_dir / f"{prefix}_source_preview.png",
        "preview_contour_png": output_dir / f"{prefix}_contour_preview.png",
        "preview_index_png": output_dir / f"{prefix}_index_preview.png",
        "crop_preview_png": output_dir / f"{prefix}_crop_preview.png",
        "contour_mask_tif": output_dir / f"{prefix}_contour_mask.tif",
        "index_mask_tif": output_dir / f"{prefix}_index_mask.tif",
        "candidate_mask_tif": output_dir / f"{prefix}_candidate_mask.tif",
        "work_package_gpkg": output_dir / "hawkins_work.gpkg",
        "contours_gpkg": output_dir / f"{prefix}_contours.gpkg",
        "qa_gpkg": output_dir / f"{prefix}_qa.gpkg",
        "dem_tif": output_dir / f"{prefix}_dem.tif",
        "workbench_qgs": output_dir / f"{prefix}_workbench.qgs",
        "scene_qgs": output_dir / f"{prefix}_3d_scene.qgs",
    }


def _read_preview_rgb(dataset, target_size: int) -> np.ndarray:
    width = dataset.RasterXSize
    height = dataset.RasterYSize
    scale = target_size / max(width, height)
    preview_width = max(64, int(width * scale))
    preview_height = max(64, int(height * scale))
    array = dataset.ReadAsArray(buf_xsize=preview_width, buf_ysize=preview_height)
    if array.ndim == 2:
        array = np.stack([array, array, array])
    if array.shape[0] < 3:
        array = np.vstack([array, array[-1:, :, :].repeat(3 - array.shape[0], axis=0)])
    return array[:3].astype(np.uint8, copy=False)


def _compute_color_masks(rgb: np.ndarray) -> dict[str, np.ndarray]:
    """Build broad preview masks used by audit mode and AOI selection."""
    red = rgb[0].astype(np.int16)
    green = rgb[1].astype(np.int16)
    blue = rgb[2].astype(np.int16)
    value = ((red + green + blue) / 3.0).astype(np.float32)

    dark_cut = float(np.percentile(value, 42))
    deep_cut = float(np.percentile(value, 24))

    brown_mask = (
        (red >= green)
        & (green >= blue)
        & ((red - green) >= 4)
        & ((green - blue) >= 2)
        & ((red - blue) >= 12)
    )
    neutral_dark_mask = (
        (value <= deep_cut)
        & (np.abs(red - green) <= 36)
        & (np.abs(green - blue) <= 36)
    )
    red_mask = (red >= green + 40) & (red >= blue + 55) & (np.abs(green - blue) <= 18)
    blue_mask = (blue >= green + 18) & (blue >= red + 18)

    contour_mask = ((brown_mask & (value <= dark_cut)) | neutral_dark_mask) & ~red_mask & ~blue_mask
    contour_mask = _neighbor_cleanup(contour_mask, min_neighbors=2, passes=2)

    neighbor_counts = _neighbor_counts(contour_mask)
    index_mask = contour_mask & (neighbor_counts >= 3)
    index_mask = _neighbor_cleanup(index_mask, min_neighbors=1, passes=1)

    return {
        "contour": contour_mask,
        "index": index_mask,
        "red": red_mask,
        "blue": blue_mask,
        "value": value,
    }


def _neighbor_cleanup(mask: np.ndarray, min_neighbors: int, passes: int) -> np.ndarray:
    cleaned = mask.astype(bool, copy=True)
    for _ in range(passes):
        neighbors = _neighbor_counts(cleaned)
        cleaned &= neighbors >= min_neighbors
    return cleaned


def _neighbor_counts(mask: np.ndarray) -> np.ndarray:
    neighbors = np.zeros(mask.shape, dtype=np.uint8)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            shifted = np.roll(mask, shift=(dy, dx), axis=(0, 1))
            if dy > 0:
                shifted[:dy, :] = False
            elif dy < 0:
                shifted[dy:, :] = False
            if dx > 0:
                shifted[:, :dx] = False
            elif dx < 0:
                shifted[:, dx:] = False
            neighbors += shifted
    return neighbors


def _audit_source(dataset, preview_masks: dict[str, np.ndarray]) -> dict[str, Any]:
    """Summarize raster properties and pilot-AOI feasibility signals."""
    contour_density = float(np.mean(preview_masks["contour"]))
    index_density = float(np.mean(preview_masks["index"]))
    red_density = float(np.mean(preview_masks["red"]))
    blue_density = float(np.mean(preview_masks["blue"]))
    pilot_candidate = _find_pilot_candidate(dataset, preview_masks)

    projection = dataset.GetProjection()
    spatial_ref = osr.SpatialReference()
    if projection:
        spatial_ref.ImportFromWkt(projection)

    return {
        "raster_size": [dataset.RasterXSize, dataset.RasterYSize],
        "band_count": dataset.RasterCount,
        "pixel_size": [dataset.GetGeoTransform()[1], abs(dataset.GetGeoTransform()[5])],
        "crs_wkt_head": projection[:180],
        "crs_authid": spatial_ref.GetAuthorityCode(None) or "",
        "contour_density": contour_density,
        "index_density": index_density,
        "red_density": red_density,
        "blue_density": blue_density,
        "color_separable": contour_density >= 0.03 and index_density >= 0.004 and (red_density + blue_density) <= 0.08,
        "pilot_candidate": pilot_candidate,
        "absolute_label_detection": {
            "implemented": False,
            "status": "manual_verification_required",
        },
    }


def _find_pilot_candidate(dataset, preview_masks: dict[str, np.ndarray]) -> dict[str, Any] | None:
    """Pick a pilot AOI with strong contour density and limited cartographic clutter."""
    contour = preview_masks["contour"]
    index = preview_masks["index"]
    clutter = preview_masks["red"] | preview_masks["blue"]

    height, width = contour.shape
    tile = max(120, min(height, width) // 5)
    step = max(40, tile // 2)

    best: dict[str, Any] | None = None
    best_score = -1e18

    for y0 in range(0, max(1, height - tile + 1), step):
        for x0 in range(0, max(1, width - tile + 1), step):
            y1 = min(height, y0 + tile)
            x1 = min(width, x0 + tile)
            tile_contour = contour[y0:y1, x0:x1]
            tile_index = index[y0:y1, x0:x1]
            tile_clutter = clutter[y0:y1, x0:x1]

            contour_density = float(np.mean(tile_contour))
            index_density = float(np.mean(tile_index))
            clutter_density = float(np.mean(tile_clutter))
            crossings = _count_runs(tile_index[tile_index.shape[0] // 2, :], 3) + _count_runs(
                tile_index[:, tile_index.shape[1] // 2],
                3,
            )

            if contour_density < 0.025 or contour_density > 0.34:
                continue
            if index_density < 0.003:
                continue
            if clutter_density > 0.04:
                continue

            score = (contour_density * 160) + (index_density * 380) + (crossings * 4.5) - (clutter_density * 200)
            if score <= best_score:
                continue

            pixel_window = _preview_window_to_source_window(dataset, contour.shape, (x0, y0, x1 - x0, y1 - y0))
            bbox = _pixel_window_to_bbox(dataset, pixel_window)
            best_score = score
            best = {
                "score": score,
                "preview_window": [x0, y0, x1 - x0, y1 - y0],
                "pixel_window": list(pixel_window),
                "bbox": bbox,
                "contour_density": contour_density,
                "index_density": index_density,
                "index_crossings": crossings,
                "clutter_density": clutter_density,
            }

    return best


def _preview_window_to_source_window(dataset, preview_shape: tuple[int, int], window: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    preview_height, preview_width = preview_shape
    x0, y0, width, height = window
    scale_x = dataset.RasterXSize / preview_width
    scale_y = dataset.RasterYSize / preview_height
    source_x0 = max(0, int(math.floor(x0 * scale_x)))
    source_y0 = max(0, int(math.floor(y0 * scale_y)))
    source_width = max(1, int(math.ceil(width * scale_x)))
    source_height = max(1, int(math.ceil(height * scale_y)))
    source_width = min(source_width, dataset.RasterXSize - source_x0)
    source_height = min(source_height, dataset.RasterYSize - source_y0)
    return source_x0, source_y0, source_width, source_height


def _pixel_window_to_bbox(dataset, pixel_window: tuple[int, int, int, int]) -> list[float]:
    geo_transform = dataset.GetGeoTransform()
    xoff, yoff, width, height = pixel_window
    x_min = geo_transform[0] + (xoff * geo_transform[1]) + (yoff * geo_transform[2])
    y_max = geo_transform[3] + (xoff * geo_transform[4]) + (yoff * geo_transform[5])
    x_max = x_min + (width * geo_transform[1]) + (height * geo_transform[2])
    y_min = y_max + (width * geo_transform[4]) + (height * geo_transform[5])
    return [float(min(x_min, x_max)), float(min(y_min, y_max)), float(max(x_min, x_max)), float(max(y_min, y_max))]


def _resolve_aoi(config: Hawkins3DConfig, dataset, audit_result: dict[str, Any]) -> dict[str, Any]:
    """Resolve ``auto`` or explicit AOI input into bbox and pixel-window form."""
    if config.mode == "full" and config.aoi == "auto":
        bbox = _pixel_window_to_bbox(dataset, (0, 0, dataset.RasterXSize, dataset.RasterYSize))
        return {
            "name": "full_extent",
            "bbox": bbox,
            "pixel_window": [0, 0, dataset.RasterXSize, dataset.RasterYSize],
        }

    if config.aoi == "auto":
        pilot_candidate = audit_result.get("pilot_candidate")
        if not pilot_candidate:
            raise ValueError("Audit did not find a usable pilot AOI; manual AOI input is required.")
        return {
            "name": "pilot_auto",
            "bbox": pilot_candidate["bbox"],
            "pixel_window": pilot_candidate["pixel_window"],
        }

    numbers = [float(part.strip()) for part in config.aoi.split(",")]
    if len(numbers) != 4:
        raise ValueError("--aoi must be 'auto' or 'xmin,ymin,xmax,ymax'")
    bbox = [numbers[0], numbers[1], numbers[2], numbers[3]]
    pixel_window = _bbox_to_pixel_window(dataset, bbox)
    return {
        "name": "manual_aoi",
        "bbox": bbox,
        "pixel_window": pixel_window,
    }


def _bbox_to_pixel_window(dataset, bbox: list[float]) -> list[int]:
    gt = dataset.GetGeoTransform()
    x_min, y_min, x_max, y_max = bbox
    xoff = max(0, int(math.floor((x_min - gt[0]) / gt[1])))
    xend = min(dataset.RasterXSize, int(math.ceil((x_max - gt[0]) / gt[1])))
    yoff = max(0, int(math.floor((gt[3] - y_max) / abs(gt[5]))))
    yend = min(dataset.RasterYSize, int(math.ceil((gt[3] - y_min) / abs(gt[5]))))
    return [xoff, yoff, max(1, xend - xoff), max(1, yend - yoff)]


def _write_preview_png(source_raster: Path, output_png: Path) -> None:
    gdal.Translate(str(output_png), str(source_raster), format="PNG", width=1400)


def _write_crop_png(source_raster: Path, output_png: Path, pixel_window: list[int]) -> None:
    gdal.Translate(
        str(output_png),
        str(source_raster),
        format="PNG",
        srcWin=pixel_window,
        width=1200,
    )


def _write_mask_raster(output_path: Path, mask: np.ndarray, geo_transform: tuple[float, ...], projection: str) -> None:
    driver = gdal.GetDriverByName("GTiff")
    dataset = driver.Create(
        str(output_path),
        mask.shape[1],
        mask.shape[0],
        1,
        gdal.GDT_Byte,
        options=["COMPRESS=LZW", "TILED=YES"],
    )
    dataset.SetGeoTransform(geo_transform)
    dataset.SetProjection(projection)
    band = dataset.GetRasterBand(1)
    band.SetNoDataValue(0)
    band.WriteArray(mask.astype(np.uint8))
    band.FlushCache()
    dataset.FlushCache()
    band = None
    dataset = None


def _write_mask_png(output_path: Path, mask: np.ndarray) -> None:
    driver = gdal.GetDriverByName("GTiff")
    tmp_path = output_path.with_suffix(".tmp.tif")
    dataset = driver.Create(str(tmp_path), mask.shape[1], mask.shape[0], 1, gdal.GDT_Byte)
    dataset.GetRasterBand(1).WriteArray((mask.astype(np.uint8) * 255))
    dataset.FlushCache()
    dataset = None
    gdal.Translate(str(output_path), str(tmp_path), format="PNG")
    tmp_path.unlink(missing_ok=True)


def _window_geotransform(geo_transform: tuple[float, ...], xoff: int, yoff: int) -> tuple[float, ...]:
    return (
        geo_transform[0] + (xoff * geo_transform[1]) + (yoff * geo_transform[2]),
        geo_transform[1],
        geo_transform[2],
        geo_transform[3] + (xoff * geo_transform[4]) + (yoff * geo_transform[5]),
        geo_transform[4],
        geo_transform[5],
    )


def _ensure_work_package(
    ds,
    work_package_path: Path,
    aoi_info: dict[str, Any],
    config: Hawkins3DConfig,
) -> Path:
    """Create or update the GeoPackage that stores editable Hawkins work layers."""
    driver = ogr.GetDriverByName("GPKG")
    if work_package_path.exists():
        package = driver.Open(str(work_package_path), update=1)
    else:
        package = driver.CreateDataSource(str(work_package_path))

    projection = ds.GetProjection()
    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromWkt(projection)

    aoi_layer = _ensure_layer(
        package,
        "hawkins_aoi",
        spatial_ref,
        ogr.wkbPolygon,
        [
            ("aoi_name", ogr.OFTString),
            ("mode", ogr.OFTString),
            ("source_raster", ogr.OFTString),
        ],
    )
    _ensure_layer(
        package,
        "hawkins_contours_manual",
        spatial_ref,
        ogr.wkbLineString,
        [
            ("contour_id", ogr.OFTInteger),
            ("elev_src", ogr.OFTString),
            ("elev_unit", ogr.OFTString),
            ("elev_m", ogr.OFTReal),
            ("is_index", ogr.OFTInteger),
            ("confidence", ogr.OFTString),
            ("source", ogr.OFTString),
            ("note", ogr.OFTString),
        ],
    )
    _ensure_layer(
        package,
        "hawkins_labels_manual",
        spatial_ref,
        ogr.wkbPoint,
        [
            ("label_text", ogr.OFTString),
            ("elev_src", ogr.OFTString),
            ("elev_unit", ogr.OFTString),
            ("elev_m", ogr.OFTReal),
            ("confidence", ogr.OFTString),
            ("note", ogr.OFTString),
        ],
    )
    _ensure_layer(
        package,
        "hawkins_contours_candidates",
        spatial_ref,
        ogr.wkbLineString,
        AUTO_CANDIDATE_FIELDS,
    )

    _upsert_aoi_feature(
        aoi_layer,
        aoi_name=aoi_info["name"],
        mode=config.mode,
        source_raster=str(_resolve_source_raster(config)),
        bbox=aoi_info["bbox"],
    )

    aoi_layer = None
    package = None
    return work_package_path


def _ensure_layer(
    datasource,
    layer_name: str,
    spatial_ref,
    geometry_type: int,
    fields: list[tuple[str, int]],
):
    layer = datasource.GetLayerByName(layer_name)
    if layer is None:
        layer = datasource.CreateLayer(layer_name, srs=spatial_ref, geom_type=geometry_type)

    existing_fields = {
        layer.GetLayerDefn().GetFieldDefn(index).GetName()
        for index in range(layer.GetLayerDefn().GetFieldCount())
    }
    for field_name, field_type in fields:
        if field_name in existing_fields:
            continue
        layer.CreateField(ogr.FieldDefn(field_name, field_type))
    return layer


def _upsert_aoi_feature(layer, aoi_name: str, mode: str, source_raster: str, bbox: list[float]) -> None:
    matching_fids: list[int] = []
    layer.ResetReading()
    for feature in layer:
        if feature.GetField("aoi_name") == aoi_name:
            matching_fids.append(feature.GetFID())

    for fid in matching_fids:
        layer.DeleteFeature(fid)

    ring = ogr.Geometry(ogr.wkbLinearRing)
    ring.AddPoint_2D(bbox[0], bbox[1])
    ring.AddPoint_2D(bbox[2], bbox[1])
    ring.AddPoint_2D(bbox[2], bbox[3])
    ring.AddPoint_2D(bbox[0], bbox[3])
    ring.AddPoint_2D(bbox[0], bbox[1])
    polygon = ogr.Geometry(ogr.wkbPolygon)
    polygon.AddGeometry(ring)

    feature = ogr.Feature(layer.GetLayerDefn())
    feature.SetField("aoi_name", aoi_name)
    feature.SetField("mode", mode)
    feature.SetField("source_raster", source_raster)
    feature.SetGeometry(polygon)
    layer.CreateFeature(feature)


def _spatial_ref_from_wkt(wkt: str):
    spatial_ref = osr.SpatialReference()
    if wkt:
        spatial_ref.ImportFromWkt(wkt)
    if hasattr(spatial_ref, "SetAxisMappingStrategy"):
        spatial_ref.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return spatial_ref


def _auto_extract_candidate_contours(
    ds,
    work_package_path: Path,
    aoi_info: dict[str, Any],
    config: Hawkins3DConfig,
    candidate_mask_path: Path,
) -> dict[str, Any]:
    """Generate pilot-scale contour candidates and store them in the work package.

    These features are guide geometry only. They are meant to help manual review
    and later automation, not to be treated as final historical contours.
    """
    pixel_window = aoi_info["pixel_window"]
    pixel_count = int(pixel_window[2] * pixel_window[3])
    if pixel_count > AUTO_CANDIDATE_MAX_PIXELS:
        return {
            "status": "skipped",
            "reason": "aoi_too_large",
            "aoi_pixels": pixel_count,
            "max_pixels": AUTO_CANDIDATE_MAX_PIXELS,
        }

    rgb = ds.ReadAsArray(
        pixel_window[0],
        pixel_window[1],
        pixel_window[2],
        pixel_window[3],
    )[:3]
    candidate_mask, mask_summary = _compute_candidate_contour_mask(rgb)
    gt = _window_geotransform(ds.GetGeoTransform(), pixel_window[0], pixel_window[1])
    _write_mask_raster(candidate_mask_path, candidate_mask, gt, ds.GetProjection())

    candidates, candidate_summary = _vectorize_candidate_contours(
        candidate_mask=candidate_mask,
        geo_transform=gt,
        aoi_name=aoi_info["name"],
        mode=config.mode,
    )
    samplers = _reference_dem_samplers(aoi_info["bbox"], ds.GetProjection())
    try:
        sampled_count = 0
        for candidate in candidates:
            stats = _sample_candidate_reference_stats(candidate["points_map"], samplers)
            candidate.update(stats)
            if stats["modern_samples"] > 0:
                sampled_count += 1
    finally:
        for sampler in samplers:
            sampler["band"] = None
            sampler["dataset"] = None

    _rank_candidate_elevations(candidates)
    _replace_candidate_features(
        work_package_path=work_package_path,
        aoi_name=aoi_info["name"],
        candidates=candidates,
    )

    lengths = [candidate["length_m"] for candidate in candidates]
    modern_sources = sorted({candidate["modern_dem"] for candidate in candidates if candidate["modern_dem"]})
    return {
        "status": "ok" if candidates else "no_candidates",
        "aoi_name": aoi_info["name"],
        "mask_summary": mask_summary,
        "component_summary": candidate_summary,
        "feature_count": len(candidates),
        "sampled_feature_count": sampled_count,
        "reference_dems": modern_sources,
        "length_m": {
            "min": float(min(lengths)) if lengths else 0.0,
            "median": float(np.median(lengths)) if lengths else 0.0,
            "max": float(max(lengths)) if lengths else 0.0,
        },
        "artifacts": {
            "candidate_mask_tif": str(candidate_mask_path),
        },
        "notes": [
            "Candidate contours are guide geometry only; validate against Hawkins before copying them into hawkins_contours_manual.",
            "modern_med_m is from a modern DEM and is only a calibration hint, not a historical elevation assignment.",
        ],
    }


def _compute_candidate_contour_mask(rgb: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    """Build a stricter contour-ink mask than the broad audit preview mask."""
    red = rgb[0].astype(np.int16)
    green = rgb[1].astype(np.int16)
    blue = rgb[2].astype(np.int16)
    value = ((red + green + blue) / 3.0).astype(np.float32)

    red_mask = (red >= green + 40) & (red >= blue + 55) & (np.abs(green - blue) <= 18)
    blue_mask = (blue >= green + 18) & (blue >= red + 18)

    brown_cut = float(np.percentile(value, 15))
    neutral_cut = float(np.percentile(value, 8))
    brown_mask = (
        (red >= green)
        & (green >= blue)
        & ((red - green) >= 6)
        & ((green - blue) >= 3)
        & ((red - blue) >= 16)
        & (value <= brown_cut)
    )
    neutral_dark_mask = (
        (value <= neutral_cut)
        & (np.abs(red - green) <= 14)
        & (np.abs(green - blue) <= 14)
    )

    base_mask = (brown_mask | neutral_dark_mask) & ~red_mask & ~blue_mask
    local_coverage = ndi.uniform_filter(base_mask.astype(np.float32), size=5, mode="constant")
    candidate_mask = base_mask & (local_coverage <= 0.7)

    labels, component_count = ndi.label(candidate_mask, structure=np.ones((3, 3), dtype=np.uint8))
    component_sizes = np.bincount(labels.ravel())
    keep = np.flatnonzero(component_sizes >= AUTO_CANDIDATE_MIN_COMPONENT_PIXELS)
    keep = keep[keep != 0]
    candidate_mask = np.isin(labels, keep)

    return candidate_mask, {
        "brown_cut": brown_cut,
        "neutral_cut": neutral_cut,
        "base_density": float(np.mean(base_mask)),
        "candidate_density": float(np.mean(candidate_mask)),
        "raw_component_count": int(component_count),
        "kept_component_count": int(len(keep)),
    }


def _vectorize_candidate_contours(
    candidate_mask: np.ndarray,
    geo_transform: tuple[float, ...],
    aoi_name: str,
    mode: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Trace simple candidate linework from a raster contour mask."""
    structure = np.ones((3, 3), dtype=np.uint8)
    mask_labels, _ = ndi.label(candidate_mask, structure=structure)
    mask_sizes = np.bincount(mask_labels.ravel())

    skeleton = _skeletonize_mask(candidate_mask)
    skeleton_labels, component_count = ndi.label(skeleton, structure=structure)
    component_slices = ndi.find_objects(skeleton_labels)
    neighbor_kernel = np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]], dtype=np.uint8)
    neighbor_counts = ndi.convolve(skeleton.astype(np.uint8), neighbor_kernel, mode="constant", cval=0)

    candidates: list[dict[str, Any]] = []
    skipped_branchy = 0
    skipped_short = 0
    skipped_trace_fail = 0

    for index, component_slice in enumerate(component_slices, start=1):
        if component_slice is None:
            continue

        component_mask = skeleton_labels[component_slice] == index
        if not np.any(component_mask):
            continue

        component_degrees = neighbor_counts[component_slice][component_mask]
        end_count = int(np.count_nonzero(component_degrees == 1))
        branch_count = int(np.count_nonzero(component_degrees >= 3))
        if branch_count != 0 or end_count not in (0, 2):
            skipped_branchy += 1
            continue

        local_path = _trace_simple_skeleton_component(component_mask)
        if not local_path:
            skipped_trace_fail += 1
            continue

        y_offset = component_slice[0].start or 0
        x_offset = component_slice[1].start or 0
        global_path = [(y + y_offset, x + x_offset) for y, x in local_path]
        first_y, first_x = global_path[0]
        mask_label = int(mask_labels[first_y, first_x])
        area_pixels = int(mask_sizes[mask_label]) if mask_label > 0 else int(len(global_path))
        points_map = [_pixel_center_to_map(geo_transform, x, y) for y, x in global_path]
        length_m = _polyline_length(points_map)
        if length_m < AUTO_CANDIDATE_MIN_LENGTH_M:
            skipped_short += 1
            continue

        geometry = ogr.Geometry(ogr.wkbLineString)
        for x_map, y_map in points_map:
            geometry.AddPoint_2D(x_map, y_map)
        if end_count == 0 and points_map[0] != points_map[-1]:
            geometry.AddPoint_2D(points_map[0][0], points_map[0][1])

        skeleton_pixels = max(1, len(global_path) - (1 if end_count == 0 else 0))
        stroke_width = area_pixels / skeleton_pixels
        candidates.append(
            {
                "candidate_id": len(candidates) + 1,
                "aoi_name": aoi_name,
                "mode": mode,
                "geometry": geometry,
                "points_map": points_map,
                "mask_px": area_pixels,
                "skel_px": skeleton_pixels,
                "stroke_w": float(stroke_width),
                "length_m": float(length_m),
                "is_closed": int(end_count == 0),
                "is_index": int(stroke_width >= 1.8 or area_pixels >= 120),
                "confidence": "candidate",
                "source": "Hawkins_auto",
                "note": "Auto-extracted from contour ink; validate before use.",
                "modern_dem": "",
                "modern_samples": 0,
                "modern_min_m": None,
                "modern_med_m": None,
                "modern_max_m": None,
                "modern_range_m": None,
                "modern_rank": 0,
            }
        )

    return candidates, {
        "skeleton_component_count": int(component_count),
        "traceable_components": int(len(candidates)),
        "skipped_branchy_components": int(skipped_branchy),
        "skipped_short_components": int(skipped_short),
        "skipped_trace_fail_components": int(skipped_trace_fail),
    }


def _skeletonize_mask(mask: np.ndarray) -> np.ndarray:
    """Thin a binary mask to one-pixel centerlines using Zhang-Suen logic."""
    image = mask.astype(np.uint8, copy=True)
    changed = True
    iterations = 0
    while changed and iterations < 200:
        changed = False
        for step in (0, 1):
            padded = np.pad(image, 1, mode="constant")
            p2 = padded[:-2, 1:-1]
            p3 = padded[:-2, 2:]
            p4 = padded[1:-1, 2:]
            p5 = padded[2:, 2:]
            p6 = padded[2:, 1:-1]
            p7 = padded[2:, :-2]
            p8 = padded[1:-1, :-2]
            p9 = padded[:-2, :-2]

            neighbors = p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9
            transitions = ((p2 == 0) & (p3 == 1)).astype(np.uint8)
            transitions += ((p3 == 0) & (p4 == 1)).astype(np.uint8)
            transitions += ((p4 == 0) & (p5 == 1)).astype(np.uint8)
            transitions += ((p5 == 0) & (p6 == 1)).astype(np.uint8)
            transitions += ((p6 == 0) & (p7 == 1)).astype(np.uint8)
            transitions += ((p7 == 0) & (p8 == 1)).astype(np.uint8)
            transitions += ((p8 == 0) & (p9 == 1)).astype(np.uint8)
            transitions += ((p9 == 0) & (p2 == 1)).astype(np.uint8)

            if step == 0:
                mask_a = p2 * p4 * p6
                mask_b = p4 * p6 * p8
            else:
                mask_a = p2 * p4 * p8
                mask_b = p2 * p6 * p8

            marker = (
                (image == 1)
                & (neighbors >= 2)
                & (neighbors <= 6)
                & (transitions == 1)
                & (mask_a == 0)
                & (mask_b == 0)
            )
            if np.any(marker):
                image[marker] = 0
                changed = True
        iterations += 1
    return image.astype(bool)


def _trace_simple_skeleton_component(component_mask: np.ndarray) -> list[tuple[int, int]] | None:
    """Trace a non-branching skeleton component into an ordered pixel path."""
    coords = np.argwhere(component_mask)
    pixels = {tuple(int(value) for value in coord) for coord in coords}
    if not pixels:
        return None

    degree_map = {pixel: len(_pixel_neighbors(pixel, pixels)) for pixel in pixels}
    end_points = sorted(pixel for pixel, degree in degree_map.items() if degree == 1)
    if len(end_points) == 2:
        start = end_points[0]
        is_loop = False
    elif len(end_points) == 0:
        start = min(pixels)
        is_loop = True
    else:
        return None

    path = [start]
    visited = {start}
    previous = None
    current = start

    while True:
        neighbors = [pixel for pixel in _pixel_neighbors(current, pixels) if pixel != previous]
        if not neighbors:
            break

        next_pixel = None
        for candidate in sorted(neighbors):
            if candidate not in visited:
                next_pixel = candidate
                break

        if next_pixel is None:
            if is_loop and start in neighbors and len(visited) == len(pixels):
                path.append(start)
                return path
            break

        path.append(next_pixel)
        visited.add(next_pixel)
        previous, current = current, next_pixel

    if len(visited) != len(pixels):
        return None
    return path


def _pixel_neighbors(pixel: tuple[int, int], pixels: set[tuple[int, int]]) -> list[tuple[int, int]]:
    y, x = pixel
    neighbors: list[tuple[int, int]] = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            neighbor = (y + dy, x + dx)
            if neighbor in pixels:
                neighbors.append(neighbor)
    return neighbors


def _pixel_center_to_map(geo_transform: tuple[float, ...], pixel_x: int, pixel_y: int) -> tuple[float, float]:
    map_x = geo_transform[0] + ((pixel_x + 0.5) * geo_transform[1]) + ((pixel_y + 0.5) * geo_transform[2])
    map_y = geo_transform[3] + ((pixel_x + 0.5) * geo_transform[4]) + ((pixel_y + 0.5) * geo_transform[5])
    return float(map_x), float(map_y)


def _polyline_length(points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    total = 0.0
    for start, end in zip(points[:-1], points[1:]):
        total += math.hypot(end[0] - start[0], end[1] - start[1])
    return total


def _reference_dem_samplers(aoi_bbox: list[float], source_projection_wkt: str) -> list[dict[str, Any]]:
    """Open overlapping modern DEMs and prepare coordinate transforms for sampling."""
    source_srs = _spatial_ref_from_wkt(source_projection_wkt)
    samplers: list[dict[str, Any]] = []
    for path in _reference_dem_paths():
        dataset = gdal.Open(str(path))
        if dataset is None:
            continue

        target_srs = _spatial_ref_from_wkt(dataset.GetProjection())
        overlap_area = _transformed_bbox_overlap(aoi_bbox, source_srs, dataset, target_srs)
        if overlap_area <= 0:
            dataset = None
            continue

        samplers.append(
            {
                "path": path,
                "dataset": dataset,
                "band": dataset.GetRasterBand(1),
                "nodata": dataset.GetRasterBand(1).GetNoDataValue(),
                "geo_transform": dataset.GetGeoTransform(),
                "target_srs": target_srs,
                "transform": osr.CoordinateTransformation(source_srs, target_srs),
                "overlap_area": overlap_area,
            }
        )
    return samplers


def _transformed_bbox_overlap(
    source_bbox: list[float],
    source_srs,
    dataset,
    target_srs,
) -> float:
    try:
        transform = osr.CoordinateTransformation(source_srs, target_srs)
        xs: list[float] = []
        ys: list[float] = []
        for x in (source_bbox[0], source_bbox[2]):
            for y in (source_bbox[1], source_bbox[3]):
                tx, ty, _ = transform.TransformPoint(x, y)
                xs.append(tx)
                ys.append(ty)
    except Exception:
        return 0.0

    source_bounds = [min(xs), min(ys), max(xs), max(ys)]
    target_bounds = _dataset_bbox(dataset)
    overlap_x = max(0.0, min(source_bounds[2], target_bounds[2]) - max(source_bounds[0], target_bounds[0]))
    overlap_y = max(0.0, min(source_bounds[3], target_bounds[3]) - max(source_bounds[1], target_bounds[1]))
    return float(overlap_x * overlap_y)


def _dataset_bbox(dataset) -> list[float]:
    gt = dataset.GetGeoTransform()
    x_min = gt[0]
    y_max = gt[3]
    x_max = x_min + (dataset.RasterXSize * gt[1]) + (dataset.RasterYSize * gt[2])
    y_min = y_max + (dataset.RasterXSize * gt[4]) + (dataset.RasterYSize * gt[5])
    return [float(min(x_min, x_max)), float(min(y_min, y_max)), float(max(x_min, x_max)), float(max(y_min, y_max))]


def _sample_candidate_reference_stats(
    points_map: list[tuple[float, float]],
    samplers: list[dict[str, Any]],
) -> dict[str, Any]:
    """Attach modern DEM min/median/max guide values to one candidate contour."""
    sample_points = _resample_polyline(points_map, AUTO_CANDIDATE_SAMPLE_SPACING_M)
    for sampler in samplers:
        values: list[float] = []
        for x, y in sample_points:
            try:
                tx, ty, _ = sampler["transform"].TransformPoint(x, y)
            except Exception:
                continue
            value = _sample_raster_nearest(
                band=sampler["band"],
                geo_transform=sampler["geo_transform"],
                raster_xsize=sampler["dataset"].RasterXSize,
                raster_ysize=sampler["dataset"].RasterYSize,
                x=tx,
                y=ty,
                nodata=sampler["nodata"],
            )
            if value is None:
                continue
            values.append(value)

        if not values:
            continue

        return {
            "modern_dem": sampler["path"].stem,
            "modern_samples": len(values),
            "modern_min_m": float(min(values)),
            "modern_med_m": float(np.median(values)),
            "modern_max_m": float(max(values)),
            "modern_range_m": float(max(values) - min(values)),
        }

    return {
        "modern_dem": "",
        "modern_samples": 0,
        "modern_min_m": None,
        "modern_med_m": None,
        "modern_max_m": None,
        "modern_range_m": None,
    }


def _resample_polyline(points: list[tuple[float, float]], spacing: float) -> list[tuple[float, float]]:
    if len(points) < 2:
        return list(points)

    samples: list[tuple[float, float]] = [points[0]]
    for start, end in zip(points[:-1], points[1:]):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        segment_length = math.hypot(dx, dy)
        if segment_length == 0:
            continue
        sample_count = max(1, int(math.ceil(segment_length / spacing)))
        for sample_index in range(1, sample_count + 1):
            factor = sample_index / sample_count
            samples.append((start[0] + (dx * factor), start[1] + (dy * factor)))
    return samples


def _sample_raster_nearest(
    band,
    geo_transform: tuple[float, ...],
    raster_xsize: int,
    raster_ysize: int,
    x: float,
    y: float,
    nodata: float | None,
) -> float | None:
    pixel_x, pixel_y = _world_to_pixel(geo_transform, x, y)
    column = int(math.floor(pixel_x))
    row = int(math.floor(pixel_y))
    if column < 0 or row < 0 or column >= raster_xsize or row >= raster_ysize:
        return None

    array = band.ReadAsArray(column, row, 1, 1)
    if array is None or array.size == 0:
        return None

    value = float(array[0, 0])
    if not np.isfinite(value):
        return None
    if nodata is not None and math.isclose(value, float(nodata), rel_tol=0.0, abs_tol=1e-6):
        return None
    return value


def _world_to_pixel(geo_transform: tuple[float, ...], x: float, y: float) -> tuple[float, float]:
    determinant = (geo_transform[1] * geo_transform[5]) - (geo_transform[2] * geo_transform[4])
    if determinant == 0:
        raise ValueError("GeoTransform is not invertible")
    pixel_x = ((geo_transform[5] * (x - geo_transform[0])) - (geo_transform[2] * (y - geo_transform[3]))) / determinant
    pixel_y = ((-geo_transform[4] * (x - geo_transform[0])) + (geo_transform[1] * (y - geo_transform[3]))) / determinant
    return pixel_x, pixel_y


def _rank_candidate_elevations(candidates: list[dict[str, Any]]) -> None:
    """Assign a low-to-high rank based on modern DEM median elevation hints."""
    ranked = sorted(
        [candidate for candidate in candidates if candidate["modern_med_m"] is not None],
        key=lambda candidate: candidate["modern_med_m"],
    )
    for rank, candidate in enumerate(ranked, start=1):
        candidate["modern_rank"] = rank


def _replace_candidate_features(
    work_package_path: Path,
    aoi_name: str,
    candidates: list[dict[str, Any]],
) -> None:
    """Replace auto-generated candidate features for a single AOI in-place."""
    datasource = ogr.Open(str(work_package_path), update=1)
    if datasource is None:
        raise ValueError(f"Failed to open work package for candidate update: {work_package_path}")

    layer = datasource.GetLayerByName("hawkins_contours_candidates")
    if layer is None:
        raise ValueError("Work package does not contain hawkins_contours_candidates")

    to_delete: list[int] = []
    layer.ResetReading()
    for feature in layer:
        if str(feature.GetField("aoi_name") or "") == aoi_name:
            to_delete.append(feature.GetFID())
    for fid in to_delete:
        layer.DeleteFeature(fid)

    for candidate in candidates:
        feature = ogr.Feature(layer.GetLayerDefn())
        feature.SetGeometry(candidate["geometry"].Clone())
        feature.SetField("candidate_id", int(candidate["candidate_id"]))
        feature.SetField("aoi_name", str(candidate["aoi_name"]))
        feature.SetField("mode", str(candidate["mode"]))
        feature.SetField("is_index", int(candidate["is_index"]))
        feature.SetField("confidence", str(candidate["confidence"]))
        feature.SetField("source", str(candidate["source"]))
        feature.SetField("note", str(candidate["note"]))
        feature.SetField("mask_px", int(candidate["mask_px"]))
        feature.SetField("skel_px", int(candidate["skel_px"]))
        feature.SetField("stroke_w", float(candidate["stroke_w"]))
        feature.SetField("length_m", float(candidate["length_m"]))
        feature.SetField("is_closed", int(candidate["is_closed"]))
        feature.SetField("modern_dem", str(candidate["modern_dem"] or ""))
        feature.SetField("modern_samples", int(candidate["modern_samples"]))
        if candidate["modern_min_m"] is not None:
            feature.SetField("modern_min_m", float(candidate["modern_min_m"]))
        if candidate["modern_med_m"] is not None:
            feature.SetField("modern_med_m", float(candidate["modern_med_m"]))
        if candidate["modern_max_m"] is not None:
            feature.SetField("modern_max_m", float(candidate["modern_max_m"]))
        if candidate["modern_range_m"] is not None:
            feature.SetField("modern_range_m", float(candidate["modern_range_m"]))
        feature.SetField("modern_rank", int(candidate["modern_rank"]))
        layer.CreateFeature(feature)

    layer = None
    datasource = None


def _manual_contour_summary(work_package: Path, aoi_bbox: list[float], config: Hawkins3DConfig) -> dict[str, Any]:
    """Report whether the vetted manual layer has enough attributed contours for a DEM."""
    datasource = ogr.Open(str(work_package))
    if datasource is None:
        return {"ready_for_dem": False, "reason": "work_package_missing"}

    layer = datasource.GetLayerByName("hawkins_contours_manual")
    if layer is None:
        return {"ready_for_dem": False, "reason": "manual_contour_layer_missing"}

    aoi_geom = _bbox_geometry(aoi_bbox)
    feature_count = 0
    attributed_count = 0
    accepted_count = 0
    elevations: list[float] = []

    for feature in layer:
        geometry = feature.GetGeometryRef()
        if geometry is None or geometry.IsEmpty():
            continue
        if not geometry.Intersects(aoi_geom):
            continue

        feature_count += 1
        elev_m = _feature_elevation_m(feature, config.z_unit)
        if elev_m is None:
            continue

        attributed_count += 1
        elevations.append(elev_m)

        confidence = (feature.GetField("confidence") or "").strip().lower()
        if confidence == "low":
            continue
        accepted_count += 1

    datasource = None
    distinct_elevations = sorted({round(value, 3) for value in elevations})
    return {
        "feature_count": feature_count,
        "attributed_count": attributed_count,
        "accepted_count": accepted_count,
        "distinct_elevations": distinct_elevations,
        "ready_for_dem": accepted_count >= 3 and len(distinct_elevations) >= 3,
    }


def _feature_elevation_m(feature, default_unit: str) -> float | None:
    elev_m = feature.GetField("elev_m")
    if elev_m not in (None, ""):
        return float(elev_m)

    raw_value = feature.GetField("elev_src")
    if raw_value in (None, ""):
        return None

    text = str(raw_value).strip()
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None

    numeric_value = float(match.group(0))
    unit = (feature.GetField("elev_unit") or default_unit or "").strip().lower()
    if unit in {"meter", "meters", "m"}:
        return numeric_value
    if unit in {"foot", "feet", "ft"}:
        return numeric_value * 0.3048
    return None


def _build_vetted_contours_package(
    source_package: Path,
    output_path: Path,
    aoi_bbox: list[float],
    config: Hawkins3DConfig,
) -> Path:
    """Copy accepted manual contours into a clean package used for DEM generation."""
    source_ds = ogr.Open(str(source_package))
    source_layer = source_ds.GetLayerByName("hawkins_contours_manual")
    if source_layer is None:
        raise ValueError("Work package does not contain hawkins_contours_manual")

    output_path.unlink(missing_ok=True)
    driver = ogr.GetDriverByName("GPKG")
    target_ds = driver.CreateDataSource(str(output_path))
    target_layer = target_ds.CreateLayer(
        "hawkins_contours",
        srs=source_layer.GetSpatialRef(),
        geom_type=ogr.wkbLineString,
    )
    for field_name, field_type in REQUIRED_CONTOUR_FIELDS:
        target_layer.CreateField(ogr.FieldDefn(field_name, field_type))
    target_layer.CreateField(ogr.FieldDefn("note", ogr.OFTString))

    aoi_geom = _bbox_geometry(aoi_bbox)
    for feature in source_layer:
        geometry = feature.GetGeometryRef()
        if geometry is None or geometry.IsEmpty() or not geometry.Intersects(aoi_geom):
            continue

        elev_m = _feature_elevation_m(feature, config.z_unit)
        if elev_m is None:
            continue

        confidence = (feature.GetField("confidence") or "medium").strip()
        if confidence.lower() == "low":
            continue

        clipped = geometry.Intersection(aoi_geom)
        if clipped is None or clipped.IsEmpty():
            continue

        if clipped.GetGeometryType() in (ogr.wkbMultiLineString, ogr.wkbGeometryCollection):
            parts = [clipped.GetGeometryRef(index).Clone() for index in range(clipped.GetGeometryCount())]
        else:
            parts = [clipped.Clone()]

        for part in parts:
            if part.IsEmpty():
                continue
            out_feature = ogr.Feature(target_layer.GetLayerDefn())
            out_feature.SetGeometry(part)
            out_feature.SetField("elev_src", str(feature.GetField("elev_src") or elev_m))
            out_feature.SetField("elev_unit", str(feature.GetField("elev_unit") or config.z_unit or ""))
            out_feature.SetField("elev_m", float(elev_m))
            out_feature.SetField("is_index", int(feature.GetField("is_index") or 0))
            out_feature.SetField("confidence", confidence or "medium")
            out_feature.SetField("source", "Hawkins")
            out_feature.SetField("note", str(feature.GetField("note") or ""))
            target_layer.CreateFeature(out_feature)

    target_layer = None
    target_ds = None
    source_ds = None
    return output_path


def _build_dem_from_contours(
    contour_gpkg: Path,
    output_dem: Path,
    aoi_bbox: list[float],
    source_ds,
    sample_spacing: float,
) -> Path:
    """Interpolate a DEM from vetted contour lines by sampling points along them."""
    contour_ds = ogr.Open(str(contour_gpkg))
    contour_layer = contour_ds.GetLayerByName("hawkins_contours")
    if contour_layer is None:
        raise ValueError("Vetted contour package does not contain hawkins_contours")

    point_path = output_dem.with_name(output_dem.stem + "_points.gpkg")
    point_path.unlink(missing_ok=True)
    point_driver = ogr.GetDriverByName("GPKG")
    point_ds = point_driver.CreateDataSource(str(point_path))
    point_layer = point_ds.CreateLayer(
        "hawkins_dem_points",
        srs=contour_layer.GetSpatialRef(),
        geom_type=ogr.wkbPoint,
    )
    point_layer.CreateField(ogr.FieldDefn("elev_m", ogr.OFTReal))

    for feature in contour_layer:
        geometry = feature.GetGeometryRef()
        if geometry is None or geometry.IsEmpty():
            continue
        elev_m = float(feature.GetField("elev_m"))
        _emit_points_from_geometry(point_layer, geometry, elev_m, sample_spacing)

    point_layer = None
    point_ds = None
    contour_layer = None
    contour_ds = None

    gt = source_ds.GetGeoTransform()
    resolution = max(2.0, min(5.0, abs(gt[1]) * 4.0))
    width = max(1, int(math.ceil((aoi_bbox[2] - aoi_bbox[0]) / resolution)))
    height = max(1, int(math.ceil((aoi_bbox[3] - aoi_bbox[1]) / resolution)))

    grid_options = gdal.GridOptions(
        format="GTiff",
        outputBounds=[aoi_bbox[0], aoi_bbox[1], aoi_bbox[2], aoi_bbox[3]],
        width=width,
        height=height,
        zfield="elev_m",
        algorithm="linear:nodata=-9999.0",
        outputType=gdal.GDT_Float32,
        creationOptions=["COMPRESS=LZW", "TILED=YES"],
    )
    gdal.Grid(str(output_dem), str(point_path), options=grid_options)

    _mask_dem_to_aoi(output_dem, aoi_bbox, source_ds.GetProjection())
    return output_dem


def _emit_points_from_geometry(layer, geometry, elev_m: float, spacing: float) -> None:
    geometry_type = geometry.GetGeometryType()
    if geometry_type == ogr.wkbMultiLineString:
        for index in range(geometry.GetGeometryCount()):
            _emit_points_from_geometry(layer, geometry.GetGeometryRef(index), elev_m, spacing)
        return

    points = geometry.GetPoints()
    if len(points) < 2:
        return

    for start, end in zip(points[:-1], points[1:]):
        x0, y0 = start[:2]
        x1, y1 = end[:2]
        segment_length = math.hypot(x1 - x0, y1 - y0)
        sample_count = max(1, int(math.ceil(segment_length / spacing)))
        for sample_index in range(sample_count + 1):
            factor = sample_index / sample_count
            x = x0 + ((x1 - x0) * factor)
            y = y0 + ((y1 - y0) * factor)
            feature = ogr.Feature(layer.GetLayerDefn())
            point = ogr.Geometry(ogr.wkbPoint)
            point.AddPoint_2D(x, y)
            feature.SetGeometry(point)
            feature.SetField("elev_m", elev_m)
            layer.CreateFeature(feature)


def _mask_dem_to_aoi(dem_path: Path, bbox: list[float], projection_wkt: str) -> None:
    dem_ds = gdal.Open(str(dem_path), gdal.GA_Update)
    band = dem_ds.GetRasterBand(1)
    nodata = band.GetNoDataValue()
    mask = _rasterize_geometry_mask(
        geometry=_bbox_geometry(bbox),
        xsize=dem_ds.RasterXSize,
        ysize=dem_ds.RasterYSize,
        geo_transform=dem_ds.GetGeoTransform(),
        projection_wkt=projection_wkt,
    )
    array = band.ReadAsArray().astype(np.float32, copy=False)
    array[~mask] = nodata
    band.WriteArray(array)
    band.FlushCache()
    dem_ds.FlushCache()
    band = None
    dem_ds = None


def _rasterize_geometry_mask(
    geometry,
    xsize: int,
    ysize: int,
    geo_transform: tuple[float, ...],
    projection_wkt: str,
) -> np.ndarray:
    raster_ds = gdal.GetDriverByName("MEM").Create("", xsize, ysize, 1, gdal.GDT_Byte)
    raster_ds.SetGeoTransform(geo_transform)
    raster_ds.SetProjection(projection_wkt)

    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromWkt(projection_wkt)
    vector_ds = ogr.GetDriverByName("MEM").CreateDataSource("")
    layer = vector_ds.CreateLayer("mask", srs=spatial_ref, geom_type=ogr.wkbPolygon)
    feature = ogr.Feature(layer.GetLayerDefn())
    feature.SetGeometry(geometry.Clone())
    layer.CreateFeature(feature)
    gdal.RasterizeLayer(raster_ds, [1], layer, burn_values=[1])
    mask = raster_ds.GetRasterBand(1).ReadAsArray().astype(bool)
    feature = None
    layer = None
    vector_ds = None
    raster_ds = None
    return mask


def _bbox_geometry(bbox: list[float]):
    ring = ogr.Geometry(ogr.wkbLinearRing)
    ring.AddPoint_2D(bbox[0], bbox[1])
    ring.AddPoint_2D(bbox[2], bbox[1])
    ring.AddPoint_2D(bbox[2], bbox[3])
    ring.AddPoint_2D(bbox[0], bbox[3])
    ring.AddPoint_2D(bbox[0], bbox[1])
    polygon = ogr.Geometry(ogr.wkbPolygon)
    polygon.AddGeometry(ring)
    return polygon


def _resolve_contour_interval(contour_summary: dict[str, Any], contour_interval: str) -> float:
    if contour_interval != "auto":
        return float(contour_interval)

    distinct = contour_summary.get("distinct_elevations") or []
    if len(distinct) < 2:
        return 5.0

    diffs = [round(second - first, 4) for first, second in zip(distinct[:-1], distinct[1:]) if second > first]
    if not diffs:
        return 5.0
    mode_diff, _ = Counter(diffs).most_common(1)[0]
    return float(mode_diff)


def _derive_qa_contours(dem_path: Path, output_path: Path, contour_interval: float) -> Path:
    """Generate QA contours from the derived DEM for visual comparison."""
    output_path.unlink(missing_ok=True)
    dem_ds = gdal.Open(str(dem_path))
    band = dem_ds.GetRasterBand(1)
    nodata = band.GetNoDataValue()

    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromWkt(dem_ds.GetProjection())

    driver = ogr.GetDriverByName("GPKG")
    qa_ds = driver.CreateDataSource(str(output_path))
    qa_layer = qa_ds.CreateLayer("hawkins_qa_contours", srs=spatial_ref, geom_type=ogr.wkbLineString)
    qa_layer.CreateField(ogr.FieldDefn("contour_id", ogr.OFTInteger))
    qa_layer.CreateField(ogr.FieldDefn("elev_m", ogr.OFTReal))

    gdal.ContourGenerate(
        band,
        contour_interval,
        0.0,
        [],
        int(nodata is not None),
        float(nodata or 0.0),
        qa_layer,
        0,
        1,
    )

    qa_layer = None
    qa_ds = None
    band = None
    dem_ds = None
    return output_path


def _qa_alignment_score(dem_path: Path, contour_interval: float, mask_path: Path) -> dict[str, Any]:
    """Compare DEM-derived contours with the pilot contour mask for rough QA."""
    dem_ds = gdal.Open(str(dem_path))
    band = dem_ds.GetRasterBand(1)
    nodata = band.GetNoDataValue()

    contour_mask_ds = gdal.Open(str(mask_path))
    mask_band = contour_mask_ds.GetRasterBand(1)
    mask_array = mask_band.ReadAsArray().astype(bool)

    temp_contours = ogr.GetDriverByName("MEM").CreateDataSource("")
    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromWkt(dem_ds.GetProjection())
    contour_layer = temp_contours.CreateLayer("qa", srs=spatial_ref, geom_type=ogr.wkbLineString)
    contour_layer.CreateField(ogr.FieldDefn("cid", ogr.OFTInteger))
    contour_layer.CreateField(ogr.FieldDefn("elev_m", ogr.OFTReal))

    gdal.ContourGenerate(
        band,
        contour_interval,
        0.0,
        [],
        int(nodata is not None),
        float(nodata or 0.0),
        contour_layer,
        0,
        1,
    )

    raster_ds = gdal.GetDriverByName("MEM").Create(
        "",
        contour_mask_ds.RasterXSize,
        contour_mask_ds.RasterYSize,
        1,
        gdal.GDT_Byte,
    )
    raster_ds.SetGeoTransform(contour_mask_ds.GetGeoTransform())
    raster_ds.SetProjection(contour_mask_ds.GetProjection())
    gdal.RasterizeLayer(raster_ds, [1], contour_layer, burn_values=[1])
    contour_array = raster_ds.GetRasterBand(1).ReadAsArray().astype(bool)

    overlap = int(np.count_nonzero(contour_array & mask_array))
    contour_pixels = int(np.count_nonzero(contour_array))
    mask_pixels = int(np.count_nonzero(mask_array))
    precision = overlap / contour_pixels if contour_pixels else 0.0
    recall = overlap / mask_pixels if mask_pixels else 0.0

    raster_ds = None
    contour_layer = None
    temp_contours = None
    mask_band = None
    contour_mask_ds = None
    band = None
    dem_ds = None

    return {
        "overlap_pixels": overlap,
        "contour_pixels": contour_pixels,
        "mask_pixels": mask_pixels,
        "precision": precision,
        "recall": recall,
    }


def _build_qgis_project(
    project_path: Path,
    title: str,
    source_raster: Path,
    contour_gpkg: Path,
    aoi_gpkg: Path,
    aoi_layer_name: str,
    dem_path: Path | None,
    qa_gpkg: Path | None,
    include_3d: bool,
    extra_rasters: list[Path | None],
) -> Path:
    """Write a QGIS project that loads the current Hawkins work products."""
    project = QgsProject.instance()
    project.clear()
    project.setTitle(title)

    root = project.layerTreeRoot()
    layers_for_3d: list = []

    source_layer = QgsRasterLayer(str(source_raster), source_raster.stem)
    if not source_layer.isValid():
        raise ValueError(f"Failed to load raster layer {source_raster}")
    project.addMapLayer(source_layer, addToLegend=False)
    root.insertLayer(0, source_layer)
    layers_for_3d.append(source_layer)

    aoi_layer = QgsVectorLayer(
        f"{aoi_gpkg}|layername={aoi_layer_name}",
        "hawkins_aoi",
        "ogr",
    )
    if aoi_layer.isValid():
        aoi_symbol = QgsFillSymbol.createSimple(
            {
                "color": "0,0,0,0",
                "outline_color": QColor("#aa1e1e").name(),
                "outline_width": "0.6",
            }
        )
        aoi_layer.setRenderer(QgsSingleSymbolRenderer(aoi_symbol))
        project.addMapLayer(aoi_layer, addToLegend=False)
        root.insertLayer(0, aoi_layer)

    candidate_layer = QgsVectorLayer(
        f"{contour_gpkg}|layername=hawkins_contours_candidates",
        "hawkins_contours_candidates",
        "ogr",
    )
    if candidate_layer.isValid():
        candidate_symbol = QgsLineSymbol.createSimple(
            {
                "color": "#d97706",
                "width": "0.38",
                "line_style": "dash",
            }
        )
        candidate_layer.setRenderer(QgsSingleSymbolRenderer(candidate_symbol))
        project.addMapLayer(candidate_layer, addToLegend=False)
        root.insertLayer(0, candidate_layer)

    contour_layer = QgsVectorLayer(
        f"{contour_gpkg}|layername=hawkins_contours_manual",
        "hawkins_contours_manual",
        "ogr",
    )
    if contour_layer.isValid():
        contour_symbol = QgsLineSymbol.createSimple({"color": "#7f1d1d", "width": "0.45"})
        contour_layer.setRenderer(QgsSingleSymbolRenderer(contour_symbol))
        project.addMapLayer(contour_layer, addToLegend=False)
        root.insertLayer(0, contour_layer)
        layers_for_3d.append(contour_layer)

    vetted_layer = QgsVectorLayer(
        f"{contour_gpkg}|layername=hawkins_contours",
        "hawkins_contours",
        "ogr",
    )
    if vetted_layer.isValid():
        vetted_symbol = QgsLineSymbol.createSimple({"color": "#b91c1c", "width": "0.55"})
        vetted_layer.setRenderer(QgsSingleSymbolRenderer(vetted_symbol))
        project.addMapLayer(vetted_layer, addToLegend=False)
        root.insertLayer(0, vetted_layer)
        layers_for_3d.append(vetted_layer)

    qa_layer = None
    if qa_gpkg is not None:
        qa_layer = QgsVectorLayer(
            f"{qa_gpkg}|layername=hawkins_qa_contours",
            "hawkins_qa_contours",
            "ogr",
        )
        if qa_layer.isValid():
            qa_symbol = QgsLineSymbol.createSimple({"color": "#2563eb", "width": "0.35"})
            qa_layer.setRenderer(QgsSingleSymbolRenderer(qa_symbol))
            project.addMapLayer(qa_layer, addToLegend=False)
            root.insertLayer(0, qa_layer)
            layers_for_3d.append(qa_layer)

    for extra_raster in [path for path in extra_rasters if path]:
        layer = QgsRasterLayer(str(extra_raster), extra_raster.stem)
        if not layer.isValid():
            continue
        project.addMapLayer(layer, addToLegend=False)
        root.insertLayer(0, layer)
        root.findLayer(layer.id()).setItemVisibilityChecked(False)

    for reference_path in _reference_dem_paths():
        layer = QgsRasterLayer(str(reference_path), reference_path.stem)
        if not layer.isValid():
            continue
        project.addMapLayer(layer, addToLegend=False)
        root.insertLayer(0, layer)
        root.findLayer(layer.id()).setItemVisibilityChecked(False)

    project.setCrs(source_layer.crs())

    if dem_path is not None:
        dem_layer = QgsRasterLayer(str(dem_path), "Hawkins_Derived_DEM")
        if not dem_layer.isValid():
            raise ValueError(f"Failed to load DEM layer {dem_path}")
        project.addMapLayer(dem_layer, addToLegend=False)
        root.insertLayer(0, dem_layer)
        root.findLayer(dem_layer.id()).setItemVisibilityChecked(False)

        terrain_provider = qcore.QgsRasterDemTerrainProvider()
        terrain_provider.setLayer(dem_layer)
        terrain_provider.setOffset(0.0)
        terrain_provider.setScale(1.0)
        project.elevationProperties().setTerrainProvider(terrain_provider)
    else:
        dem_layer = None

    project.write(str(project_path))

    if include_3d and dem_layer is not None:
        extent = _project_extent_from_layer(aoi_layer if aoi_layer.isValid() else source_layer)
        _inject_3d_view(project_path, project.crs(), dem_layer, layers_for_3d, extent)

    return project_path


def _reference_dem_paths() -> list[Path]:
    """Return modern DEMs that may be used as reference-only calibration aids."""
    repo_root = find_repo_root(CURRENT_DIR)
    candidates = [
        repo_root / "data/tif/USGS_one_meter_x32y431_MD_VA_Sandy_NCR_2014.tif",
        repo_root / "data/tif/dc_dem.tif",
    ]
    return [candidate for candidate in candidates if candidate.exists()]


def _project_extent_from_layer(layer) -> QgsRectangle:
    extent = layer.extent()
    return QgsRectangle(extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum())


def _inject_3d_view(project_path: Path, crs: QgsCoordinateReferenceSystem, dem_layer, layers_for_3d: list, extent: QgsRectangle) -> None:
    """Inject a predefined 3D map dock into a written QGIS project file."""
    terrain = q3d.QgsDemTerrainSettings()
    terrain.setLayer(dem_layer)
    terrain.setResolution(8)
    terrain.setSkirtHeight(10)
    terrain.setMaximumGroundError(1.0)
    terrain.setMaximumScreenError(3.0)
    terrain.setVerticalScale(1.0)

    settings = q3d.Qgs3DMapSettings()
    settings.setCrs(crs)
    settings.setExtent(extent)
    settings.setOrigin(QgsVector3D(extent.center().x(), extent.center().y(), 0.0))
    settings.setBackgroundColor(QColor("#f3eee2"))
    settings.setSelectionColor(QColor("#fff200"))
    settings.setLayers([layer for layer in layers_for_3d if layer.isValid()])
    settings.setTerrainSettings(terrain)
    settings.setTerrainRenderingEnabled(True)
    settings.setTerrainShadingEnabled(False)
    settings.setShowLabels(False)
    settings.setEyeDomeLightingEnabled(True)
    settings.setEyeDomeLightingStrength(1000)
    settings.setEyeDomeLightingDistance(1)

    document = QDomDocument()
    qgis3d_element = settings.writeXml(document, QgsReadWriteContext())
    document.appendChild(qgis3d_element)
    qgis3d_xml = document.toString(2).strip()

    center = extent.center()
    distance = max(extent.width(), extent.height()) * 1.6
    camera_xml = (
        f'      <camera dist="{distance:.3f}" pitch="-38" '
        f'xMap="{center.x():.6f}" yMap="{center.y():.6f}" yaw="28" zMap="0"/>'
    )
    animation_xml = (
        "      <animation3d interpolation=\"0\" widget-visible=\"0\">\n"
        "        <keyframes>\n"
        f'          <keyframe dist="{distance:.3f}" pitch="-38" time="0" '
        f'x="{center.x():.6f}" y="{center.y():.6f}" yaw="28" z="0"/>\n'
        f'          <keyframe dist="{distance * 1.12:.3f}" pitch="-52" time="5" '
        f'x="{center.x():.6f}" y="{center.y():.6f}" yaw="210" z="0"/>\n'
        "        </keyframes>\n"
        "      </animation3d>"
    )
    view_block = (
        "  <mapViewDocks3D>\n"
        f'    <view area="2" d_height="900" d_width="1200" d_x="120" d_y="120" '
        f'floating="0" height="460" isDocked="1" isOpen="1" name="Hawkins 3D" '
        f'uuid="{{{uuid.uuid4()}}}" width="720" x="880" y="140">\n'
        + _indent_xml(qgis3d_xml, 6)
        + "\n"
        + camera_xml
        + "\n"
        + animation_xml
        + "\n      <tab_siblings/>\n"
        "    </view>\n"
        "  </mapViewDocks3D>"
    )

    text = project_path.read_text(encoding="utf-8")
    if "<mapViewDocks/>" in text:
        text = text.replace("<mapViewDocks/>", view_block, 1)
    elif "<mapViewDocks3D/>" in text:
        text = text.replace("<mapViewDocks3D/>", view_block, 1)
    elif "<mapViewDocks3D>" in text:
        text = re.sub(r"<mapViewDocks3D>.*?</mapViewDocks3D>", view_block, text, count=1, flags=re.S)
    else:
        text = text.replace("<main-annotation-layer", view_block + "\n  <main-annotation-layer", 1)
    text = text.replace("<mapViewDocks3D/>", "")
    project_path.write_text(text, encoding="utf-8")


def _indent_xml(text: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(prefix + line if line else line for line in text.splitlines())


def _count_runs(line: np.ndarray, min_length: int) -> int:
    values = np.asarray(line, dtype=np.uint8)
    padded = np.concatenate(([0], values, [0]))
    transitions = np.diff(padded)
    starts = np.where(transitions == 1)[0]
    ends = np.where(transitions == -1)[0]
    run_lengths = ends - starts
    return int(np.sum(run_lengths >= min_length))


def _write_json(output_path: Path, data: dict[str, Any]) -> None:
    output_path.write_text(json.dumps(data, indent=2, default=_json_default), encoding="utf-8")


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    return value
