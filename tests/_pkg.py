"""Import the plugin package under test regardless of the directory's name."""
from __future__ import annotations

import importlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))
PACKAGE = importlib.import_module(ROOT.name)


def module(name: str):
    """One submodule of the plugin package (its real name is the directory name)."""
    return importlib.import_module(f"{ROOT.name}.{name}")
