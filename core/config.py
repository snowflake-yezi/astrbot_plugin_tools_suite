from pathlib import Path

PLUGIN_NAME = "tool_suite"
STATE_FILE_NAME = "tool_suite.json"
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
LEGACY_STATE_PATH = PACKAGE_ROOT / "data" / STATE_FILE_NAME
