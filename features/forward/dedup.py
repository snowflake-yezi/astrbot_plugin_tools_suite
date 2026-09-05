from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ...core.models import ForwardRecord, PluginData, ScopeData

FINGERPRINT_VERSION = 3
MAX_STORED_RECORDS = 10000
MAX_HISTORY_BYTES = 16 * 1024 * 1024


class ForwardStatus(StrEnum):
    LATEST = "latest"
    PARTIAL = "partial"
    EXACT = "exact"


@dataclass(frozen=True)
class ForwardMatch:
    status: ForwardStatus
    message_id: str = ""


def _hashes(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if str(value).strip()]


def enforce_forward_history_budget(
    data: PluginData,
    *,
    max_bytes: int = MAX_HISTORY_BYTES,
) -> int:
    scopes = data.get("scopes")
    if not isinstance(scopes, dict):
        return 0

    entries: list[tuple[int, ForwardRecord, int]] = []
    total_size = 0
    for scope in scopes.values():
        if not isinstance(scope, dict):
            continue
        records = scope.get("forward_records")
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            size = (
                len(
                    json.dumps(
                        record,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
                + 1
            )
            try:
                seen_at = int(record.get("seen_at", 0) or 0)
            except (TypeError, ValueError):
                seen_at = 0
            entries.append((seen_at, record, size))
            total_size += size

    removed_ids: set[int] = set()
    for _, record, size in sorted(entries, key=lambda item: item[0]):
        if total_size <= max_bytes:
            break
        removed_ids.add(id(record))
        total_size -= size

    if not removed_ids:
        return 0
    # 预算跨会话共享，避免多个小会话分别撑满单会话上限。
    for scope in scopes.values():
        if not isinstance(scope, dict):
            continue
        records = scope.get("forward_records")
        if isinstance(records, list):
            scope["forward_records"] = [
                record for record in records if id(record) not in removed_ids
            ]
    return len(removed_ids)


def prune_forward_records(
    scope: ScopeData,
    *,
    now: int,
    retention_days: int,
) -> list[ForwardRecord]:
    try:
        fingerprint_version = int(scope.get("forward_fingerprint_version", 0) or 0)
    except (TypeError, ValueError):
        fingerprint_version = 0

    if fingerprint_version not in {2, FINGERPRINT_VERSION}:
        scope["forward_records"] = []
        scope["forward_fingerprint_version"] = FINGERPRINT_VERSION
        return []

    cutoff = now - retention_days * 86400
    raw_records = scope.get("forward_records")
    if not isinstance(raw_records, list):
        raw_records = []

    records: list[ForwardRecord] = []
    for item in raw_records:
        if not isinstance(item, dict):
            continue
        try:
            seen_at = int(item.get("seen_at", 0) or 0)
        except (TypeError, ValueError):
            continue
        record_hash = str(item.get("record_hash") or "").strip()
        content_hashes = item.get("content_hashes")
        leaf_hashes = item.get("leaf_hashes")
        if fingerprint_version == 2 and leaf_hashes is None:
            # v2 仅有组件指纹，仍用于兼容查重。
            leaf_hashes = []
        if (
            seen_at < cutoff
            or not record_hash
            or not isinstance(content_hashes, list)
            or not isinstance(leaf_hashes, list)
        ):
            continue
        records.append(
            {
                "record_hash": record_hash,
                "content_hashes": _hashes(content_hashes),
                "leaf_hashes": _hashes(leaf_hashes),
                "seen_at": seen_at,
                "message_id": str(item.get("message_id") or "").strip(),
            }
        )

    records = records[-MAX_STORED_RECORDS:]
    scope["forward_records"] = records
    scope["forward_fingerprint_version"] = FINGERPRINT_VERSION
    return records


def classify_and_store_forward(
    scope: ScopeData,
    *,
    record_hash: str,
    content_hashes: tuple[str, ...],
    leaf_hashes: tuple[str, ...],
    message_id: str,
    compatible_record_hashes: tuple[str, ...] = (),
    compatible_content_hashes: tuple[str, ...] = (),
    now: int,
    retention_days: int,
) -> ForwardMatch:
    records = prune_forward_records(scope, now=now, retention_days=retention_days)
    current_leaf_hashes = set(leaf_hashes)
    compatible_hashes = set(compatible_content_hashes)
    exact_hashes = {record_hash, *compatible_record_hashes}
    exact_hashes.discard("")
    exact_record: ForwardRecord | None = None
    partial_record: ForwardRecord | None = None

    for record in records:
        stored_content = tuple(record["content_hashes"])
        stored_leaves = set(record["leaf_hashes"])
        if record["record_hash"] in exact_hashes or (
            bool(content_hashes) and stored_content == content_hashes
        ):
            if exact_record is None or record["seen_at"] >= exact_record["seen_at"]:
                exact_record = record
        elif current_leaf_hashes.intersection(stored_leaves) or (
            not stored_leaves and compatible_hashes.intersection(stored_content)
        ):
            if partial_record is None or record["seen_at"] >= partial_record["seen_at"]:
                partial_record = record

    if exact_record is not None:
        # 保留原消息的 ID、时间和叶子分组，不能被即将撤回的重复消息覆盖。
        return ForwardMatch(ForwardStatus.EXACT, exact_record["message_id"])

    records.append(
        {
            "record_hash": record_hash,
            "content_hashes": list(content_hashes),
            "leaf_hashes": list(leaf_hashes),
            "message_id": message_id,
            "seen_at": now,
        }
    )
    scope["forward_records"] = records[-MAX_STORED_RECORDS:]
    if partial_record is not None:
        return ForwardMatch(ForwardStatus.PARTIAL, partial_record["message_id"])
    return ForwardMatch(ForwardStatus.LATEST)
