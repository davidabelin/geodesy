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


@dataclass
class Hawkins3DConfig:
    mode: str
    source_raster: Path
    fallback_raster: Path
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
    repo_root = find_repo_root(CURRENT_DIR)
    hawkins_dir = repo_root / "qgis/Hawkins"
    resolved_output_dir = output_dir or (hawkins_dir / "generated")
    resolved_source = source_raster or (hawkins_dir / "Hawkins_Georeferenced.tif")
    return Hawkins3DConfig(
        mode=mode,
        source_raster=resolved_source,
        fallback_raster=hawkins_dir / "Hawkins_Topography_unzip/Hawkins_Topography.img",
        output_dir=resolved_output_dir,
        aoi=aoi,
        contour_interval=contour_interval,
        z_unit=z_unit,
        manual_package=resolved_output_dir / "hawkins_work.gpkg",
    )


def run_pipeline(config: Hawkins3DConfig) -> dict[str, Any]:
    app, created = init_qgis_app(gui=False)
    try:
        return _run_pipeline(config)
    finally:
        shutdown_qgis_app(app, created)


def _run_pipeline(config: Hawkins3DConfig) -> dict[str, Any]:
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
        report["notes"].extend(
            [
                "The work package is ready for tracing and label attribution in QGIS Desktop.",
                "Populate hawkins_contours_manual.elev_m for traced contours, then rerun pilot/full mode.",
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
    candidates = [config.source_raster, config.fallback_raster]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError(
        "Neither the primary Hawkins raster nor the fallback raster exists: "
        + ", ".join(str(candidate) for candidate in candidates)
    )


def _output_paths(mode: str, output_dir: Path) -> dict[str, Path]:
    prefix = f"hawkins_{mode}"
    return {
        "report_json": output_dir / f"{prefix}_report.json",
        "source_preview_png": output_dir / f"{prefix}_source_preview.png",
        "preview_contour_png": output_dir / f"{prefix}_contour_preview.png",
        "preview_index_png": output_dir / f"{prefix}_index_preview.png",
        "crop_preview_png": output_dir / f"{prefix}_crop_preview.png",
        "contour_mask_tif": output_dir / f"{prefix}_contour_mask.tif",
        "index_mask_tif": output_dir / f"{prefix}_index_mask.tif",
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


def _manual_contour_summary(work_package: Path, aoi_bbox: list[float], config: Hawkins3DConfig) -> dict[str, Any]:
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
    project = QgsProject.instance()
    project.clear()
    project.setTitle(title)

    root = project.layerTreeRoot()
    layers_for_3d: list = []

    source_layer = QgsRasterLayer(str(source_raster), "Hawkins_Georeferenced")
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
