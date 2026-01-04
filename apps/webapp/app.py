from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

from flask import Flask, abort, redirect, render_template, request, send_from_directory, url_for

from .jobs import JobStore
from .runner import JobRunner


def repo_root() -> Path:
    # apps/webapp/app.py -> apps/webapp -> apps -> repo root
    return Path(__file__).resolve().parents[2]


ALLOWED_DOWNLOAD_ROOTS = {
    "roadways_out": repo_root() / "roadways" / "out",
    "highpoints_out": repo_root() / "highpoints" / "out",
    "spirals_out": repo_root() / "spirals" / "out",
}


def create_app() -> Flask:
    app = Flask(__name__)
    job_store = JobStore(repo_root() / "apps" / "webapp" / "var" / "jobs.sqlite3")
    runner = JobRunner(job_store=job_store, cwd=repo_root())

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/roadways")
    def roadways():
        return render_template("roadways.html")

    def _submit(argv: List[str], outputs: Optional[Dict[str, str]] = None):
        job_id = job_store.create_job(argv=argv, outputs=outputs or {})
        runner.submit(job_id)
        return redirect(url_for("job_detail", job_id=job_id))

    @app.get("/roadways/dc-centerlines")
    def roadways_dc_centerlines_form():
        return render_template("roadways_dc_centerlines.html")

    @app.post("/roadways/dc-centerlines")
    def roadways_dc_centerlines_submit():
        argv = [
            "-m",
            "roadways",
            "dc-centerlines",
            "--streettype",
            request.form.get("streettype", "").strip(),
            "--roadtype",
            request.form.get("roadtype", "Street").strip(),
            "--style",
            request.form.get("style", "dc-street-v1").strip(),
            "--line-width",
            request.form.get("line_width", "1").strip(),
            "--output-kml",
            request.form.get("output_kml", "centerlines.kml").strip(),
        ]
        ew_csv = request.form.get("ew_csv", "").strip()
        ns_csv = request.form.get("ns_csv", "").strip()
        if ew_csv:
            argv += ["--ew-csv", ew_csv]
        if ns_csv:
            argv += ["--ns-csv", ns_csv]
        if request.form.get("keep_other"):
            argv.append("--keep-other")
        snap_gap_m = request.form.get("snap_gap_m", "").strip()
        snap_gap_frac = request.form.get("snap_gap_frac", "").strip()
        if snap_gap_m:
            argv += ["--snap-gap-m", snap_gap_m]
        if snap_gap_frac:
            argv += ["--snap-gap-frac", snap_gap_frac]

        outputs = {
            "kml": request.form.get("output_kml", "centerlines.kml").strip(),
        }
        if ew_csv:
            outputs["ew_csv"] = ew_csv
        if ns_csv:
            outputs["ns_csv"] = ns_csv
        return _submit(argv, outputs=outputs)

    @app.get("/roadways/dc-roadways")
    def roadways_dc_roadways_form():
        return render_template("roadways_dc_roadways.html")

    @app.post("/roadways/dc-roadways")
    def roadways_dc_roadways_submit():
        argv = [
            "-m",
            "roadways",
            "dc-roadways",
            "--road-type",
            request.form.get("road_type", "ST").strip(),
            "--style",
            request.form.get("style", "dc-street-v1").strip(),
            "--line-width",
            request.form.get("line_width", "1").strip(),
            "--output-kml",
            request.form.get("output_kml", "classified_roadways.kml").strip(),
        ]
        if request.form.get("keep_other"):
            argv.append("--keep-other")
        ew_csv = request.form.get("ew_csv", "").strip()
        ns_csv = request.form.get("ns_csv", "").strip()
        if ew_csv:
            argv += ["--ew-csv", ew_csv]
        if ns_csv:
            argv += ["--ns-csv", ns_csv]
        snap_gap_m = request.form.get("snap_gap_m", "").strip()
        snap_gap_frac = request.form.get("snap_gap_frac", "").strip()
        if snap_gap_m:
            argv += ["--snap-gap-m", snap_gap_m]
        if snap_gap_frac:
            argv += ["--snap-gap-frac", snap_gap_frac]

        outputs = {"kml": request.form.get("output_kml", "classified_roadways.kml").strip()}
        if ew_csv:
            outputs["ew_csv"] = ew_csv
        if ns_csv:
            outputs["ns_csv"] = ns_csv
        return _submit(argv, outputs=outputs)

    @app.get("/roadways/intersections")
    def roadways_intersections_form():
        return render_template("roadways_intersections.html")

    @app.post("/roadways/intersections")
    def roadways_intersections_submit():
        argv = [
            "-m",
            "roadways",
            "intersections",
            "--a-input",
            request.form.get("a_input", "").strip(),
            "--b-input",
            request.form.get("b_input", "").strip(),
            "--a-field",
            request.form.get("a_field", "").strip(),
            "--a-value",
            request.form.get("a_value", "").strip(),
            "--b-field",
            request.form.get("b_field", "").strip(),
            "--b-value",
            request.form.get("b_value", "").strip(),
            "--output-geojson",
            request.form.get("output_geojson", "intersections.geojson").strip(),
        ]
        if request.form.get("derive_full_name"):
            argv.append("--derive-full-name")
        group_by = request.form.get("group_by", "").strip()
        if group_by:
            argv += ["--group-by", group_by]
        output_csv = request.form.get("output_csv", "").strip()
        output_kml = request.form.get("output_kml", "").strip()
        if output_csv:
            argv += ["--output-csv", output_csv]
        if output_kml:
            argv += ["--output-kml", output_kml]
        outputs = {"geojson": request.form.get("output_geojson", "intersections.geojson").strip()}
        if output_csv:
            outputs["csv"] = output_csv
        if output_kml:
            outputs["kml"] = output_kml
        return _submit(argv, outputs=outputs)

    @app.get("/roadways/datasets/centerlines-gpkg")
    def roadways_datasets_centerlines_gpkg_form():
        return render_template("roadways_centerlines_gpkg.html")

    @app.post("/roadways/datasets/centerlines-gpkg")
    def roadways_datasets_centerlines_gpkg_submit():
        argv = [
            "-m",
            "roadways",
            "datasets",
            "centerlines-gpkg",
        ]
        input_path = request.form.get("input", "").strip()
        output_path = request.form.get("output", "").strip()
        layer = request.form.get("layer", "centerlines").strip()
        if input_path:
            argv += ["--input", input_path]
        if output_path:
            argv += ["--output", output_path]
        if layer:
            argv += ["--layer", layer]
        if request.form.get("overwrite"):
            argv.append("--overwrite")
        outputs = {"gpkg": output_path or "roadways/data/centerlines.gpkg"}
        return _submit(argv, outputs=outputs)

    @app.get("/jobs")
    def jobs_list():
        jobs = job_store.list_jobs(limit=50)
        return render_template("jobs.html", jobs=jobs)

    @app.get("/jobs/<job_id>")
    def job_detail(job_id: str):
        job = job_store.get_job(job_id)
        if not job:
            abort(404)
        downloads = []
        for key, rel in (job.get("outputs") or {}).items():
            rel = str(rel)
            if rel:
                downloads.append({"key": key, "rel": rel, "url": url_for("download", root="roadways_out", relpath=rel)})
        return render_template("job.html", job=job, downloads=downloads)

    @app.get("/download/<root>/<path:relpath>")
    def download(root: str, relpath: str):
        base = ALLOWED_DOWNLOAD_ROOTS.get(root)
        if not base:
            abort(404)
        safe_base = base.resolve()
        target = (safe_base / relpath).resolve()
        if safe_base not in target.parents and target != safe_base:
            abort(400)
        if not target.exists():
            abort(404)
        return send_from_directory(str(safe_base), str(Path(relpath)))

    return app


if __name__ == "__main__":
    os.environ.setdefault("FLASK_ENV", "development")
    app = create_app()
    app.run(debug=True, use_reloader=False)

