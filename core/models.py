from __future__ import annotations

from typing import Any, TypedDict


class ScopeData(TypedDict, total=False):
    gold_enabled: bool
    nickname_enabled: bool
    forward_enabled: bool
    forward_enhanced_enabled: bool
    forward_dedup_days: int
    forward_fingerprint_version: int
    forward_records: list[dict[str, Any]]
    users: dict[str, list[str]]


class PluginData(TypedDict):
    scopes: dict[str, ScopeData]


def new_scope() -> ScopeData:
    return {
        "gold_enabled": False,
        "nickname_enabled": False,
        "forward_enabled": False,
        "forward_enhanced_enabled": False,
        "forward_dedup_days": 2,
        "forward_records": [],
        "users": {},
    }
