# altex: historical contour extraction

Hawkins depicts reconstructed **historical terrain**, not present-day heights.
`altex` learns visible contour ink, separates it from other marks, and exports
line fragments for review and counting. It never reads modern altitude rasters
for training, hints, calibration, or accuracy assessment. There are no elevation
arguments, sea-level assumptions, or altitude outputs.

This first milestone provides `synth`, `tiles`, `train`, `segment`, and `trace`.
The model is a four-class U-Net. Results require image-grounded review before
counting contours or proceeding to altitude reconstruction.

See [PILOT.md](PILOT.md) for the initial GPU run, limited image-grounded checks,
and remaining model-quality limitations.

## Setup

Use the repository's `geodenv` interpreter; PyQGIS is unnecessary.

```powershell
.\geodenv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu128
.\geodenv\Scripts\python.exe -m pip install -r requirements.txt
.\geodenv\Scripts\python.exe -m altex --help
```

For a CPU-only environment, install PyTorch using its CPU distribution instead.
`--device auto` selects CUDA when available; `--device cpu` is supported. The
default batch is 4, training tile size 512, and model width 32. Reduce `--batch`,
`--size`, or `--width` for a smaller GPU. The width is stored in each checkpoint.
`cdem.bat` invokes the same CLI with the repository interpreter.

The default source is `qgis/Hawkins/hawkins10000.tif`, a georeferenced uint8
RGB/RGBA map. Transparent pixels are excluded. Single-band elevation rasters
are rejected as image inputs.

## Prepare and review data

```powershell
.\geodenv\Scripts\python.exe -m altex synth --count 8 --out altex/out/synthetic_preview
.\geodenv\Scripts\python.exe -m altex tiles --count 24 --out altex/train_data/real
```

Synthetic examples are drawn from analytic curves for hills, valleys, saddles,
and parallel slopes. Their colors resemble Hawkins, with paper noise, blur,
water-like shading, text, stream strokes, red dashes, and vegetation symbols.
They do not derive from measured terrain. Erasures and occluding marks remove
the corresponding contour labels; hidden continuations are not training truth.

`tiles` creates RGB PNGs, editable indexed label PNGs, and `manifest.json`:

| Class ID | Meaning | Preview color |
| --- | --- | --- |
| 0 | Paper | Tan |
| 1 | Visible contour stroke | Red |
| 2 | Water appearance | Blue |
| 3 | Other ink | Dark grey |
| 255 | Unknown / ignored | Magenta |

Correct labels in an image editor while preserving indexed class IDs (or export
grayscale values 0/1/2/3/255). RGB-colored label images are rejected. Mark
uncertain pixels 255. Review the source image, not a DEM. Set `reviewed: true`
for each corrected tile in the manifest only after checking its labels.

The rightmost quarter of full tile columns is reserved for validation; training
uses the remaining columns. Tiles never overlap. Keep the split fixed; training
rejects overlapping training/validation windows or reused files. Provisional
training labels receive weight 0.2; reviewed training labels receive weight 1.
Only reviewed validation tiles contribute real-image metrics. Unreviewed
validation tiles are excluded from training and evaluation.

Heuristics label only strong paper, brown ink, and some distractors. They do not
claim to distinguish ambiguous water. Synthetic water and reviewed examples
provide that supervision. Existing tile directories are protected from export
overwrites so corrections survive.

## Train and segment

```powershell
.\geodenv\Scripts\python.exe -m altex train --real altex/train_data/real --epochs 30 --out altex/out/model
.\geodenv\Scripts\python.exe -m altex segment --checkpoint altex/out/model/best.pt --aoi 3800,2600,1200,1200 --out altex/out/pilot
```

`--aoi x,y,width,height` uses source pixels. Omit it for the full map, after the
pilot passes review. `tiles` also supports AOIs. The segmentation default is
1024-pixel windows with 128-pixel overlap, Hann blending, and optional `--tta`
horizontal-flip averaging. Smaller edge windows are padded. Accumulation uses
temporary disk files, requiring about 20 bytes per AOI pixel, plus output space.

Training uses AdamW, cosine learning-rate decay, masked cross-entropy plus
contour Dice loss, and CUDA mixed precision. Every epoch writes `last.pt`, the
best checkpoint, preview images, and `training.json`. Checkpoint selection uses
reviewed Hawkins contour F1 when available, otherwise explicitly labeled
synthetic-only F1. A run without reviewed validation says
`NO_REVIEWED_HAWKINS_VALIDATION`; synthetic accuracy does not establish Hawkins
accuracy. Choose a new directory for each training or segmentation run.

Segmentation produces:

- `source.tif`: the cropped RGBA map, preserving the source transform and CRS.
- `probabilities.tif`: four float32 class probabilities, with a validity mask.
- `classes.tif`: class IDs with nodata 255.
- `segment.json`: source window, checkpoint, inference settings, and provenance.

These are predictions, not reviewed labels. Model probabilities are not
calibrated confidence in contour identity or elevation.

## Trace, correct, and explicitly accept repairs

```powershell
.\geodenv\Scripts\python.exe -m altex trace --run-dir altex/out/pilot
.\geodenv\Scripts\python.exe -m altex trace --run-dir altex/out/pilot --labels corrected.tif --out altex/out/pilot/corrected
```

Without `--labels`, tracing uses contour-probability hysteresis (0.3/0.6),
excluding pixels classified as water or other ink. Corrected labels override
predictions entirely. A corrected GeoTIFF must match the run grid exactly; an
indexed PNG must match its dimensions and is interpreted on that same grid.
This also allows tracing without a model: supply an RGBA `source.tif` in a run
directory and pass a matching corrected label image with `--labels`.

Skeletons preserve small fragments and split at junctions. Short fragments,
isolated pixels, open ends, and junctions remain visible for review. There is
no pruning that silently deletes a possible contour. Fragment IDs are local
to each tracing result and are **not complete-contour identities**.

Gap proposals use endpoint tangents (30-degree tolerance), distance (default
12 pixels), and image darkness along the gap. Proposals crossing invalid pixels
or touching other linework are rejected. The default minimum image evidence is
0.05; `--min-evidence 0` also permits proposals across blank erasures. Darkness
can come from clutter, so every repair requires explicit acceptance.

1. Inspect `qa_overlay.tif` and the `repair_proposals` layer in `lines.gpkg`.
2. Copy `repair_review.template.json` to a separately named review file.
3. Add chosen IDs from `repair_proposals.json` to its `accepted_ids` list.
4. Rerun with the same labels/settings and `--accept-repairs your_review.json`.

The review's revision hash must match the current image, skeleton, validity,
and proposals. Stale IDs, shared endpoints, crossing repairs, and repairs
touching other linework are rejected. No proposal is applied by default.

Tracing writes to `RUN_DIR/trace` unless `--out` is supplied. Repeated tracing
replaces its generated products; store edited labels and review files under
separate names. Close QGIS layers before replacing an open GeoPackage.

| Product | Purpose |
| --- | --- |
| `observed_ink.tif` | Selected stroke pixels before thinning |
| `observed_skeleton.tif` | Centerlines before any repair |
| `contour_mask.tif` | Centerlines plus explicitly accepted repairs |
| `inferred_mask.tif` | Only newly added repair pixels |
| `ambiguity.tif` | Endpoint/junction/unknown/repair neighborhoods |
| `lines.gpkg` | `fragments`, `repair_proposals`, and `diagnostics` layers |
| `qa_overlay.png` / `.tif` | Cyan observed lines, orange repairs, magenta diagnostics |
| `report.json` | Fragmentation, repair provenance, optional crossing checks |

Raster and vector products share the source grid/CRS. Vectors pass through
pixel centers. Mask rasters use 0/1 with a separate validity mask. Counts of
fragments or connected components must not be interpreted as contour counts.

## Counting and acceptance

For image-reviewed transects, pass `trace --transects transects.json`. The JSON
is a list such as:

```json
[{"id":"slope-A","start":[20,100],"end":[200,100],"expected":7,"readable":true}]
```

Coordinates are integer pixel centers local to the cropped run. Draw transects
across contours, not along them. `expected` is a manual source-image count;
`readable` records whether the source supports a reliable count. Reports count
line crossings, not distinct contours across the entire map.
Crossings are calculated from traced pixel-center polylines, including diagonal
crossings between pixel centers. Endpoints, junctions, unknown pixels, repair
neighborhoods, tangencies, along-line overlaps, and source-marked unreadable transects
produce an ambiguous result.
Readable, unambiguous transects must match exactly. A mismatch fails that
transect; an ambiguous result is not a pass.

Use reviewed Hawkins validation tiles for precision/recall and per-class IoU.
Inspect dense parallel contours for merges and missing lines, and verify
crossing counts in representative map areas before full-map processing. No
modern DEM correlation or elevation error metric is relevant to acceptance.

```powershell
.\geodenv\Scripts\python.exe -m pytest tests/test_altex.py -q
.\geodenv\Scripts\python.exe -m pytest tests -q
```

## Later stages

Relative levels, uphill/downhill interpretation, contour interval, datum, and
altitudes remain deferred until extraction is reviewed. Image spacing cannot
determine the vertical interval. Future rendering will provide both flat
terraces and an optional interpolated terrain view, explicitly distinguishing
assumed heights and unresolved historical evidence.
