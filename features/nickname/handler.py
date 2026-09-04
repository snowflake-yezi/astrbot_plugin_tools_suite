from __future__ import annotations

from collections.abc import Callable
from typing import Any

from astrbot.api.message_components import At, Plain

from ...core.event import at_user_ids, group_id, message_text, scope_key
from ...core.models import PluginData
from ...core.state import PluginStateStore
from .domain import BindStatus, NicknameBook, format_nickname_list, parse_nickname_command


class NicknameHandler:
    def __init__(
        self,
        state: PluginStateStore,
        save: Callable[[PluginData], None],
    ) -> None:
        self._state = state
        self._save = save

    def set_enabled(self, event: Any, enabled: bool) -> Any:
        if group_id(event) is None:
            return event.plain_result("昵称工具只支持群聊。")

        data = self._state.load()
        scope = self._state.scope(data, scope_key(event))
        scope["nickname_enabled"] = enabled
        self._save(data)
        text = "昵称工具已开启。" if enabled else "昵称工具已关闭，已有昵称数据会保留。"
        return event.plain_result(text)

    def handle(self, event: Any) -> Any | None:
        if group_id(event) is None:
            return None

        data = self._state.load()
        scope = self._state.scope(data, scope_key(event))
        if not scope.get("nickname_enabled", False):
            return None

        book = NicknameBook.from_scope(scope)
        text = message_text(event)
        matched = book.match(text)
        if matched is not None:
            chain: list[Any] = [At(qq=user_id) for user_id in matched.user_ids]
            if matched.message:
                chain.append(Plain(" " + matched.message))
            else:
                # aiocqhttp 会 strip 普通文本，零宽字符确保纯 @ 消息可发送。
                chain.append(Plain("\u200b", convert=False))
            return event.chain_result(chain)

        is_command, nickname = parse_nickname_command(text)
        mentioned_user_ids = at_user_ids(event)
        if not is_command or len(mentioned_user_ids) != 1:
            return None

        target_user_id = mentioned_user_ids[0]
        nicknames = book.nicknames_for(target_user_id)
        if not nickname:
            return event.plain_result(format_nickname_list(nicknames))

        result = book.bind(target_user_id, nickname)
        if result.status == BindStatus.ALREADY_IN_COLLECTION:
            return event.plain_result("该用户已经在昵称集合中。")
        if result.status == BindStatus.ALREADY_BOUND:
            return event.plain_result("该用户已经绑定该昵称。")

        self._save(data)
        if result.collection_size == 1:
            text = f"昵称“{nickname}”已绑定到该用户。"
        elif result.collection_size == 2:
            text = f"昵称“{nickname}”已升级为集合，当前共 {result.collection_size} 人。"
        else:
            text = f"该用户已加入昵称“{nickname}”集合，当前共 {result.collection_size} 人。"
        return event.plain_result(text)
