import json
import tempfile
import unittest
from pathlib import Path

from core.state import PluginStateStore


class PluginStateStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.path = self.root / "plugin_data" / "tool_suite.json"
        self.warnings = []
        self.store = PluginStateStore(self.path, warn=self.warnings.append)

    def test_missing_state_returns_empty_scopes(self):
        self.assertEqual(self.store.load(), {"scopes": {}})

    def test_scope_repairs_invalid_value_and_uses_independent_defaults(self):
        data = {"scopes": {"group:1": []}}

        first = self.store.scope(data, "group:1")
        second = self.store.scope(data, "group:2")
        first["users"]["1"] = ["昵称"]

        self.assertFalse(first["gold_enabled"])
        self.assertEqual(second["users"], {})
        self.assertEqual(data["scopes"]["group:1"], first)

    def test_load_preserves_enhanced_switch_and_active_state(self):
        scope = {
            "forward_enabled": True,
            "forward_enhanced_enabled": True,
            "users": {"1": ["昵称"]},
        }
        self.store.save({"scopes": {"group:1": scope}})

        loaded = self.store.load()
        self.assertEqual(loaded["scopes"]["group:1"], scope)
        self.store.save(loaded)
        self.assertEqual(self.store.load(), loaded)

    def test_save_replaces_file_without_leaving_temporary_file(self):
        data = {"scopes": {"group:1": {"gold_enabled": True}}}

        self.store.save(data)

        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), data)
        self.assertFalse(self.path.with_suffix(".tmp").exists())

    def test_load_migrates_legacy_state_after_successful_save(self):
        legacy_path = self.root / "legacy" / "tool_suite.json"
        legacy_path.parent.mkdir(parents=True)
        legacy_data = {"scopes": {"group:1": {"nickname_enabled": True}}}
        legacy_path.write_text(json.dumps(legacy_data), encoding="utf-8")
        store = PluginStateStore(
            self.path,
            legacy_path=legacy_path,
            warn=self.warnings.append,
        )

        loaded = store.load()

        self.assertEqual(loaded, legacy_data)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), legacy_data)
        self.assertFalse(legacy_path.exists())

    def test_failed_migration_keeps_legacy_state(self):
        legacy_path = self.root / "legacy" / "tool_suite.json"
        legacy_path.parent.mkdir(parents=True)
        legacy_path.write_text('{"scopes": {}}', encoding="utf-8")

        class FailingStore(PluginStateStore):
            def save(self, data):
                raise OSError("disk full")

        store = FailingStore(self.path, legacy_path=legacy_path)

        with self.assertRaises(OSError):
            store.load()
        self.assertTrue(legacy_path.exists())

    def test_invalid_json_is_ignored_and_reported(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("not-json", encoding="utf-8")

        self.assertEqual(self.store.load(), {"scopes": {}})
        self.assertEqual(len(self.warnings), 1)


if __name__ == "__main__":
    unittest.main()
