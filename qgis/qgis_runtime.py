from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path


def find_repo_root(start: Path | None = None) -> Path:
    candidate = (start or Path(__file__).resolve()).resolve()
    if candidate.is_file():
        candidate = candidate.parent

    for path in (candidate, *candidate.parents):
        if (path / ".git").exists():
            return path
        if (path / "qgis").exists() and (path / "requirements.txt").exists():
            return path

    if candidate.name.lower() == "qgis":
        return candidate.parent
    return candidate


def _candidate_proj_dirs(repo_root: Path) -> list[Path]:
    workspace_root = repo_root.parent
    candidates: list[Path] = []

    for env_name in ("PROJ_DATA", "PROJ_LIB"):
        env_value = os.environ.get(env_name)
        if env_value:
            candidates.append(Path(env_value))

    try:
        prefix_path = Path(sys.prefix).resolve()
        candidates.append(prefix_path.parents[1] / "share/proj")
    except Exception:
        pass

    try:
        exe_path = Path(sys.executable).resolve()
        candidates.append(exe_path.parents[1] / "share/proj")
        candidates.append(exe_path.parents[2] / "share/proj")
    except Exception:
        pass

    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        local_root = Path(local_appdata) / "Programs/OSGeo4W"
        candidates.extend(
            [
                local_root / "share/proj",
                local_root / "apps/Qt5/share/proj",
            ]
        )

    candidates.extend(
        [
            repo_root / "geodenv/Lib/site-packages/pyproj/proj_dir/share/proj",
            repo_root / "geodenv/Lib/site-packages/rasterio/proj_data",
            workspace_root / "venv/Lib/site-packages/pyproj/proj_dir/share/proj",
            workspace_root / "venv/Lib/site-packages/rasterio/proj_data",
        ]
    )

    unique_candidates: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        unique_candidates.append(candidate)

    return unique_candidates


def configure_proj_env(anchor: Path | None = None) -> Path | None:
    repo_root = find_repo_root(anchor)

    for candidate in _candidate_proj_dirs(repo_root):
        proj_db = candidate / "proj.db"
        if not proj_db.exists():
            continue

        try:
            with sqlite3.connect(proj_db) as connection:
                metadata = dict(
                    connection.execute(
                        "select key, value from metadata "
                        "where key like 'DATABASE.LAYOUT.VERSION.%'"
                    ).fetchall()
                )
            minor = int(metadata.get("DATABASE.LAYOUT.VERSION.MINOR", "0"))
        except Exception:
            continue

        if minor < 4:
            continue

        os.environ["PROJ_DATA"] = str(candidate)
        os.environ["PROJ_LIB"] = str(candidate)
        return candidate

    return None


def _candidate_qgis_prefixes() -> list[Path]:
    candidates: list[Path] = []

    prefix = os.environ.get("QGIS_PREFIX_PATH")
    if prefix:
        candidates.append(Path(prefix))

    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        osgeo_root = Path(local_appdata) / "Programs/OSGeo4W/apps"
        candidates.extend(
            [
                osgeo_root / "qgis-ltr",
                osgeo_root / "qgis",
                osgeo_root / "qgis-dev",
            ]
        )

    unique_candidates: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        unique_candidates.append(candidate)

    return unique_candidates


def ensure_qgis_prefix_path() -> Path | None:
    for candidate in _candidate_qgis_prefixes():
        if not (candidate / "python").exists():
            continue
        os.environ.setdefault("QGIS_PREFIX_PATH", candidate.as_posix())
        return candidate
    return None


def add_qgis_python_paths() -> list[Path]:
    prefix = ensure_qgis_prefix_path()
    candidates: list[Path] = []

    if prefix is not None:
        candidates.extend(
            [
                prefix / "python",
                prefix / "python/plugins",
            ]
        )

    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        osgeo_root = Path(local_appdata) / "Programs/OSGeo4W/apps/qgis-ltr"
        candidates.extend(
            [
                osgeo_root / "python",
                osgeo_root / "python/plugins",
            ]
        )

    added: list[Path] = []
    for candidate in candidates:
        if not candidate.exists():
            continue
        candidate_str = str(candidate)
        if candidate_str in sys.path:
            continue
        sys.path.append(candidate_str)
        added.append(candidate)

    return added


def init_qgis_app(gui: bool = False):
    configure_proj_env()
    add_qgis_python_paths()

    from qgis.core import QgsApplication

    app = QgsApplication.instance()
    created = False
    if app is None:
        prefix = ensure_qgis_prefix_path()
        if prefix is not None:
            QgsApplication.setPrefixPath(str(prefix), True)
        app = QgsApplication([], gui)
        app.initQgis()
        created = True

    return app, created


def shutdown_qgis_app(app, created: bool) -> None:
    if created and app is not None:
        app.exitQgis()
