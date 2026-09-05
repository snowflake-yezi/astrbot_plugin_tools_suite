from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.message_components import At, Image, Plain, Reply

from ...core.config import PACKAGE_ROOT
from ...core.event import group_id, message_id, message_text, scope_key
from ...core.models import PluginData, ScopeData
from ...core.state import PluginStateStore
from .dedup import (
    ForwardStatus,
    classify_and_store_forward,
    enforce_forward_history_budget,
    prune_forward_records,
)
from .gateway import OneBotForwardGateway
from .parser import ForwardRecordExpander, find_forward_candidates


class MergedForwardHandler:
    DEFAULT_DEDUP_DAYS = 2
    MAX_DEDUP_DAYS = 365
    SEND_BATCH_SIZE = 100
    PARTIAL_TEXT = "咦，好熟悉的感觉，里面有部分内容已经有人发过一次啦"
    EXACT_TEXT = "咦，这份聊天记录已经有人发过啦，期待你下次带来新鲜的好东西~"

    def __init__(
        self,
        state: PluginStateStore,
        *,
        clock: Callable[[], float] = time.time,
        warn: Callable[[str], None] = logger.warning,
        gateway_factory: Callable[..., Any] = OneBotForwardGateway,
        image_path: Path = PACKAGE_ROOT / "assets" / "duplicate_forward.jpg",
    ) -> None:
        self._state = state
        self._clock = clock
        self._warn = warn
        self._gateway_factory = gateway_factory
        self._image_path = image_path
        self._lock = asyncio.Lock()

    def enforce_history_budget(self) -> int:
        data = self._state.load()
        removed = enforce_forward_history_budget(data)
        if removed:
            self._state.save(data)
        return removed

    def set_enabled(self, event: Any, enabled: bool) -> Any:
        data = self._state.load()
        scope = self._state.scope(data, scope_key(event))
        scope["forward_enabled"] = enabled
        self._save(data)
        if enabled:
            text = (
                "聊天记录展开与查重已开启，当前查重范围为最近 "
                f"{self.retention_days(scope)} 天。"
            )
        else:
            text = "聊天记录展开与查重已关闭，已有查重记录会保留。"
        return event.plain_result(text)

    def set_enhanced(self, event: Any, enabled: bool) -> Any:
        data = self._state.load()
        scope = self._state.scope(data, scope_key(event))
        if enabled:
            scope["forward_enabled"] = True
        scope["forward_enhanced_enabled"] = enabled
        self._save(data)
        text = (
            "聊天记录增强已开启：将平铺重发嵌套记录，查重提醒与重复撤回保持启用。"
            if enabled
            else "聊天记录增强已关闭，查重提醒与重复撤回不受影响。"
        )
        return event.plain_result(text)

    def set_retention_days(self, event: Any) -> Any | None:
        match = re.match(r"^聊天记录查重天数\s*(\d+)\s*$", message_text(event))
        if match is None:
            return None
        days = int(match.group(1))
        if not 1 <= days <= self.MAX_DEDUP_DAYS:
            return event.plain_result(
                f"聊天记录查重天数可设置为 1-{self.MAX_DEDUP_DAYS} 天。"
            )

        data = self._state.load()
        scope = self._state.scope(data, scope_key(event))
        scope["forward_dedup_days"] = days
        prune_forward_records(
            scope,
            now=int(self._clock()),
            retention_days=self.retention_days(scope),
        )
        self._save(data)
        return event.plain_result(f"聊天记录查重范围已调整为最近 {days} 天。")

    def status(self, event: Any) -> Any:
        data = self._state.load()
        scope = self._state.scope(data, scope_key(event))
        records = prune_forward_records(
            scope,
            now=int(self._clock()),
            retention_days=self.retention_days(scope),
        )
        enabled = "开启" if scope.get("forward_enabled", False) else "关闭"
        enhanced = "开启" if scope.get("forward_enhanced_enabled", False) else "关闭"
        return event.plain_result(
            "\n".join(
                [
                    f"聊天记录展开与查重：{enabled}",
                    f"聊天记录增强（平铺重发）：{enhanced}",
                    f"查重范围：最近 {self.retention_days(scope)} 天",
                    f"范围内聊天记录：{len(records)} 份",
                ]
            )
        )

    def retention_days(self, scope: ScopeData) -> int:
        try:
            days = int(scope.get("forward_dedup_days", self.DEFAULT_DEDUP_DAYS))
        except (TypeError, ValueError):
            days = self.DEFAULT_DEDUP_DAYS
        return max(1, min(days, self.MAX_DEDUP_DAYS))

    async def handle(self, event: Any) -> Any | None:
        candidates = find_forward_candidates(event)
        if not candidates:
            return None
        if str(event.get_sender_id()) == str(event.get_self_id() or ""):
            return None

        data = self._state.load()
        scope = self._state.scope(data, scope_key(event))
        if not scope.get("forward_enabled", False):
            return None

        should_call_llm = getattr(event, "should_call_llm", None)
        if callable(should_call_llm):
            should_call_llm(False)

        gateway = self._gateway_factory(
            event, group_id=group_id(event), warn=self._warn
        )
        try:
            expanded = await ForwardRecordExpander(gateway.fetch).expand(candidates)
        except Exception as exc:
            self._warn(f"[tool_suite] forward record recognition failed: {exc}")
            return None
        if not expanded.complete or not expanded.record_hash:
            self._warn(
                "[tool_suite] forward record expansion incomplete: "
                f"leaf_count={expanded.leaf_count}, errors={expanded.errors}"
            )
            return None

        async with self._lock:
            data = self._state.load()
            scope = self._state.scope(data, scope_key(event))
            if not scope.get("forward_enabled", False):
                return None
            match = classify_and_store_forward(
                scope,
                record_hash=expanded.record_hash,
                content_hashes=expanded.content_hashes,
                leaf_hashes=expanded.leaf_hashes,
                message_id=str(message_id(event) or ""),
                compatible_record_hashes=(expanded.legacy_record_hash,),
                compatible_content_hashes=expanded.leaf_hashes,
                now=int(self._clock()),
                retention_days=self.retention_days(scope),
            )
            enhanced = bool(scope.get("forward_enhanced_enabled", False))
            self._save(data)

        if (
            enhanced
            and match.status != ForwardStatus.EXACT
            and expanded.max_forward_depth > 1
        ):
            error = await gateway.send_flattened(
                expanded.nodes, batch_size=self.SEND_BATCH_SIZE
            )
            if error:
                self._warn(f"[tool_suite] flattened forward send failed: {error}")

        if match.status == ForwardStatus.LATEST:
            return None
        if match.status == ForwardStatus.EXACT:
            error = await gateway.recall_original()
            if error:
                self._warn(f"[tool_suite] duplicate forward recall failed: {error}")

        chain: list[Any] = []
        if match.message_id:
            chain.append(Reply(id=match.message_id))
        chain.append(At(qq=str(event.get_sender_id())))
        if match.status == ForwardStatus.PARTIAL:
            return event.chain_result([*chain, Plain(" " + self.PARTIAL_TEXT)])

        if self._image_path.is_file():
            try:
                image = Image.fromFileSystem(str(self._image_path))
                # 在此发送才能捕获文件读取或平台发送失败，并回退为文字提醒。
                await event.send(event.chain_result([*chain, image]))
                return None
            except Exception as exc:
                self._warn(f"[tool_suite] duplicate forward image failed: {exc}")
        return event.chain_result([*chain, Plain(" " + self.EXACT_TEXT)])

    def _save(self, data: PluginData) -> None:
        enforce_forward_history_budget(data)
        self._state.save(data)
