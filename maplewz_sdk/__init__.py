"""Project-local SDK for Maple WZ parsing and packing.

The SDK vendors the ``wzpy`` runtime that this project depends on, including
local fixes made for GMS 083 round-trip conversion. CLI scripts should call
``ensure_wzpy_importable()`` before importing ``wzpy``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SDK_NAME = "MapleWzConvert SDK"


def ensure_wzpy_importable() -> None:
    """Expose the vendored SDK module as ``wzpy`` for legacy imports.

    The original scripts and the vendored package both use ``wzpy`` imports.
    Registering this alias keeps those imports stable while making the source
    live inside this repository instead of ``.tools/wz-python``.
    """
    if "wzpy" in sys.modules:
        return
    package_dir = Path(__file__).resolve().parent / "wzpy"
    init_file = package_dir / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "wzpy",
        init_file,
        submodule_search_locations=[str(package_dir)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load vendored wzpy from {init_file}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["wzpy"] = module
    spec.loader.exec_module(module)
