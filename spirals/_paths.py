from __future__ import annotations

from pathlib import Path


def package_root() -> Path:
    return Path(__file__).resolve().parent


def out_dir() -> Path:
    return package_root() / "out"

