import importlib
import pkgutil
import subprocess
import sys

import pytest

import second_brain

MODULES = sorted(m.name for m in pkgutil.walk_packages(second_brain.__path__, "second_brain."))


@pytest.mark.parametrize("module", MODULES)
def test_module_imports_in_fresh_interpreter(module):
    """Each module must import on its own (catches circular imports)."""
    if module.startswith("second_brain.ui"):
        pytest.importorskip("PySide6.QtWidgets")
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_version():
    assert importlib.import_module("second_brain").__version__
