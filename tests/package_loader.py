import importlib
import sys
import types
from pathlib import Path


PACKAGE_NAME = "astrbot_plugin_tool_suite"
PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def load_module(name):
    if PACKAGE_NAME not in sys.modules:
        package = types.ModuleType(PACKAGE_NAME)
        package.__path__ = [str(PACKAGE_ROOT)]
        sys.modules[PACKAGE_NAME] = package
    return importlib.import_module(f"{PACKAGE_NAME}.{name}")
