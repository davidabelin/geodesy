# Initial Hawkins pilot — 2026-09-24

The implemented workflow runs end to end on the NVIDIA GPU. **The current
model is a review candidate, not an accepted full-map extraction.** Clear
contours are often followed well; dense slopes, texture, lettering, and stream
intersections still produce false branches and ambiguous fragments.

## Runs performed

- Exported eight procedural preview tiles and 24 Hawkins tiles (18 training,
  six spatially held-out validation tiles).
- Completed a two-epoch smoke run and AOI segmentation/tracing. The initial
  smoke checkpoint did not extract useful contours; this was a runtime check.
- Trained a width-16 U-Net for 20 epochs, 128 tiles per epoch, size 256, batch 4,
  seed 0, CUDA, combining procedural examples and provisional Hawkins labels.
  At training time no Hawkins validation tiles had been marked reviewed.
- Best held-out **synthetic** contour F1 was approximately 0.992. Checkpoint
  selection was synthetic-only; this number does not establish map accuracy.
- Segmented and traced source AOI `3800,2600,1200,1200`, using 512-pixel
  inference windows and 128-pixel overlap. The result had 18,547 fragments,
  889 connected components, and 132 proposed repairs. No repairs were accepted.
  These are fragmentation diagnostics, not contour counts.

Local generated products are ignored by Git:

- `out/hawkins_model/best.pt` and `training.json`
- `out/hawkins_pilot/trace/qa_overlay.tif`, `lines.gpkg`, and `report.json`
- `out/hawkins_holdout/` for the separate validation window
- `train_data/real/` for editable labels and the review manifest

## Limited image-grounded checks

The held-out source window `4608,1536,512,512` was not used for training.
Two transects were manually counted on its source image, then checked against
the extraction. Coordinates below are local to that window.

| Start | End | Manual count | Extracted crossings | Result |
| --- | --- | --- | --- | --- |
| (475, 75) | (475, 330) | 3 | 3 | Passed, unambiguous |
| (300, 140) | (300, 280) | 1 | 1 | Passed, unambiguous |

A sparse reference within x=445:505, y=80:195 was prepared using conservative
color thresholds and visually reviewed against the source image. It labels
two clear contour cores and clear paper, leaving uncertain boundaries and all
other pixels ignored. Only after review was this validation tile marked
reviewed in the local manifest. No training was rerun using it.

This reference contains 6,529 evaluated pixels, including 296 contour pixels.
Contour precision and recall were both 1.0 on this small, deliberately clear
sample. It **does not** measure boundary accuracy, dense-contour separation,
water recognition, stream rejection, or whole-tile accuracy. The local
`reviewed_metrics.json` records this limitation explicitly.

## Remaining acceptance work

Review and correct representative training/validation labels covering dense
slopes, water, streams, text, and broken strokes; retrain and repeat image-based
metrics and transect checks. Keep validation geographically separate. Inspect
proposed repairs individually. Full-map processing and altitude reconstruction
remain deferred until these checks support trustworthy contour counting.

No modern elevation rasters were read by the altex pilot. The repository's
separate, existing coverage tests exercise their own DEM-based workflow.
