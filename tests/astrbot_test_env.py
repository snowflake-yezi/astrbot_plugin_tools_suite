import os
from pathlib import Path

RUNTIME_ROOT = Path(__file__).resolve().parents[1] / ".venv" / "astrbot-test-runtime"
os.environ.setdefault("ASTRBOT_ROOT", str(RUNTIME_ROOT))
