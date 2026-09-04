from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import STATE_FILE_NAME
from .models import PluginData, ScopeData, new_scope


class PluginStateStore:
    """读写插件共享状态，并将旧版插件内数据迁移到 AstrBot 数据目录。"""

    def __init__(
        self,
        path: Path,
        *,
        legacy_path: Path | None = None,
        warn: Callable[[str], None] = lambda _: None,
    ) -> None:
        self.path = path
        self._legacy_path = legacy_path
        self._warn = warn

    @classmethod
    def for_plugin(
        cls,
        plugin_name: str,
        *,
        legacy_path: Path | None = None,
        warn: Callable[[str], None] = lambda _: None,
    ) -> PluginStateStore:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        path = (
            Path(get_astrbot_data_path())
            / "plugin_data"
            / plugin_name
            / STATE_FILE_NAME
        )
        return cls(path, legacy_path=legacy_path, warn=warn)

    def load(self) -> PluginData:
        source = self.path
        migrating = not source.exists() and self._legacy_path is not None
        if migrating:
            source = self._legacy_path
        if not source.exists():
            return {"scopes": {}}

        data = self._read(source)
        if data is None:
            return {"scopes": {}}
        if migrating:
            self.save(data)
            try:
                source.unlink()
            except OSError as exc:
                self._warn(f"[tool_suite] legacy data cleanup failed: {exc}")
        return data

    def save(self, data: PluginData) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(self.path)

    def scope(self, data: PluginData, scope_key: str) -> ScopeData:
        scopes = data.setdefault("scopes", {})
        scope = scopes.get(scope_key)
        if not isinstance(scope, dict):
            scope = new_scope()
            scopes[scope_key] = scope
            return scope

        defaults = new_scope()
        for key, value in defaults.items():
            scope.setdefault(key, value)
        return scope

    def _read(self, path: Path) -> PluginData | None:
        try:
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self._warn(f"[tool_suite] load data failed: {exc}")
            return None
        if not isinstance(raw, dict):
            return None
        if not isinstance(raw.get("scopes"), dict):
            raw["scopes"] = {}
        return raw
