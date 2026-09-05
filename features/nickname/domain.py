from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


@dataclass(frozen=True)
class NicknameMatch:
    nickname: str
    message: str
    user_ids: tuple[str, ...]


class BindStatus(StrEnum):
    ADDED = "added"
    ALREADY_BOUND = "already-bound"
    ALREADY_IN_COLLECTION = "already-in-collection"


@dataclass(frozen=True)
class BindResult:
    status: BindStatus
    collection_size: int


class NicknameBook:
    def __init__(self, users: dict[str, list[str]]) -> None:
        self.users = users

    @classmethod
    def from_scope(cls, scope: dict[str, Any]) -> NicknameBook:
        users = scope.get("users")
        if not isinstance(users, dict):
            users = {}
            scope["users"] = users
        return cls(users)

    def nicknames_for(self, user_id: str) -> list[str]:
        nicknames = self.users.get(user_id)
        if not isinstance(nicknames, list):
            return []
        return [str(item) for item in nicknames if str(item).strip()]

    def users_for(self, nickname: str) -> list[str]:
        return [
            str(user_id)
            for user_id in self.users
            if nickname in self.nicknames_for(str(user_id))
        ]

    def match(self, text: str) -> NicknameMatch | None:
        if not text.startswith("at"):
            return None
        payload = text[2:].strip()
        if not payload:
            return None

        nicknames = {
            nickname
            for user_id in self.users
            for nickname in self.nicknames_for(str(user_id))
        }
        for nickname in sorted(nicknames, key=len, reverse=True):
            if not payload.startswith(nickname):
                continue
            user_ids = self.users_for(nickname)
            if user_ids:
                return NicknameMatch(
                    nickname=nickname,
                    message=payload[len(nickname) :].strip(),
                    user_ids=tuple(user_ids),
                )
        return None

    def bind(self, user_id: str, nickname: str) -> BindResult:
        nicknames = self.nicknames_for(user_id)
        bound_user_ids = self.users_for(nickname)
        if nickname in nicknames:
            status = (
                BindStatus.ALREADY_IN_COLLECTION
                if len(bound_user_ids) > 1
                else BindStatus.ALREADY_BOUND
            )
            return BindResult(status, len(bound_user_ids))

        nicknames.append(nickname)
        self.users[user_id] = nicknames
        return BindResult(BindStatus.ADDED, len(bound_user_ids) + 1)


def parse_nickname_command(text: str) -> tuple[bool, str]:
    match = re.match(r"^/?昵称(?:\s+(.+))?$", text.strip())
    if not match:
        return False, ""
    return True, (match.group(1) or "").strip()


def format_nickname_list(nicknames: list[str]) -> str:
    if not nicknames:
        return "该用户还没有绑定昵称。"
    return "该用户的昵称: " + ", ".join(nicknames)
