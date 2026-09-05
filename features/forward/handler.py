from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from astrbot.api import logger
from astrbot.api.message_components import Image, Plain, Reply

from ...core.config import PACKAGE_ROOT
from ...core.event import group_id, message_text, scope_key
from ...core.models import PluginData, ScopeData
from ...core.state import PluginStateStore
from .dedup import (
    ForwardStatus,
    classify_and_store_forward,
    dedupe_flattened_nodes,
    enforce_forward_history_budget,
    prune_forward_records,
)
from .gateway import OneBotForwardGateway
from .parser import ForwardRecordExpander, find_forward_candidates


class MergedForwardHandler:
    DEFAULT_DEDUP_DAYS = 2
    MAX_DEDUP_DAYS = 365
    SEND_BATCH_SIZE = 100
    REPLY_TEXT: ClassVar[dict[ForwardStatus, str]] = {
        ForwardStatus.LATEST: "咪~让我看看，你又发出来什么好东西",
        ForwardStatus.PARTIAL: "咦，好熟悉的感觉，里面有部分内容已经有人发过一次啦",
        ForwardStatus.EXACT: "该条news已经发送过啦",
    }

    def __init__(
        self,
        state: PluginStateStore,
        *,
        clock: Callable[[], float] = time.time,
        warn: Callable[[str], None] = logger.warning,
        gateway_factory: Callable[..., Any] = OneBotForwardGateway,
        expander_factory: Callable[..., Any] = ForwardRecordExpander,
        image_path: Path = PACKAGE_ROOT / "news.jpg",
    ) -> None:
        self._state = state
        self._clock = clock
        self._warn = warn
        self._gateway_factory = gateway_factory
        self._expander_factory = expander_factory
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
            "聊天记录增强已开启：基础查重已同步开启，将平铺重发新内容并撤回完全重复记录。"
            if enabled
            else "聊天记录增强已关闭，基础展开与查重不受影响。"
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
                    f"聊天记录增强：{enhanced}",
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
        enhanced = bool(scope.get("forward_enhanced_enabled", False))

        should_call_llm = getattr(event, "should_call_llm", None)
        if callable(should_call_llm):
            should_call_llm(False)

        gateway = self._gateway_factory(
            event,
            group_id=group_id(event),
            warn=self._warn,
        )
        expanded = await self._expander_factory(gateway.fetch).expand(candidates)
        if not expanded.complete or not expanded.record_hash:
            self._warn(
                "[tool_suite] forward record expansion incomplete: "
                f"leaf_count={expanded.leaf_count}, errors={expanded.errors}"
            )
            if enhanced:
                return None
            return self._reply_result(event, "聊天记录展开失败，未执行查重。")

        async with self._lock:
            data = self._state.load()
            scope = self._state.scope(data, scope_key(event))
            if not scope.get("forward_enabled", False):
                return None
            decision = classify_and_store_forward(
                scope,
                record_hash=expanded.record_hash,
                content_hashes=expanded.content_hashes,
                leaf_hashes=tuple(node.content_hash for node in expanded.nodes),
                compatible_record_hashes=(expanded.legacy_record_hash,),
                compatible_content_hashes=expanded.legacy_content_hashes,
                now=int(self._clock()),
                retention_days=self.retention_days(scope),
            )
            enhanced = bool(scope.get("forward_enhanced_enabled", False))
            self._save(data)

        if enhanced:
            await self._handle_enhanced(gateway, expanded, decision)
            return None
        if decision.status == ForwardStatus.EXACT:
            image_result = self._image_reply_result(event)
            if image_result is not None:
                return image_result
        return self._reply_result(event, self.REPLY_TEXT[decision.status])

    async def _handle_enhanced(
        self, gateway: Any, expanded: Any, decision: Any
    ) -> None:
        if decision.status == ForwardStatus.EXACT:
            error = await gateway.recall_original()
            if error:
                self._warn(
                    f"[tool_suite] enhanced forward recall did not complete: {error}"
                )
            return
        if expanded.max_forward_depth <= 1:
            return

        nodes = dedupe_flattened_nodes(expanded.nodes)
        error = await gateway.send_flattened(nodes, batch_size=self.SEND_BATCH_SIZE)
        if error:
            self._warn(f"[tool_suite] enhanced forward send did not complete: {error}")

    def _image_reply_result(self, event: Any) -> Any | None:
        if not self._image_path.is_file():
            self._warn(
                f"[tool_suite] exact duplicate image missing: {self._image_path}"
            )
            return None
        try:
            image = Image.fromFileSystem(str(self._image_path))
        except Exception as exc:
            self._warn(
                "[tool_suite] unable to build exact duplicate image component: "
                f"path={self._image_path}, error={exc}"
            )
            return None
        chain: list[Any] = []
        message_id = str(getattr(event.message_obj, "message_id", "") or "").strip()
        if message_id:
            chain.append(Reply(id=message_id))
        chain.append(image)
        return event.chain_result(chain)

    @staticmethod
    def _reply_result(event: Any, text: str) -> Any:
        message_id = str(getattr(event.message_obj, "message_id", "") or "").strip()
        if message_id:
            return event.chain_result([Reply(id=message_id), Plain(text)])
        return event.plain_result(text)

    def _save(self, data: PluginData) -> None:
        enforce_forward_history_budget(data)
        self._state.save(data)
