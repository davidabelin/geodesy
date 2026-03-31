# Hawkins 3D Handoff

## Goal

Build a QGIS Desktop workflow that can test whether the Hawkins topographic map can support a Hawkins-derived 3D terrain surface, without treating the scanned map image itself as a real DEM.

## Authoritative source raster

- Primary raster: `qgis/Hawkins/Hawkins_Topography_unzip/Hawkins_Topography.img`
- This is the correct source going forward.
- `Hawkins_Georeferenced.tif` was an old manual fit and the code no longer depends on it.

## Runtime assumption

- Target runtime: OSGeo4W / QGIS Desktop 4.0.0
- Launcher used by the pipeline: `C:\Users\David\AppData\Local\Programs\OSGeo4W\bin\python-qgis.bat`
- Runner batch file: `qgis/run_hawkins_3d_pipeline.bat`

## What the pipeline does now

### Modes

- `audit`
  - reads the Hawkins raster
  - computes preview masks
  - scores candidate pilot AOIs
  - writes a JSON report and preview images

- `pilot`
  - works on the auto-selected or manually specified AOI
  - creates/updates the work-package GeoPackage
  - writes mask rasters and a QGIS workbench project
  - auto-extracts candidate contour linework for review
  - samples those candidates against a modern DEM for guidance only

- `full`
  - builds the work package and workbench project for the full Hawkins extent
  - currently skips full-extent auto-extraction because the heuristics are only tuned for pilot-scale review

## Work package layers

The GeoPackage is written to `qgis/Hawkins/3D map/hawkins_work.gpkg`.

- `hawkins_aoi`
  - pilot/full area-of-interest polygon

- `hawkins_contours_candidates`
  - auto-generated guide lines from the current pilot extraction pass
  - not final contour truth
  - includes modern DEM guide fields such as:
    - `modern_dem`
    - `modern_min_m`
    - `modern_med_m`
    - `modern_max_m`
    - `modern_rank`

- `hawkins_contours_manual`
  - manual/vetted contour lines intended for DEM generation

- `hawkins_labels_manual`
  - manual point annotations for label/elevation text

## Important correction

The dashed candidate lines are not historical elevations. They are only the current script's best guess at contour-like ink.

The scan can be viewed in QGIS 3D as a raster DEM, but that only embosses image tones. It is visually suggestive and not a valid terrain extraction by itself.

## What changed today

1. Retargeted the workflow to the real Hawkins source raster:
   - `Hawkins_Topography.img`

2. Retargeted the runtime to QGIS 4.0.0:
   - `python-qgis.bat`
   - QGIS project outputs are now generated as `version="4.0.0-Norrköping"`

3. Updated output defaults:
   - pipeline output now goes to `qgis/Hawkins/3D map`

4. Added pilot auto-extraction of contour candidates:
   - stricter contour-ink mask
   - skeletonization and simple line tracing
   - rejection of branch-heavy/noisy components
   - sampling against overlapping local modern DEMs

5. Loaded candidate guides directly into the generated QGIS workbench project.

## Current verified behavior

- `audit` runs successfully under QGIS 4.0.0
- `pilot` runs successfully under QGIS 4.0.0 and produces candidate contours
- `full` runs successfully under QGIS 4.0.0 but skips auto-extraction because the extent is too large for the current heuristic pass

Pilot extraction currently produced:

- `86` candidate contour features in a temp validation run
- all sampled against `USGS_one_meter_x32y431_MD_VA_Sandy_NCR_2014`

## Known limitations

1. The auto-extracted candidate lines are noisy and incomplete.
   - They are useful for review, not final use.

2. The pipeline does not yet know historical contour interval/datum/units.
   - Vertical control is still the core unsolved problem.

3. The pipeline does not yet auto-assign real historical elevations.
   - It only attaches modern DEM hints.

4. Windows file locking matters.
   - If QGIS has the workbench project open, rerunning the pipeline against `qgis/Hawkins/3D map` can fail because mask `.tif` files are locked.
   - Close the Hawkins workbench project before rerunning the batch file.

## How to rerun

From the repo root:

```bat
qgis\run_hawkins_3d_pipeline.bat --mode audit
qgis\run_hawkins_3d_pipeline.bat --mode pilot
qgis\run_hawkins_3d_pipeline.bat --mode full
```

From the QGIS Python console:

```python
from hawkins_3d_console_helper import run_hawkins
run_hawkins("pilot")
```

## Best restart point for tomorrow

Do not start by tracing the whole map.

The next conversation should start from this question:

> How should vertical control be established for Hawkins candidate contours?

Practical follow-up options:

- identify a reliable source for Hawkins contour interval and units
- identify a small set of historical elevation anchors that can be located on the map
- improve candidate filtering so the pilot workbench shows only the strongest likely contours
- build a label-to-candidate matching step if visible printed contour labels exist on the map

## Files most worth reading first tomorrow

- `qgis/Hawkins/HAWKINS_3D_HANDOFF.md`
- `qgis/hawkins_3d_core.py`
- `qgis/hawkins_3d_pipeline.py`
- `qgis/qgis_runtime.py`
