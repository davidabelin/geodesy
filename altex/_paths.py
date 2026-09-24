from pathlib import Path


def package_root() -> Path:
    return Path(__file__).resolve().parent


def default_source_tif() -> Path:
    return package_root().parent / "qgis" / "Hawkins" / "hawkins10000.tif"


def out_dir() -> Path:
    return package_root() / "out"
