# -*- coding: utf-8 -*-
"""用户命令注册表：每个命令一个对象（名称/别名/权限/处理器），声明式注册。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class CommandCtx:
    chat_id: str
    args: str
    mask: str | None = None
    is_group: bool = False


@dataclass
class Command:
    name: str
    handler: Callable[[CommandCtx], str | None]
    aliases: list = field(default_factory=list)
    scope: str = "master"        # master=仅大号私聊 | group=仅群聊 | any=两者

    def match(self, text: str) -> str | None:
        """返回命令参数；未命中返回 None。"""
        for prefix in [self.name, *self.aliases]:
            if text == prefix:
                return ""
            for sep in (" ", "：", ":"):
                if text.startswith(prefix + sep):
                    return text[len(prefix) + len(sep):].strip()
        return None


class CommandRegistry:
    def __init__(self):
        self._cmds: list[Command] = []

    def register(self, cmd: Command) -> None:
        self._cmds.append(cmd)

    def try_handle(self, chat_id: str, text: str, *,
                   is_group: bool, mask: str | None = None) -> str | None:
        """命中命令则返回回复文本；未命中返回 None。"""
        text = text.strip()
        for cmd in self._cmds:
            if cmd.scope == "master" and is_group:
                continue
            if cmd.scope == "group" and not is_group:
                continue
            args = cmd.match(text)
            if args is not None:
                return cmd.handler(CommandCtx(
                    chat_id=chat_id, args=args, mask=mask, is_group=is_group))
        return None
