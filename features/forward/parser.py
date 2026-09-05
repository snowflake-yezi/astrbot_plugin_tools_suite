from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ForwardFetcher = Callable[[str], Awaitable[Any]]


@dataclass(frozen=True)
class FlattenedForwardNode:
    content_hash: str
    content: tuple[dict[str, Any], ...]
    sender_id: str
    sender_name: str
    timestamp: int


@dataclass(frozen=True)
class _NodeMetadata:
    sender_id: str = "0"
    sender_name: str = "未知成员"
    timestamp: int = 0


@dataclass(frozen=True)
class ExpandedForwardRecord:
    record_hash: str
    content_hashes: tuple[str, ...]
    legacy_record_hash: str
    legacy_content_hashes: tuple[str, ...]
    nodes: tuple[FlattenedForwardNode, ...]
    max_forward_depth: int
    leaf_count: int
    complete: bool
    errors: tuple[str, ...]


def component_kind(component: Any) -> str:
    if isinstance(component, dict):
        value = component.get("type", "")
    else:
        value = getattr(component, "type", "")
        value = getattr(value, "value", value)
        if not value:
            value = component.__class__.__name__
    normalized = str(value or "").split(".")[-1].strip().lower()
    return normalized


def find_forward_candidates(event: Any) -> list[Any]:
    candidates = [
        component
        for component in (event.get_messages() or [])
        if component_kind(component) in {"forward", "node", "nodes"}
    ]
    if candidates:
        return candidates

    message_obj = getattr(event, "message_obj", None)
    raw_message = getattr(message_obj, "raw_message", None)
    if not isinstance(raw_message, dict):
        return []
    raw_segments = raw_message.get("message")
    if not isinstance(raw_segments, list):
        return []
    return [
        segment
        for segment in raw_segments
        if component_kind(segment) in {"forward", "node", "nodes"}
    ]


class ForwardRecordExpander:
    MAX_FORWARD_DEPTH = 16
    MAX_STRUCTURE_DEPTH = 256
    MAX_LEAF_MESSAGES = 5000
    MAX_COMPONENTS = 10000
    _MEDIA_DIGEST_PATTERN = re.compile(
        r"(?i)(?<![0-9a-f])([0-9a-f]{64}|[0-9a-f]{40}|[0-9a-f]{32})(?![0-9a-f])"
    )

    def __init__(self, fetch_forward: ForwardFetcher):
        self._fetch_forward = fetch_forward
        self._leaf_hashes: list[str] = []
        self._component_hashes: list[str] = []
        self._nodes: list[FlattenedForwardNode] = []
        self._component_count = 0
        self._current_forward_depth = 0
        self._max_forward_depth = 0
        self._active_forward_ids: set[str] = set()
        self._forward_cache: dict[str, Any] = {}
        self._complete = True
        self._errors: list[str] = []

    def _enter_forward_layer(self) -> bool:
        next_depth = self._current_forward_depth + 1
        self._max_forward_depth = max(self._max_forward_depth, next_depth)
        if next_depth > self.MAX_FORWARD_DEPTH:
            self._mark_incomplete("max-forward-depth-exceeded")
            return False
        self._current_forward_depth = next_depth
        return True

    async def expand(self, candidates: list[Any]) -> ExpandedForwardRecord:
        for candidate in candidates:
            if component_kind(candidate) == "node":
                # A top-level Node candidate represents the first forward layer.
                self._current_forward_depth = 1
                self._max_forward_depth = max(self._max_forward_depth, 1)
                try:
                    await self._expand_item(candidate, depth=0)
                finally:
                    self._current_forward_depth = 0
            else:
                await self._expand_item(candidate, depth=0)

        legacy_record_payload = json.dumps(
            self._leaf_hashes,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        record_payload = json.dumps(
            self._component_hashes,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        record_hash = self._hash(record_payload) if self._component_hashes else ""
        legacy_record_hash = (
            self._hash(legacy_record_payload) if self._leaf_hashes else ""
        )
        return ExpandedForwardRecord(
            record_hash=record_hash,
            content_hashes=tuple(self._component_hashes),
            legacy_record_hash=legacy_record_hash,
            legacy_content_hashes=tuple(self._leaf_hashes),
            nodes=tuple(self._nodes),
            max_forward_depth=self._max_forward_depth,
            leaf_count=len(self._leaf_hashes),
            complete=self._complete,
            errors=tuple(self._errors),
        )

    async def _expand_item(self, item: Any, depth: int) -> None:
        if depth > self.MAX_STRUCTURE_DEPTH:
            self._mark_incomplete("max-structure-depth-exceeded")
            return
        if len(self._leaf_hashes) >= self.MAX_LEAF_MESSAGES:
            self._mark_incomplete("max-leaf-messages-exceeded")
            return
        if item is None:
            return
        if isinstance(item, (list, tuple)):
            for child in item:
                await self._expand_item(child, depth + 1)
            return

        kind = component_kind(item)
        if kind == "forward":
            await self._expand_forward(item, depth)
            return
        if kind == "nodes":
            nodes = self._field(item, "nodes") or self._field(item, "messages")
            if not self._enter_forward_layer():
                return
            try:
                await self._expand_item(nodes, depth + 1)
            finally:
                self._current_forward_depth -= 1
            return
        if kind == "node":
            content = self._field(item, "content") or self._field(item, "message")
            await self._expand_content(
                content,
                depth + 1,
                metadata=self._node_metadata(item),
            )
            return

        if isinstance(item, dict):
            data = item.get("data")
            if isinstance(data, dict):
                for key in ("messages", "nodes"):
                    value = data.get(key)
                    if isinstance(value, list):
                        await self._expand_item(value, depth + 1)
                        return
                for key in ("message", "content"):
                    value = data.get(key)
                    if isinstance(value, list):
                        await self._expand_content(
                            value,
                            depth + 1,
                            metadata=self._node_metadata(item),
                        )
                        return

            messages = item.get("messages")
            if isinstance(messages, list):
                await self._expand_item(messages, depth + 1)
                return

            message = item.get("message")
            if isinstance(message, list):
                await self._expand_content(
                    message,
                    depth + 1,
                    metadata=self._node_metadata(item),
                )
                return

            nodes = item.get("nodes")
            if isinstance(nodes, list):
                await self._expand_item(nodes, depth + 1)
                return

            content = item.get("content")
            if isinstance(content, list):
                await self._expand_content(
                    content,
                    depth + 1,
                    metadata=self._node_metadata(item),
                )

    async def _expand_forward(self, item: Any, depth: int) -> None:
        if not self._enter_forward_layer():
            return
        try:
            await self._expand_forward_payload(item, depth)
        finally:
            self._current_forward_depth -= 1

    async def _expand_forward_payload(self, item: Any, depth: int) -> None:
        inline_present, inline_payload = self._present_field(
            item,
            ("content", "messages", "message", "nodes"),
        )
        forward_id = str(
            self._field(item, "id") or self._field(item, "message_id") or ""
        ).strip()

        if inline_present and inline_payload:
            await self._expand_inline_forward(inline_payload, depth + 1)
            return
        if inline_present and not forward_id:
            # NapCat may inline an empty nested record without assigning it an ID.
            # It contributes no content but should not invalidate sibling messages.
            return
        if not forward_id:
            self._mark_incomplete("forward-without-id-or-inline-content")
            return
        if forward_id in self._active_forward_ids:
            self._mark_incomplete(f"forward-cycle:{forward_id}")
            return

        if forward_id not in self._forward_cache:
            try:
                self._forward_cache[forward_id] = await self._fetch_forward(forward_id)
            except Exception as exc:
                self._mark_incomplete(
                    f"forward-fetch-failed:{forward_id}:{type(exc).__name__}"
                )
                return
        payload = self._forward_cache.get(forward_id)
        if payload is None:
            self._mark_incomplete(f"forward-empty-payload:{forward_id}")
            return

        self._active_forward_ids.add(forward_id)
        try:
            await self._expand_item(payload, depth + 1)
        finally:
            self._active_forward_ids.discard(forward_id)

    async def _expand_inline_forward(self, payload: Any, depth: int) -> None:
        if not isinstance(payload, (list, tuple)):
            await self._expand_item(payload, depth)
            return

        contains_message_records = any(
            isinstance(item, dict)
            and not component_kind(item)
            and any(key in item for key in ("messages", "message", "nodes", "content"))
            for item in payload
        )
        if contains_message_records:
            await self._expand_item(payload, depth)
        else:
            await self._expand_content(payload, depth)

    async def _expand_content(
        self,
        content: Any,
        depth: int,
        metadata: _NodeMetadata | None = None,
    ) -> None:
        if not isinstance(content, (list, tuple)):
            if content is None:
                return
            kind = component_kind(content)
            if isinstance(content, str) or kind not in {"", "forward", "node", "nodes"}:
                content = (content,)
            else:
                await self._expand_item(content, depth)
                return

        regular_components: list[str] = []
        resend_components: list[dict[str, Any]] = []

        async def flush_regular_components() -> None:
            if not regular_components:
                return
            if len(self._leaf_hashes) >= self.MAX_LEAF_MESSAGES:
                self._mark_incomplete("max-leaf-messages-exceeded")
                regular_components.clear()
                resend_components.clear()
                return
            canonical = json.dumps(
                regular_components,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            component_hashes = tuple(
                self._hash(component) for component in regular_components
            )
            content_hash = self._hash(canonical)
            node_metadata = metadata or _NodeMetadata()
            self._component_hashes.extend(component_hashes)
            self._append_leaf_hash(content_hash)
            self._nodes.append(
                FlattenedForwardNode(
                    content_hash=content_hash,
                    content=tuple(resend_components),
                    sender_id=node_metadata.sender_id,
                    sender_name=node_metadata.sender_name,
                    timestamp=node_metadata.timestamp,
                )
            )
            regular_components.clear()
            resend_components.clear()

        for component in content:
            if component_kind(component) in {"forward", "node", "nodes"}:
                await flush_regular_components()
                await self._expand_item(component, depth + 1)
                continue
            if self._component_count >= self.MAX_COMPONENTS:
                self._mark_incomplete("max-components-exceeded")
                return
            self._component_count += 1
            resend_component = self._resend_component(component)
            if resend_component is None:
                self._mark_incomplete("component-serialization-failed")
                return
            resend_components.append(resend_component)
            canonical = self._canonical_component(component)
            if canonical:
                regular_components.append(canonical)
        await flush_regular_components()

    def _append_leaf_hash(self, content_hash: str) -> None:
        if len(self._leaf_hashes) >= self.MAX_LEAF_MESSAGES:
            self._mark_incomplete("max-leaf-messages-exceeded")
            return
        self._leaf_hashes.append(content_hash)

    def _mark_incomplete(self, reason: str) -> None:
        self._complete = False
        if reason not in self._errors:
            self._errors.append(reason)

    def _canonical_component(self, component: Any) -> str:
        if isinstance(component, str):
            text = self._normalize_text(component)
            return f"text:{text}" if text else ""

        kind = component_kind(component)
        data = self._component_data(component)

        if kind in {"plain", "text"}:
            text = self._normalize_text(data.get("text") or data.get("plain") or "")
            return f"text:{text}" if text else ""

        if kind == "at":
            return f"at:{str(data.get('qq') or data.get('user_id') or '').strip()}"

        if kind == "face":
            return f"face:{str(data.get('id') or '').strip()}"

        if kind in {
            "image",
            "mface",
            "record",
            "audio",
            "voice",
            "video",
            "file",
        }:
            media_kind = {
                "mface": "image",
                "record": "audio",
                "voice": "audio",
            }.get(kind, kind)
            digest_keys = (
                "md5",
                "md5HexStr",
                "hash",
                "sha1",
                "sha256",
            )
            for key in digest_keys:
                value = data.get(key)
                if value not in (None, ""):
                    normalized = self._normalize_media_value(key, value)
                    return f"{media_kind}:digest:{normalized}"

            media_location_keys = (
                "file",
                "path",
                "sourcePath",
                "local_path",
                "url",
                "URL",
                "name",
                "file_name",
                "fileName",
            )
            for key in media_location_keys:
                value = data.get(key)
                if value in (None, ""):
                    continue
                digest = self._extract_media_digest(value)
                if digest:
                    return f"{media_kind}:digest:{digest}"

            stable_identity_keys = (
                "file_id",
                "fileId",
                "file_uuid",
                "fileUuid",
                "file_unique",
                "image_id",
                "emoji_id",
                "key",
                "uuid",
            )
            for key in stable_identity_keys:
                value = data.get(key)
                if value not in (None, ""):
                    normalized = self._normalize_media_value(key, value)
                    return f"{media_kind}:id:{normalized}"

            for key in media_location_keys:
                value = data.get(key)
                if value not in (None, ""):
                    normalized = self._normalize_media_value(key, value)
                    return f"{media_kind}:location:{normalized}"

            fallback = {
                key: data.get(key)
                for key in ("summary", "sub_type", "file_size", "width", "height")
                if data.get(key) not in (None, "")
            }
            return f"{media_kind}:unidentified:{json.dumps(fallback, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"

        if kind == "reply":
            value = data.get("message_str") or data.get("text") or data.get("id") or ""
            return f"reply:{self._normalize_text(value)}"

        sanitized = self._sanitize_value(data)
        return f"{kind}:{json.dumps(sanitized, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"

    @staticmethod
    def _resend_component(component: Any) -> dict[str, Any] | None:
        if isinstance(component, str):
            return {"type": "text", "data": {"text": component}}
        if isinstance(component, dict):
            return copy.deepcopy(component)

        serializer = getattr(component, "toDict", None)
        if not callable(serializer):
            return None
        try:
            serialized = serializer()
        except Exception:
            return None
        return copy.deepcopy(serialized) if isinstance(serialized, dict) else None

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_text(value: Any) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[^\S\n]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _normalize_media_value(key: str, value: Any) -> str:
        text = str(value).strip()
        if key in {"md5", "md5HexStr", "hash", "sha1", "sha256"}:
            return text.lower()
        if key in {"url", "URL"}:
            try:
                parts = urlsplit(text)
                return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
            except ValueError:
                return text
        if key in {
            "file",
            "path",
            "sourcePath",
            "local_path",
            "name",
            "file_name",
            "fileName",
        }:
            return text.replace("\\", "/").rsplit("/", 1)[-1].lower()
        return text

    @classmethod
    def _extract_media_digest(cls, value: Any) -> str:
        match = cls._MEDIA_DIGEST_PATTERN.search(str(value or ""))
        return match.group(1).lower() if match else ""

    @classmethod
    def _sanitize_value(cls, value: Any, depth: int = 0) -> Any:
        if depth > 8:
            return "<max-depth>"
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, dict):
            return {
                str(key): cls._sanitize_value(item, depth + 1)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
                if str(key) not in {"time", "timestamp", "seq", "real_id"}
            }
        if isinstance(value, (list, tuple)):
            return [cls._sanitize_value(item, depth + 1) for item in value]
        try:
            return cls._sanitize_value(vars(value), depth + 1)
        except TypeError:
            return str(value)

    @staticmethod
    def _component_data(component: Any) -> dict[str, Any]:
        if isinstance(component, dict):
            data = component.get("data")
            if isinstance(data, dict):
                return data
            return {
                str(key): value for key, value in component.items() if key != "type"
            }
        try:
            return {
                str(key): value
                for key, value in vars(component).items()
                if key != "type" and not str(key).startswith("_")
            }
        except TypeError:
            return {"value": str(component)}

    @classmethod
    def _node_metadata(cls, item: Any) -> _NodeMetadata:
        sources: list[dict[str, Any]] = []
        if isinstance(item, dict):
            data = item.get("data")
            if isinstance(data, dict):
                sources.append(data)
            sources.append(item)
            for source in tuple(sources):
                sender = source.get("sender")
                if isinstance(sender, dict):
                    sources.append(sender)
        else:
            try:
                fields = vars(item)
            except TypeError:
                fields = {}
            if isinstance(fields, dict):
                sources.append(fields)
                sender = fields.get("sender")
                if isinstance(sender, dict):
                    sources.append(sender)

        def first_value(keys: tuple[str, ...]) -> Any:
            for source in sources:
                for key in keys:
                    value = source.get(key)
                    if value not in (None, ""):
                        return value
            return None

        sender_id = str(
            first_value(("uin", "user_id", "userId", "sender_id", "qq")) or "0"
        ).strip()
        sender_name = str(
            first_value(("name", "nickname", "sender_name", "card")) or "未知成员"
        ).strip()
        try:
            timestamp = int(first_value(("time", "timestamp")) or 0)
        except (TypeError, ValueError):
            timestamp = 0
        return _NodeMetadata(
            sender_id=sender_id or "0",
            sender_name=sender_name or "未知成员",
            timestamp=max(timestamp, 0),
        )

    @classmethod
    def _field(cls, item: Any, key: str) -> Any:
        if isinstance(item, dict):
            data = item.get("data")
            if isinstance(data, dict) and key in data:
                return data.get(key)
            return item.get(key)
        return getattr(item, key, None)

    @classmethod
    def _present_field(
        cls,
        item: Any,
        keys: tuple[str, ...],
    ) -> tuple[bool, Any]:
        if isinstance(item, dict):
            data = item.get("data")
            if isinstance(data, dict):
                for key in keys:
                    if key in data:
                        return True, data.get(key)
            for key in keys:
                if key in item:
                    return True, item.get(key)
            return False, None
        for key in keys:
            if hasattr(item, key):
                return True, getattr(item, key)
        return False, None
