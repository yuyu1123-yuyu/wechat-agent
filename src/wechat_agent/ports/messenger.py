# -*- coding: utf-8 -*-
"""消息读写接口（微信侧）。

    Sender       —— 发送队列：所有发送（AI 回复/提醒/主动发言）都经它串行化
    ChatSource   —— 只读消息源：会话列表、昵称、历史消息、群消息解析
实现：infra.wechat（gui.SenderQueue / db.DBAdapter）。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from wechat_agent.domain.message import ParsedMsg


@runtime_checkable
class Sender(Protocol):
    def enqueue(self, target: str, text: str, *,
                chat_id: str = "", drop_ok: bool = False) -> None:
        """把一条消息投入发送队列。target 为会话昵称（GUI 按昵称打开会话）。"""
        ...


@runtime_checkable
class ChatSource(Protocol):
    @property
    def self_username(self) -> str: ...

    def sessions(self, limit: int = 300) -> list: ...
    def nickname(self, username: str) -> str: ...
    def messages(self, user: str, limit: int = 20, offset: int = 0) -> list: ...
    def parse_group_msg(self, content: str) -> ParsedMsg: ...
    def resolve_sender_nick(self, sender) -> str: ...
    def is_self_sender(self, msg: dict, parsed_sender) -> bool: ...
    def sender_id_index(self) -> dict: ...
