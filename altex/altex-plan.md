# Hawkins contour extraction: recover and correct the plan

## Implementation status

The first-milestone CLI is implemented: `synth`, `tiles`, `train`, `segment`, and `trace`. See [README.md](README.md) for commands, review formats, products, and limitations. Tests cover topology, explicit repairs, spatial holdouts, georeferencing, overlap blending, transparency, and a training/inference smoke run.

The source GeoTIFF is present. PyTorch with CUDA and scikit-image are installed in `geodenv`. Training reports distinguish synthetic validation from reviewed Hawkins validation; generated model outputs remain candidates until the map-image acceptance checks below pass.

The first implementation milestone will be **clean, countable contours**. Altitude assignment follows review of those results.

The runtime pilot and limited clear-contour checks are recorded in [PILOT.md](PILOT.md). The implementation is available, but dense-slope and distractor accuracy has not passed acceptance; full-map extraction remains deferred.

## Changes to the design

- Treat Hawkins as a modern reconstruction of **historical terrain**. Exclude `dc_dem`, `us_geo`/USGS rasters, and other modern elevation data from training, direction hints, interval estimation, and accuracy assessment.
- Replace DEM-derived training examples with procedural contour drawings: nested hills, valleys, saddles, and closely spaced curves, rendered with Hawkins-like paper, ink, clutter, and degradation.
- Supplement synthetic training with editable Hawkins tile labels. Heuristic labels are provisional; reviewed Hawkins tiles provide real-image validation. Keep validation areas geographically separate from training tiles.
- Retain the four segmentation classes: paper, contour, water, and other ink, plus an ignored label for uncertain pixels. Water recognition supplies image context; it does not automatically establish sea level.
- Train segmentation against visible contour strokes. Preserve separate evidence for inferred connections across missing or obscured ink.
- Defer contour levels, uphill/downhill decisions, interval, datum, and elevation output. Image spacing alone does not establish the vertical interval.

## First implementation milestone

- Follow the repository’s `python -m altex` convention, using rasterio independently of PyQGIS. Implement `synth`, `tiles`, `train`, `segment`, and `trace`; retain source selection and pixel-window AOIs.
- Use the saved U-Net approach with overlapping, blended inference. Export class probabilities and labels on the source grid, respecting transparent source pixels.
- Skeletonize and trace contour predictions while preserving close parallel lines. Split traces at junctions and flag ambiguous intersections, endpoints, and possible merges. Fragment IDs must not be presented as complete-contour identities.
- Generate conservative gap-repair proposals using endpoint direction, distance, and image evidence. Export proposals separately; apply only explicitly accepted repairs. Support corrected label masks as inputs to retracing.
- Produce georeferenced contour masks, traced GeoPackage lines, repair proposals, source overlays, and a diagnostic report. Include fragmentation and ambiguity diagnostics without claiming a fragment count is the number of contours.
- Update the saved plan, package documentation, dependencies, and CLI help together. Document the historical-terrain constraint and the review workflow.

## Validation and later stages

- Test synthetic rings, close parallel contours, broken strokes, distracting streams/text, junctions, transparency, and AOI boundaries. Check that repairs cannot silently merge neighboring contours.
- Verify raster/vector alignment and inference continuity across tile boundaries.
- Evaluate on reviewed Hawkins tiles using contour precision/recall, separation, and crossing counts along annotated transects. Readable transects must match manual counts; ambiguous sections must remain flagged.
- Run a small training/inference pilot before full-map processing, then focused tests and the repository suite. Modern DEM agreement is never an acceptance criterion.
- After extraction review, plan relative levels and explicit assumed contour spacing. Provide **both flat terraces and an optional interpolated terrain view**, clearly identifying assumed heights and unresolved direction or datum.
- After implementation, provide a copyable plain Markdown commit summary as requested.
